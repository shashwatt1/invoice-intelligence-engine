"""
tests/test_product_master_governance.py — review safety before broad data-team use.

  * Proposals, approvals, rejections and reopenings each need a stated basis.
  * A decision made against a stale view is refused (409); nothing is overwritten.
  * Reopening is a new, append-only event; the decision it reconsiders is kept.
  * Multi-select approval is all-or-nothing and writes one review event per
    mapping through the same approve(); conflicts, proposals and reopened rows
    are refused and need individual review.
  * Only MANAGER/ADMIN decide; who decided is always the session.
  * Only the review service writes a mapping's decision fields; approval never
    touches product identity or the evidence.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from app.core.exceptions import StaleReviewError, ValidationError
from app.models.product_master import (
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    COMMERCIAL_CONFLICT,
    COMMERCIAL_UNIT_IS_SELLING_UNIT,
    DECISION_APPROVE,
    DECISION_REOPEN,
    STATE_APPROVED,
    STATE_PENDING,
    STATE_REJECTED,
    STATE_REVIEW_REQUIRED,
)
from app.models.user import User, UserRole
from app.services import master_commercial_review_service as service

ROOT = Path(__file__).resolve().parent.parent
BASIS = "Distributor items/case column states 4."


class Mapping:
    def __init__(self, *, basis=COMMERCIAL_UNIT_IS_SELLING_UNIT, units=4, state=STATE_REVIEW_REQUIRED,
                 proposed=None):
        self.id = uuid.uuid4()
        self.commercial_unit_basis = basis
        self.units_accounted_for = units
        self.approval_state = state
        self.cost_basis = "DISTRIBUTOR_CASE_PRICE"
        self.reviewed_by = None
        self.reviewed_at = None
        self.proposed_units_accounted_for = proposed
        self.proposed_by = None
        self.proposed_at = None
        self.proposed_note = None
        self.evidence = {"notes": "Sheet1 items/case column", "source_statements": [{"units": 4}]}
        self.source_snapshot = {"source_rows": [{"raw_items_case": "4"}]}


class Repository:
    """Several candidates, their append-only history, and the locks taken."""

    def __init__(self, *mappings):
        self.mappings = {m.id: m for m in mappings}
        self.reviews = []
        self.locked = []

    async def get(self, mapping_id, *, for_update=False):
        if for_update:
            self.locked.append(mapping_id)
        m = self.mappings.get(mapping_id)
        return (m, None, None) if m else None

    def _of(self, mapping_id):
        return [r for r in self.reviews if r.mapping_id == mapping_id]

    async def review_version(self, mapping_id):
        return len(self._of(mapping_id))

    async def review_facts(self, ids):
        return {i: (len(self._of(i)), self._of(i)[-1].decision if self._of(i) else None) for i in ids}

    async def history_for(self, mapping_id):
        return list(reversed(self._of(mapping_id)))   # newest first, like the real repository

    async def add_review(self, review):
        self.reviews.append(review)
        return review


@pytest.fixture
def repo(monkeypatch):
    def bind(*mappings):
        repository = Repository(*mappings)
        monkeypatch.setattr(service, "MasterCommercialRepository", lambda _s: repository)
        return repository
    return bind


# ---- bases -------------------------------------------------------------------

class TestEveryActionStatesItsBasis:
    @pytest.mark.parametrize("note", [None, "", "   "])
    async def test_a_proposal_needs_a_basis(self, repo, note):
        m = Mapping()
        r = repo(m)
        with pytest.raises(ValidationError) as refused:
            await service.propose(None, m.id, units_accounted_for=12, proposer="vivek", note=note)
        assert refused.value.detail["decision"] == "PROPOSE"
        assert m.approval_state == STATE_REVIEW_REQUIRED and r.reviews == []

    async def test_bases_are_stored_trimmed(self, repo):
        m = Mapping()
        r = repo(m)
        await service.propose(None, m.id, units_accounted_for=12, proposer="vivek", note="  sheet says 12  ")
        assert r.reviews[0].note == "sheet says 12"


# ---- stale protection --------------------------------------------------------

class TestStaleDecisionsAreRefused:
    async def test_manager_a_cannot_overwrite_manager_b(self, repo):
        m = Mapping()
        r = repo(m)
        await service.approve(None, m.id, reviewer="barj", note=BASIS, expected_review_version=0)
        with pytest.raises(StaleReviewError) as refused:
            await service.reject(None, m.id, reviewer="prabh", note="stale view", expected_review_version=0)
        assert refused.value.detail["review_version"] == 1
        assert m.approval_state == STATE_APPROVED and m.reviewed_by == "barj"
        assert [x.decision for x in r.reviews] == [DECISION_APPROVE]

    async def test_a_proposal_since_opening_makes_the_view_stale(self, repo):
        m = Mapping()
        repo(m)
        await service.propose(None, m.id, units_accounted_for=12, proposer="vivek", note="sheet says 12")
        with pytest.raises(StaleReviewError):
            await service.approve(None, m.id, reviewer="barj", note=BASIS, expected_review_version=0)
        assert m.approval_state == STATE_PENDING

    async def test_every_decision_locks_the_row(self, repo):
        m = Mapping()
        r = repo(m)
        await service.approve(None, m.id, reviewer="barj", note=BASIS, expected_review_version=0)
        assert r.locked == [m.id]

    async def test_repeating_a_decision_on_the_current_view_is_harmless(self, repo):
        m = Mapping()
        r = repo(m)
        await service.approve(None, m.id, reviewer="barj", note=BASIS, expected_review_version=0)
        outcome = await service.approve(None, m.id, reviewer="barj", note=BASIS, expected_review_version=1)
        assert outcome.new_state == STATE_APPROVED and len(r.reviews) == 1


# ---- reopening ---------------------------------------------------------------

class TestReopening:
    async def test_reopening_is_a_new_event_and_restores_the_evidence_interpretation(self, repo):
        m = Mapping(basis=COMMERCIAL_CONFLICT, units=None)
        r = repo(m)
        await service.approve(None, m.id, reviewer="barj", note="Case is one unit",
                              commercial_unit_basis=COMMERCIAL_CASE_IS_SELLING_UNIT, expected_review_version=0)
        account = uuid.uuid4()
        await service.reopen(None, m.id, reviewer="shashwatt1", reason="  Distributor corrected the sheet ",
                             reviewer_user_id=account, reviewer_role="ADMIN", expected_review_version=1)
        assert (m.approval_state, m.commercial_unit_basis, m.units_accounted_for) == (
            STATE_REVIEW_REQUIRED, COMMERCIAL_CONFLICT, None)
        assert m.reviewed_by is None
        approve_entry, reopen_entry = r.reviews
        assert approve_entry.decision == DECISION_APPROVE, "the original decision is kept"
        assert (reopen_entry.decision, reopen_entry.previous_approval_state, reopen_entry.new_approval_state) == (
            DECISION_REOPEN, STATE_APPROVED, STATE_REVIEW_REQUIRED)
        assert (reopen_entry.reviewer_user_id, reopen_entry.reviewer_role, reopen_entry.note) == (
            account, "ADMIN", "Distributor corrected the sheet")
        assert reopen_entry.previous_commercial_unit_basis == COMMERCIAL_CASE_IS_SELLING_UNIT

    async def test_a_rejected_mapping_reopens_unchanged(self, repo):
        m = Mapping()
        repo(m)
        await service.reject(None, m.id, reviewer="barj", note="stale sheet", expected_review_version=0)
        await service.reopen(None, m.id, reviewer="barj", reason="sheet refreshed", expected_review_version=1)
        assert (m.approval_state, m.units_accounted_for) == (STATE_REVIEW_REQUIRED, 4)

    @pytest.mark.parametrize("reason", [None, " "])
    async def test_reopening_needs_a_reason(self, repo, reason):
        m = Mapping(state=STATE_APPROVED)
        r = repo(m)
        with pytest.raises(ValidationError):
            await service.reopen(None, m.id, reviewer="barj", reason=reason)
        assert m.approval_state == STATE_APPROVED and r.reviews == []

    async def test_only_a_decided_mapping_can_be_reopened(self, repo):
        m = Mapping()
        repo(m)
        with pytest.raises(ValidationError, match="Only an approved or rejected"):
            await service.reopen(None, m.id, reviewer="barj", reason="why")


# ---- multi-select approval ---------------------------------------------------

def items(*mappings, version=0):
    return [service.BulkApprovalItem(m.id, version) for m in mappings]


class TestBulkApproval:
    async def test_each_mapping_is_approved_and_recorded_individually(self, repo):
        ready = [Mapping() for _ in range(3)]
        r = repo(*ready)
        account = uuid.uuid4()
        outcomes = await service.bulk_approve(None, items(*ready), reviewer="barj", note=f"  {BASIS} ",
                                              reviewer_user_id=account, reviewer_role="MANAGER")
        assert len(outcomes) == 3 and all(m.approval_state == STATE_APPROVED for m in ready)
        assert [x.mapping_id for x in r.reviews] == [m.id for m in ready]
        assert {(x.decision, x.reviewer, x.reviewer_user_id, x.reviewer_role, x.note) for x in r.reviews} == {
            (DECISION_APPROVE, "barj", account, "MANAGER", BASIS)}
        assert all(x.evidence_considered == ready[0].evidence for x in r.reviews)

    @pytest.mark.parametrize("blocker", [
        Mapping(basis=COMMERCIAL_CONFLICT, units=None),
        Mapping(units=None),
        Mapping(state=STATE_PENDING, proposed=12),
    ])
    async def test_a_row_needing_an_individual_decision_refuses_the_whole_batch(self, repo, blocker):
        ready = Mapping()
        r = repo(ready, blocker)
        with pytest.raises(ValidationError) as refused:
            await service.bulk_approve(None, items(ready, blocker), reviewer="barj", note=BASIS)
        assert refused.value.detail["ineligible"][0]["mapping_id"] == str(blocker.id)
        assert ready.approval_state == STATE_REVIEW_REQUIRED and r.reviews == []

    async def test_a_reopened_row_needs_individual_review(self, repo):
        m = Mapping()
        r = repo(m)
        await service.approve(None, m.id, reviewer="barj", note=BASIS, expected_review_version=0)
        await service.reopen(None, m.id, reviewer="barj", reason="reconsider", expected_review_version=1)
        with pytest.raises(ValidationError):
            await service.bulk_approve(None, items(m, version=2), reviewer="barj", note=BASIS)
        assert m.approval_state == STATE_REVIEW_REQUIRED and len(r.reviews) == 2

    async def test_a_stale_row_refuses_the_whole_batch_and_writes_nothing(self, repo):
        fresh, decided = Mapping(), Mapping()
        r = repo(fresh, decided)
        await service.approve(None, decided.id, reviewer="prabh", note=BASIS, expected_review_version=0)
        with pytest.raises(StaleReviewError) as refused:
            await service.bulk_approve(None, items(fresh, decided), reviewer="barj", note=BASIS)
        assert refused.value.detail["stale"][0]["mapping_id"] == str(decided.id)
        assert fresh.approval_state == STATE_REVIEW_REQUIRED and len(r.reviews) == 1

    async def test_rows_are_locked_in_one_order(self, repo):
        ms = [Mapping() for _ in range(4)]
        r = repo(*ms)
        await service.bulk_approve(None, items(*reversed(ms)), reviewer="barj", note=BASIS)
        assert r.locked[:4] == sorted(m.id for m in ms)

    @pytest.mark.parametrize(("chosen", "note"), [("none", BASIS), ("twice", BASIS), ("one", " ")])
    async def test_malformed_requests_are_refused(self, repo, chosen, note):
        m = Mapping()
        r = repo(m)
        selection = {"none": [], "twice": items(m, m), "one": items(m)}[chosen]
        with pytest.raises(ValidationError):
            await service.bulk_approve(None, selection, reviewer="barj", note=note)
        assert r.reviews == []


# ---- over the API ------------------------------------------------------------

class _Session:
    async def commit(self):
        return None


@pytest.fixture
def api(app, repo):
    from app.core.dependencies import require_authenticated_user
    from app.database.session import get_db

    async def fake_db():
        yield _Session()

    app.dependency_overrides[get_db] = fake_db

    def bind(role, *mappings):
        account = User(id=uuid.uuid4(), username=f"{role.lower()}-account", password_hash="x", role=role,
                       is_active=True)
        app.dependency_overrides[require_authenticated_user] = lambda: account
        return account, repo(*mappings)
    yield bind
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_authenticated_user, None)


URL = "/api/v1/product-master/commercial"


class TestTheApi:
    @pytest.mark.parametrize("action", ["approve", "reject", "reopen", "bulk"])
    async def test_a_user_cannot_decide(self, client, api, action):
        m = Mapping(state=STATE_APPROVED if action == "reopen" else STATE_REVIEW_REQUIRED)
        _, r = api(UserRole.USER.value, m)
        if action == "bulk":
            response = await client.post(f"{URL}/bulk-approve", json={
                "items": [{"mapping_id": str(m.id), "expected_review_version": 0}], "note": BASIS})
        else:
            response = await client.post(f"{URL}/{m.id}/{action}", json={
                "note": BASIS, "reason": BASIS, "expected_review_version": 0})
        assert response.status_code == 403 and r.reviews == []

    @pytest.mark.parametrize("role", [UserRole.MANAGER.value, UserRole.ADMIN.value])
    async def test_managers_bulk_approve_as_themselves(self, client, api, role):
        ms = [Mapping(), Mapping()]
        account, r = api(role, *ms)
        response = await client.post(f"{URL}/bulk-approve", json={
            "items": [{"mapping_id": str(m.id), "expected_review_version": 0} for m in ms],
            "note": BASIS, "reviewer": "someone-else", "reviewer_role": "ADMIN"})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["approved"] == 2
        assert {(x.reviewer, x.reviewer_user_id, x.reviewer_role) for x in r.reviews} == {
            (account.username, account.id, role)}

    @pytest.mark.parametrize("role", [UserRole.MANAGER.value, UserRole.ADMIN.value])
    async def test_managers_reopen_with_a_reason(self, client, api, role):
        m = Mapping(state=STATE_APPROVED)
        account, r = api(role, m)
        response = await client.post(f"{URL}/{m.id}/reopen", json={"reason": "reconsider", "expected_review_version": 0})
        assert response.status_code == 200, response.text
        assert (r.reviews[0].decision, r.reviews[0].reviewer) == (DECISION_REOPEN, account.username)
        blank = await client.post(f"{URL}/{m.id}/reopen", json={"reason": " ", "expected_review_version": 1})
        assert blank.status_code == 422

    async def test_a_stale_decision_is_a_409(self, client, api):
        m = Mapping()
        _, r = api(UserRole.MANAGER.value, m)
        await client.post(f"{URL}/{m.id}/approve", json={"note": BASIS, "expected_review_version": 0})
        response = await client.post(f"{URL}/{m.id}/reject", json={"note": "late", "expected_review_version": 0})
        assert response.status_code == 409
        assert response.json()["error"]["error_code"] == "ERR_STALE_REVIEW"
        assert m.approval_state == STATE_APPROVED and len(r.reviews) == 1

    async def test_a_decision_must_say_which_version_it_saw(self, client, api):
        m = Mapping()
        _, r = api(UserRole.MANAGER.value, m)
        response = await client.post(f"{URL}/{m.id}/approve", json={"note": BASIS})
        assert response.status_code == 422 and r.reviews == []

    async def test_a_proposal_without_a_basis_is_refused_over_the_api(self, client, api):
        m = Mapping()
        _, r = api(UserRole.USER.value, m)
        response = await client.post(f"{URL}/{m.id}/propose", json={"units_accounted_for": 12})
        assert response.status_code == 422 and r.reviews == []


# ---- structural guarantees ---------------------------------------------------

class TestOneGovernedWritePath:
    def test_only_the_review_service_writes_a_mappings_decision(self):
        fields = {"approval_state", "commercial_unit_basis", "units_accounted_for", "reviewed_by"}
        writers = set()
        for path in (ROOT / "app").rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Attribute) and t.attr in fields and ast.unparse(t.value) == "mapping":
                            writers.add(str(path.relative_to(ROOT)))
        assert writers == {"app/services/master_commercial_review_service.py"}

    def test_approval_never_touches_product_identity(self):
        source = (ROOT / "app/services/master_commercial_review_service.py").read_text()
        for model in ("MasterProduct(", "MasterProductIdentifier", "MasterProductDescription", "MasterPackComposition"):
            assert model not in source, model

    async def test_the_evidence_a_decision_rests_on_is_left_intact(self, repo):
        m = Mapping()
        repo(m)
        before = (dict(m.evidence), dict(m.source_snapshot))
        await service.approve(None, m.id, reviewer="barj", note=BASIS, expected_review_version=0)
        await service.reopen(None, m.id, reviewer="barj", reason="check", expected_review_version=1)
        assert (m.evidence, m.source_snapshot) == before

    def test_the_undecided_filter_is_exactly_the_open_states(self):
        from sqlalchemy.dialects import postgresql

        from app.repositories.master_commercial_repository import _review_status_clause

        sql = str(_review_status_clause("UNDECIDED").compile(dialect=postgresql.dialect(),
                                                             compile_kwargs={"literal_binds": True}))
        assert "IN ('REVIEW_REQUIRED', 'PENDING')" in sql
        assert STATE_REJECTED not in sql and STATE_APPROVED not in sql
