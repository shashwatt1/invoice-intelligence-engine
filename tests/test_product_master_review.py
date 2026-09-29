"""
tests/test_product_master_review.py — LOCAL OFFLINE TESTS for the Phase 2D
commercial review workflow.

The governance properties defended here:

  * a CONFLICT candidate cannot be approved without a reviewer explicitly
    supplying the interpretation the evidence could not;
  * cost status and commercial-unit status are independent — a candidate
    whose sources disagreed on cost may still be approved;
  * rejection keeps the row, it does not delete it;
  * every decision appends history carrying the values held before it;
  * approving master data changes nothing about EDI, and USER cannot reach
    the workflow at all.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.dependencies import require_authenticated_user
from app.core.exceptions import ValidationError
from app.models.product_master import (
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    COMMERCIAL_CONFLICT,
    COMMERCIAL_UNIT_IS_SELLING_UNIT,
    COMMERCIAL_UNKNOWN,
    COST_CONFLICTING_SOURCES,
    COST_DISTRIBUTOR_CASE_PRICE,
    DECISION_APPROVE,
    DECISION_REJECT,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_REVIEW_REQUIRED,
)
from app.models.user import User, UserRole
from app.services import master_commercial_review_service as service


class FakeMapping:
    """Stands in for a seeded candidate without a database."""

    def __init__(self, *, basis=COMMERCIAL_UNIT_IS_SELLING_UNIT, units=4,
                 state=STATE_REVIEW_REQUIRED, cost_basis=COST_DISTRIBUTOR_CASE_PRICE):
        self.id = uuid.uuid4()
        self.commercial_unit_basis = basis
        self.units_accounted_for = units
        self.approval_state = state
        self.cost_basis = cost_basis
        self.reviewed_by = None
        self.reviewed_at = None
        self.evidence = {"notes": "Sheet1 items/case column", "governed_units_observed": []}


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
    """Bind the service to a fake repository — no session, no database."""
    def bind(mapping):
        repository = FakeRepository(mapping)
        monkeypatch.setattr(service, "MasterCommercialRepository", lambda _s: repository)
        return repository
    return bind


class TestConflictCannotBeApprovedWithoutResolution:
    async def test_a_conflict_refuses_bare_approval(self, patched):
        mapping = FakeMapping(basis=COMMERCIAL_CONFLICT, units=None)
        patched(mapping)
        with pytest.raises(ValidationError) as raised:
            await service.approve(None, mapping.id, reviewer="manager", note="Reviewed against the source evidence.")
        assert raised.value.detail["reason"] == "resolution_required"
        assert mapping.approval_state == STATE_REVIEW_REQUIRED, "nothing changed"

    async def test_unknown_also_refuses_bare_approval(self, patched):
        mapping = FakeMapping(basis=COMMERCIAL_UNKNOWN, units=None)
        patched(mapping)
        with pytest.raises(ValidationError):
            await service.approve(None, mapping.id, reviewer="manager", note="Reviewed against the source evidence.")

    async def test_a_reviewer_may_resolve_a_conflict_explicitly(self, patched):
        mapping = FakeMapping(basis=COMMERCIAL_CONFLICT, units=None)
        repository = patched(mapping)
        outcome = await service.approve(
            None, mapping.id, reviewer="manager",
            commercial_unit_basis=COMMERCIAL_CASE_IS_SELLING_UNIT,
            note="Distributor items/case column says the case is one sellable unit.",
        )
        assert outcome.new_state == STATE_APPROVED
        assert mapping.units_accounted_for == 1
        assert mapping.commercial_unit_basis == COMMERCIAL_CASE_IS_SELLING_UNIT
        # The history keeps what it used to be.
        assert repository.reviews[0].previous_commercial_unit_basis == COMMERCIAL_CONFLICT
        assert repository.reviews[0].previous_units_accounted_for is None

    async def test_resolving_to_a_contained_unit_requires_a_number(self, patched):
        mapping = FakeMapping(basis=COMMERCIAL_CONFLICT, units=None)
        patched(mapping)
        with pytest.raises(ValidationError) as raised:
            await service.approve(
                None, mapping.id, reviewer="manager",
                commercial_unit_basis=COMMERCIAL_UNIT_IS_SELLING_UNIT, note="Reviewed against the source evidence.",
            )
        assert raised.value.detail["field"] == "units_accounted_for"

    @pytest.mark.parametrize("basis", [COMMERCIAL_CONFLICT, COMMERCIAL_UNKNOWN, "NONSENSE"])
    async def test_a_non_decision_cannot_be_recorded_as_the_resolution(self, patched, basis):
        mapping = FakeMapping(basis=COMMERCIAL_CONFLICT, units=None)
        patched(mapping)
        with pytest.raises(ValidationError):
            await service.approve(None, mapping.id, reviewer="m",
                                  commercial_unit_basis=basis, units_accounted_for=4, note="Reviewed against the source evidence.")

    async def test_case_is_selling_unit_cannot_carry_a_larger_multiplier(self, patched):
        mapping = FakeMapping(basis=COMMERCIAL_CONFLICT, units=None)
        patched(mapping)
        with pytest.raises(ValidationError) as raised:
            await service.approve(
                None, mapping.id, reviewer="m",
                commercial_unit_basis=COMMERCIAL_CASE_IS_SELLING_UNIT,
                units_accounted_for=18, note="Reviewed against the source evidence.",
            )
        assert raised.value.detail["expected"] == 1

    async def test_a_multiplier_of_one_must_use_the_case_basis(self, patched):
        mapping = FakeMapping(basis=COMMERCIAL_CONFLICT, units=None)
        patched(mapping)
        with pytest.raises(ValidationError):
            await service.approve(
                None, mapping.id, reviewer="m",
                commercial_unit_basis=COMMERCIAL_UNIT_IS_SELLING_UNIT,
                units_accounted_for=1, note="Reviewed against the source evidence.",
            )


class TestApproval:
    async def test_a_settled_candidate_approves_without_resolution(self, patched):
        mapping = FakeMapping()
        repository = patched(mapping)
        outcome = await service.approve(None, mapping.id, reviewer="manager", note="Looks right")
        assert outcome.new_state == STATE_APPROVED
        assert mapping.reviewed_by == "manager"
        assert mapping.reviewed_at is not None
        assert repository.reviews[0].decision == DECISION_APPROVE

    async def test_approval_is_idempotent(self, patched):
        mapping = FakeMapping()
        repository = patched(mapping)
        await service.approve(None, mapping.id, reviewer="a", note="Reviewed against the source evidence.")
        outcome = await service.approve(None, mapping.id, reviewer="b")
        assert outcome.new_state == STATE_APPROVED
        assert len(repository.reviews) == 1, "a double submit must not rewrite the decision"
        assert mapping.reviewed_by == "a"

    async def test_a_rejected_candidate_cannot_be_approved_silently(self, patched):
        mapping = FakeMapping(state=STATE_REJECTED)
        patched(mapping)
        with pytest.raises(ValidationError):
            await service.approve(None, mapping.id, reviewer="manager", note="Reviewed against the source evidence.")

    async def test_history_records_the_reviewer_note_and_evidence(self, patched):
        mapping = FakeMapping()
        repository = patched(mapping)
        await service.approve(None, mapping.id, reviewer="manager", note="checked Sheet1")
        entry = repository.reviews[0]
        assert entry.reviewer == "manager"
        assert entry.note == "checked Sheet1"
        assert entry.evidence_considered["notes"] == "Sheet1 items/case column"


class TestRejection:
    async def test_rejection_keeps_the_row_and_records_why(self, patched):
        mapping = FakeMapping()
        repository = patched(mapping)
        outcome = await service.reject(
            None, mapping.id, reviewer="manager", note="Source sheet is out of date",
        )
        assert outcome.new_state == STATE_REJECTED
        assert mapping.approval_state == STATE_REJECTED
        assert repository.reviews[0].decision == DECISION_REJECT
        assert repository.reviews[0].note == "Source sheet is out of date"

    async def test_rejection_does_not_erase_the_candidate_values(self, patched):
        mapping = FakeMapping(units=4)
        patched(mapping)
        await service.reject(None, mapping.id, reviewer="manager", note="Source sheet is out of date")
        assert mapping.units_accounted_for == 4, "evidence is preserved, not blanked"

    async def test_rejection_is_idempotent(self, patched):
        mapping = FakeMapping()
        repository = patched(mapping)
        await service.reject(None, mapping.id, reviewer="a", note="Source sheet is out of date")
        await service.reject(None, mapping.id, reviewer="b")
        assert len(repository.reviews) == 1


class TestDecisionAccountability:
    """Every APPROVE/REJECT says what it rests on and who, by account and role, made it."""

    @pytest.mark.parametrize("note", [None, "", "   "])
    async def test_an_approval_without_a_decision_basis_is_refused_and_changes_nothing(self, patched, note):
        mapping = FakeMapping()
        repository = patched(mapping)
        with pytest.raises(ValidationError) as raised:
            await service.approve(None, mapping.id, reviewer="manager", note=note)
        assert raised.value.detail == {"field": "note", "reason": "required", "decision": DECISION_APPROVE}
        assert mapping.approval_state == STATE_REVIEW_REQUIRED and mapping.reviewed_by is None
        assert repository.reviews == []

    @pytest.mark.parametrize("note", [None, "", "   "])
    async def test_a_rejection_without_a_reason_is_refused_and_changes_nothing(self, patched, note):
        mapping = FakeMapping()
        repository = patched(mapping)
        with pytest.raises(ValidationError) as raised:
            await service.reject(None, mapping.id, reviewer="manager", note=note)
        assert raised.value.detail["decision"] == DECISION_REJECT
        assert mapping.approval_state == STATE_REVIEW_REQUIRED and repository.reviews == []

    async def test_the_basis_is_recorded_as_written_without_surrounding_space(self, patched):
        mapping = FakeMapping()
        repository = patched(mapping)
        await service.approve(None, mapping.id, reviewer="manager", note="  Sheet1 items/case says 4.  ")
        assert repository.reviews[0].note == "Sheet1 items/case says 4."

    async def test_approve_reject_and_propose_record_the_account_and_its_role(self, patched):
        account = uuid.uuid4()
        approved, rejected, proposed = FakeMapping(), FakeMapping(), FakeMapping()
        repository = patched(approved)
        await service.approve(None, approved.id, reviewer="barj", note="Sheet1 says 4.",
                              reviewer_user_id=account, reviewer_role="MANAGER")
        assert (repository.reviews[0].reviewer, repository.reviews[0].reviewer_user_id,
                repository.reviews[0].reviewer_role) == ("barj", account, "MANAGER")
        repository = patched(rejected)
        await service.reject(None, rejected.id, reviewer="shashwatt1", note="Stale sheet.",
                             reviewer_user_id=account, reviewer_role="ADMIN")
        assert (repository.reviews[0].reviewer_user_id, repository.reviews[0].reviewer_role) == (account, "ADMIN")
        repository = patched(proposed)
        await service.propose(None, proposed.id, units_accounted_for=6, proposer="vivek",
                              proposer_user_id=account, proposer_role="USER")
        assert (repository.reviews[0].reviewer_user_id, repository.reviews[0].reviewer_role) == (account, "USER")


class TestTheSessionDecides:
    """Who decided is the authenticated account — never a value in the request."""

    @pytest.fixture
    def session_as(self, app, patched):
        from app.database.session import get_db

        class _Session:
            commits = 0

            async def commit(self):
                _Session.commits += 1

        async def fake_db():
            yield _Session()

        app.dependency_overrides[get_db] = fake_db

        def bind(role, mapping):
            account = User(id=uuid.uuid4(), username=f"{role.lower()}-account", password_hash="x",
                           role=role, is_active=True)
            app.dependency_overrides[require_authenticated_user] = lambda: account
            return account, patched(mapping)
        yield bind
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_authenticated_user, None)

    @pytest.mark.parametrize("role", [UserRole.MANAGER.value, UserRole.ADMIN.value])
    @pytest.mark.parametrize("action", ["approve", "reject"])
    async def test_the_session_account_is_recorded_whatever_the_request_claims(
        self, client, session_as, role, action,
    ):
        mapping = FakeMapping()
        account, repository = session_as(role, mapping)
        response = await client.post(
            f"/api/v1/product-master/commercial/{mapping.id}/{action}",
            json={"note": "Distributor sheet states 4 per case.",
                  "reviewer": "someone-else", "reviewer_user_id": str(uuid.uuid4()), "reviewer_role": "ADMIN"},
        )
        assert response.status_code == 200, response.text
        entry = repository.reviews[0]
        assert (entry.reviewer, entry.reviewer_user_id, entry.reviewer_role) == (
            account.username, account.id, role)
        assert entry.note == "Distributor sheet states 4 per case."

    @pytest.mark.parametrize("action", ["approve", "reject"])
    async def test_a_decision_without_a_basis_is_refused_over_the_api(self, client, session_as, action):
        mapping = FakeMapping()
        _, repository = session_as(UserRole.MANAGER.value, mapping)
        response = await client.post(f"/api/v1/product-master/commercial/{mapping.id}/{action}", json={})
        assert response.status_code == 422
        assert response.json()["error"]["detail"]["field"] == "note"
        assert repository.reviews == [] and mapping.approval_state == STATE_REVIEW_REQUIRED


class TestCostIsIndependent:
    async def test_a_candidate_with_conflicting_costs_can_still_be_approved(self, patched):
        # Cost status must not gate the commercial-unit decision.
        mapping = FakeMapping(cost_basis=COST_CONFLICTING_SOURCES)
        patched(mapping)
        outcome = await service.approve(None, mapping.id, reviewer="manager", note="Reviewed against the source evidence.")
        assert outcome.new_state == STATE_APPROVED
        assert mapping.cost_basis == COST_CONFLICTING_SOURCES, "cost status is untouched"

    async def test_approving_never_invents_a_cost(self, patched):
        mapping = FakeMapping(cost_basis=None)
        patched(mapping)
        await service.approve(None, mapping.id, reviewer="manager", note="Reviewed against the source evidence.")
        assert mapping.cost_basis is None


class TestRequiresResolutionHelper:
    @pytest.mark.parametrize(("basis", "units", "expected"), [
        (COMMERCIAL_CONFLICT, None, True),
        (COMMERCIAL_UNKNOWN, None, True),
        (COMMERCIAL_UNIT_IS_SELLING_UNIT, None, True),
        (COMMERCIAL_UNIT_IS_SELLING_UNIT, 4, False),
        (COMMERCIAL_CASE_IS_SELLING_UNIT, 1, False),
    ])
    def test_a_candidate_without_a_multiplier_always_needs_a_person(
        self, basis, units, expected,
    ):
        assert service.requires_resolution(FakeMapping(basis=basis, units=units)) is expected


class TestRoleAccess:
    """The backend enforces this independently of what the UI renders."""

    def _user(self, role):
        return User(id=uuid.uuid4(), username=f"{role}-user", password_hash="x",
                    role=role, is_active=True)

    @pytest.fixture
    def as_role(self, app):
        def bind(role):
            app.dependency_overrides[require_authenticated_user] = lambda: self._user(role)
        yield bind
        app.dependency_overrides.pop(require_authenticated_user, None)

    def test_read_routes_require_only_authentication(self):
        """
        Phase 3 widened this deliberately: a USER reviews evidence and
        proposes a value, so a blank 403 page would be wrong. Asserted on
        the wiring rather than over HTTP — the read route needs a database, and
        what is under test here is authorization, not the query.
        """
        import app.api.v1.product_master as module

        source = Path(module.__file__).read_text()
        for route in ("/commercial\",", "/commercial/summary\",",
                      "/commercial/{mapping_id}\",", "/identity-unresolved\","):
            assert route in source
        # The router-wide guard is authentication, not the MANAGER role.
        assert "dependencies=[Depends(require_authenticated_user)]" in source

    async def test_user_cannot_approve(self, client, as_role):
        as_role(UserRole.USER.value)
        response = await client.post(
            f"/api/v1/product-master/commercial/{uuid.uuid4()}/approve", json={},
        )
        assert response.status_code == 403

    async def test_user_cannot_reject(self, client, as_role):
        as_role(UserRole.USER.value)
        response = await client.post(
            f"/api/v1/product-master/commercial/{uuid.uuid4()}/reject", json={},
        )
        assert response.status_code == 403

    def test_approve_and_reject_still_require_manager(self):
        import re

        import app.api.v1.product_master as module

        source = Path(module.__file__).read_text()
        for handler in ("approve_candidate", "reject_candidate"):
            body = re.search(rf"async def {handler}\((.*?)\) ->", source, re.S)
            assert body and "require_manager" in body.group(1), handler
        # Proposing is open to any authenticated account.
        body = re.search(r"async def propose_candidate\((.*?)\) ->", source, re.S)
        assert body and "require_authenticated_user" in body.group(1)

    async def test_an_anonymous_request_is_refused(self, client):
        assert (await client.get("/api/v1/product-master/commercial")).status_code == 401


class TestEdiIsolation:
    """Approving master data must not reach the export path."""

    def test_the_review_module_never_imports_the_export_service(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        for module in ("app/api/v1/product_master.py",
                       "app/services/master_commercial_review_service.py",
                       "app/repositories/master_commercial_repository.py"):
            source = (root / module).read_text()
            for forbidden in ("export_service", "build_pdi_export", "pdi_export_eligibility",
                              "_pdi_units_per_case", "normalize_item_code"):
                assert forbidden not in source, f"{module} must not touch {forbidden}"

    def test_the_review_workflow_never_writes_a_legacy_case_mapping(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        for module in ("app/api/v1/product_master.py",
                       "app/services/master_commercial_review_service.py",
                       "app/repositories/master_commercial_repository.py"):
            source = (root / module).read_text()
            # The model is imported to READ legacy context; never constructed,
            # and never mutated.
            assert not re.search(r"ProductCaseMapping\([^)]", source)
            assert ".units_per_case =" not in source

    def test_the_repository_never_commits(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent
                  / "app/repositories/master_commercial_repository.py").read_text()
        assert "commit()" not in source
