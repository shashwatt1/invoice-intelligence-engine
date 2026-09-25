"""
tests/test_product_master_proposals.py — LOCAL OFFLINE TESTS for the USER
proposal path added for the live review dashboard.

The property that matters: a USER can say what they think the multiplier
should be, and cannot make it true. A proposal never becomes an
authoritative mapping on its own — it moves the candidate to PENDING and
waits for a MANAGER or ADMIN to run the same approval service that already
governs every authoritative write.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from app.core.exceptions import ValidationError
from app.models.product_master import (
    COMMERCIAL_CONFLICT,
    COMMERCIAL_UNIT_IS_SELLING_UNIT,
    DECISION_PROPOSE,
    STATE_APPROVED,
    STATE_PENDING,
    STATE_REJECTED,
    STATE_REVIEW_REQUIRED,
)
from app.services import master_commercial_review_service as service

API = Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "product_master.py"


class FakeMapping:
    def __init__(self, *, basis=COMMERCIAL_UNIT_IS_SELLING_UNIT, units=4,
                 state=STATE_REVIEW_REQUIRED):
        self.id = uuid.uuid4()
        self.commercial_unit_basis = basis
        self.units_accounted_for = units
        self.approval_state = state
        self.cost_basis = "DISTRIBUTOR_CASE_PRICE"
        self.reviewed_by = self.reviewed_at = None
        self.proposed_units_accounted_for = self.proposed_by = None
        self.proposed_at = self.proposed_note = None
        self.evidence = {"notes": "Sheet1 items/case column"}


class FakeRepository:
    def __init__(self, mapping):
        self._mapping = mapping
        self.reviews = []

    async def get(self, mapping_id):
        return (self._mapping, None, None) if mapping_id == self._mapping.id else None

    async def add_review(self, review):
        self.reviews.append(review)
        return review


@pytest.fixture
def patched(monkeypatch):
    def bind(mapping):
        repository = FakeRepository(mapping)
        monkeypatch.setattr(service, "MasterCommercialRepository", lambda _s: repository)
        return repository
    return bind


class TestProposalIsNotAuthoritative:
    async def test_a_proposal_moves_the_candidate_to_pending(self, patched):
        mapping = FakeMapping()
        repository = patched(mapping)
        outcome = await service.propose(
            None, mapping.id, units_accounted_for=12, proposer="vivek", note="case of 12",
        )
        assert outcome.new_state == STATE_PENDING
        assert mapping.approval_state == STATE_PENDING
        assert repository.reviews[0].decision == DECISION_PROPOSE

    async def test_a_proposal_does_not_overwrite_the_derived_value(self, patched):
        mapping = FakeMapping(units=4)
        patched(mapping)
        await service.propose(None, mapping.id, units_accounted_for=12, proposer="vivek")
        # The evidence's own number is untouched; the proposal sits beside it.
        assert mapping.units_accounted_for == 4
        assert mapping.proposed_units_accounted_for == 12
        assert mapping.proposed_by == "vivek"

    async def test_a_proposal_does_not_reinterpret_the_evidence(self, patched):
        mapping = FakeMapping(basis=COMMERCIAL_CONFLICT, units=None)
        patched(mapping)
        await service.propose(None, mapping.id, units_accounted_for=18, proposer="vivek")
        assert mapping.commercial_unit_basis == COMMERCIAL_CONFLICT, "still a conflict"

    async def test_a_decided_candidate_cannot_be_reopened_by_a_proposal(self, patched):
        for state in (STATE_APPROVED, STATE_REJECTED):
            mapping = FakeMapping(state=state)
            patched(mapping)
            with pytest.raises(ValidationError):
                await service.propose(
                    None, mapping.id, units_accounted_for=12, proposer="vivek")

    @pytest.mark.parametrize("units", [0, -1, 10000])
    async def test_an_impossible_multiplier_is_refused(self, patched, units):
        mapping = FakeMapping()
        patched(mapping)
        with pytest.raises(ValidationError):
            await service.propose(
                None, mapping.id, units_accounted_for=units, proposer="vivek")

    async def test_the_proposal_is_recorded_with_its_author_and_evidence(self, patched):
        mapping = FakeMapping()
        repository = patched(mapping)
        await service.propose(
            None, mapping.id, units_accounted_for=12, proposer="vivek", note="from the sheet")
        entry = repository.reviews[0]
        assert entry.reviewer == "vivek"
        assert entry.note == "from the sheet"
        assert entry.evidence_considered["notes"] == "Sheet1 items/case column"


class TestManagerApprovesTheProposal:
    async def test_approving_a_pending_candidate_promotes_the_proposed_value(self, patched):
        mapping = FakeMapping()
        repository = patched(mapping)
        await service.propose(None, mapping.id, units_accounted_for=12, proposer="vivek")
        outcome = await service.approve(None, mapping.id, reviewer="barj")

        assert outcome.new_state == STATE_APPROVED
        assert mapping.units_accounted_for == 12, "the proposal became authoritative"
        assert mapping.commercial_unit_basis == COMMERCIAL_UNIT_IS_SELLING_UNIT
        assert mapping.reviewed_by == "barj"
        # Both the proposal and the decision are in the history.
        assert [r.decision for r in repository.reviews] == ["PROPOSE", "APPROVE"]

    async def test_a_manager_may_override_the_proposed_value(self, patched):
        mapping = FakeMapping()
        patched(mapping)
        await service.propose(None, mapping.id, units_accounted_for=12, proposer="vivek")
        await service.approve(
            None, mapping.id, reviewer="barj",
            commercial_unit_basis="CASE_IS_SELLING_UNIT",
        )
        assert mapping.units_accounted_for == 1

    async def test_rejecting_a_proposal_creates_no_authoritative_mapping(self, patched):
        mapping = FakeMapping()
        patched(mapping)
        await service.propose(None, mapping.id, units_accounted_for=12, proposer="vivek")
        await service.reject(None, mapping.id, reviewer="barj", note="not supported")
        assert mapping.approval_state == STATE_REJECTED
        assert mapping.units_accounted_for == 4, "the derived value is untouched"
        assert mapping.proposed_units_accounted_for == 12, "evidence of the proposal is kept"

    async def test_a_pending_candidate_reports_as_pending(self, patched):
        mapping = FakeMapping()
        patched(mapping)
        await service.propose(None, mapping.id, units_accounted_for=12, proposer="vivek")
        assert service.review_status(mapping) == service.REVIEW_PENDING


class TestRouteAuthorization:
    def test_proposing_is_open_to_any_authenticated_account(self):
        body = re.search(r"async def propose_candidate\((.*?)\) ->", API.read_text(), re.S)
        assert body and "require_authenticated_user" in body.group(1)
        assert "require_manager" not in body.group(1)

    @pytest.mark.parametrize("handler", ["approve_candidate", "reject_candidate"])
    def test_deciding_still_requires_manager(self, handler):
        body = re.search(rf"async def {handler}\((.*?)\) ->", API.read_text(), re.S)
        assert body and "require_manager" in body.group(1)

    def test_the_proposal_route_uses_the_existing_review_service(self):
        source = API.read_text()
        assert "review_service.propose(" in source
        # No second mapping-mutation path was introduced.
        assert "MasterCommercialMapping(" not in source


class TestLiveDeploymentSafety:
    def test_the_app_never_imports_the_scripts_package_at_runtime(self):
        """
        `scripts/` is developer tooling and is not guaranteed to be
        importable in the container; `data/reference/` is gitignored source
        evidence and is not shipped at all. Matched on import statements and
        path literals in code — prose mentioning either in a docstring is
        fine and is what the `#` / quote checks below skip.
        """
        import ast

        root = Path(__file__).resolve().parent.parent / "app"
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("scripts"), path
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("scripts"), path
                # A path literal used in code (not a docstring) would read
                # the workbooks at runtime.
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and "data/reference" in node.value and len(node.value) < 120):
                    raise AssertionError(f"{path} uses a reference-data path literal")

    def test_the_identity_queue_is_served_from_the_database(self):
        source = (Path(__file__).resolve().parent.parent
                  / "app/services/product_master/identity_queue.py").read_text()
        assert "AsyncSession" in source
        assert "read_workbook" not in source and "load_raw_records" not in source
