"""
tests/integration/test_bulk_review_api.py — the review table's fast path.

Bulk approve / reject and revise exist so a reviewer does not have to open
every proposal to decide the obvious ones. They must be a UI convenience
and nothing more: every row still goes through proposal_service.approve()
(the one writer of product_case_mappings), keeps its own id, reviewer,
timestamp, note and evidence, and a batch either lands whole or not at
all. A revision never edits history — it is a new proposal and the old
one is frozen as superseded.
"""

from __future__ import annotations

import uuid

from app.models.product_data_proposal import (
    SOURCE_OPERATOR_ENTERED,
    SOURCE_REFERENCE_DERIVED,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from tests.integration.conftest import requires_db, store_id
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse
from tests.integration.test_proposal_governance import (  # noqa: F401 — fixture reuse
    NORMALIZED,
    STORE,
    confirm,
    line,
    process,
)
from tests.integration.test_proposal_review_api import _pending

pytestmark = requires_db

CODES = [NORMALIZED, "01820023986", "01820000801"]
REVIEWER = "data-team:shashwat"


async def _three_pending(db_session, values=(4, 24, 6)):
    return [await _pending(db_session, v, key=c) for v, c in zip(values, CODES, strict=True)]


async def _mapping(db_session, code):
    return await ProductCaseMappingRepository(db_session).get(store_id(STORE), code)


async def _no_mappings(db_session):
    return all([await _mapping(db_session, c) is None for c in CODES])


class TestBulkApprove:
    async def test_each_row_is_approved_through_the_service_with_its_own_audit(self, api_client, db_session):  # noqa: F811
        pending = await _three_pending(db_session)
        r = await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [str(p.id) for p in pending],
            "reviewed_by": REVIEWER, "note": "printed count and ratio agree",
        })
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["reviewed_by"] == REVIEWER
        assert [o["id"] for o in d["decided"]] == [str(p.id) for p in pending]
        assert {o["status"] for o in d["decided"]} == {STATUS_APPROVED}
        assert [o["applied_to"] for o in d["decided"]] == [
            f"product_case_mappings:{store_id(STORE)}:{c}" for c in CODES]

        for p, code, value in zip(pending, CODES, (4, 24, 6), strict=True):
            await db_session.refresh(p)
            # the proposal's own audit row: reviewer, time, note, id preserved
            assert p.status == STATUS_APPROVED
            assert p.reviewed_by == REVIEWER
            assert p.reviewed_at is not None
            assert p.review_note == "printed count and ratio agree"
            assert p.evidence["best"]["kind"] == "reference_ratio"       # evidence untouched
            # and the authoritative row it produced, linked back to it
            mapping = await _mapping(db_session, code)
            assert mapping.units_per_case == value
            assert mapping.source == "APPROVED"
            assert mapping.approved_proposal_id == p.id

    async def test_note_is_optional_and_reviewer_is_not(self, api_client, db_session):  # noqa: F811
        [p, *_] = await _three_pending(db_session)
        assert (await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [str(p.id)]})).status_code == 422
        assert (await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [str(p.id)], "reviewed_by": ""})).status_code == 422
        assert (await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [], "reviewed_by": REVIEWER})).status_code == 422
        await db_session.refresh(p)
        assert p.status == STATUS_PENDING

        ok = await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [str(p.id)], "reviewed_by": REVIEWER})
        assert ok.status_code == 200
        await db_session.refresh(p)
        assert p.status == STATUS_APPROVED and p.review_note is None

    async def test_one_already_approved_row_refuses_the_whole_batch(self, api_client, db_session):  # noqa: F811
        pending = await _three_pending(db_session)
        stale = pending[1]
        await api_client.post(f"/api/v1/proposals/{stale.id}/approve", json={"reviewed_by": "cli:earlier"})

        r = await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [str(p.id) for p in pending], "reviewed_by": REVIEWER})
        assert r.status_code == 422, r.text
        err = r.json()["error"]
        assert err["detail"]["failures"] == {str(stale.id): f"already {STATUS_APPROVED}"}
        assert "nothing was changed" in err["message"]

        for p in (pending[0], pending[2]):
            await db_session.refresh(p)
            assert p.status == STATUS_PENDING                       # the others did not land
            assert p.reviewed_by is None
        assert await _mapping(db_session, CODES[0]) is None
        assert await _mapping(db_session, CODES[2]) is None
        await db_session.refresh(stale)
        assert stale.reviewed_by == "cli:earlier"                   # the first decision stands

    async def test_one_already_rejected_row_refuses_the_whole_batch(self, api_client, db_session):  # noqa: F811
        pending = await _three_pending(db_session)
        await api_client.post(f"/api/v1/proposals/{pending[0].id}/reject", json={"reviewed_by": "r"})
        r = await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [str(p.id) for p in pending], "reviewed_by": REVIEWER})
        assert r.status_code == 422
        assert r.json()["error"]["detail"]["failures"] == {str(pending[0].id): f"already {STATUS_REJECTED}"}
        assert await _no_mappings(db_session)

    async def test_an_unknown_id_refuses_the_whole_batch(self, api_client, db_session):  # noqa: F811
        pending = await _three_pending(db_session)
        ghost = uuid.uuid4()
        r = await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [str(pending[0].id), str(ghost)], "reviewed_by": REVIEWER})
        assert r.status_code == 422
        assert r.json()["error"]["detail"]["failures"] == {str(ghost): "not found"}
        await db_session.refresh(pending[0])
        assert pending[0].status == STATUS_PENDING
        assert await _mapping(db_session, CODES[0]) is None

    async def test_duplicate_ids_are_decided_once(self, api_client, db_session):  # noqa: F811
        [p, *_] = await _three_pending(db_session)
        r = await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [str(p.id), str(p.id)], "reviewed_by": REVIEWER})
        assert r.status_code == 200, r.text
        assert len(r.json()["data"]["decided"]) == 1

    async def test_bulk_approval_unblocks_the_invoice_like_a_single_one(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await process(api_client, app, [line()], "bulk.pdf", "bulk unblock",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 24)
        [p] = await ProductDataProposalRepository(db_session).list(entity_key=NORMALIZED)
        r = await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [str(p.id)], "reviewed_by": REVIEWER})
        assert r.status_code == 200
        after = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert after["pdi_export_allowed"] is True
        assert after["case_mappings"][0]["units_per_case"] == 24
        assert after["case_mappings"][0]["pending_value"] is None


class TestBulkReject:
    async def test_rejects_every_row_and_writes_no_master_data(self, api_client, db_session):  # noqa: F811
        pending = await _three_pending(db_session)
        r = await api_client.post("/api/v1/proposals/bulk-reject", json={
            "proposal_ids": [str(p.id) for p in pending],
            "reviewed_by": REVIEWER, "note": "test run, invoice deleted"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert {o["status"] for o in d["decided"]} == {STATUS_REJECTED}
        assert all(o["applied_to"] is None for o in d["decided"])
        for p in pending:
            await db_session.refresh(p)
            assert p.status == STATUS_REJECTED
            assert p.reviewed_by == REVIEWER
            assert p.reviewed_at is not None
            assert p.review_note == "test run, invoice deleted"
        assert await _no_mappings(db_session)
        # they have left the queue and are in the history
        queue = (await api_client.get("/api/v1/proposals")).json()
        assert queue["total"] == 0
        history = (await api_client.get("/api/v1/proposals", params={"status": "REJECTED"})).json()
        assert history["total"] == 3

    async def test_a_decided_row_refuses_the_batch(self, api_client, db_session):  # noqa: F811
        pending = await _three_pending(db_session)
        await api_client.post(f"/api/v1/proposals/{pending[2].id}/approve", json={"reviewed_by": "r"})
        r = await api_client.post("/api/v1/proposals/bulk-reject", json={
            "proposal_ids": [str(p.id) for p in pending], "reviewed_by": REVIEWER})
        assert r.status_code == 422
        for p in pending[:2]:
            await db_session.refresh(p)
            assert p.status == STATUS_PENDING
        assert (await _mapping(db_session, CODES[2])).units_per_case == 6   # untouched by the refusal


class TestRevise:
    async def test_a_revision_is_a_new_proposal_and_the_original_is_superseded(self, api_client, db_session):  # noqa: F811
        original = await _pending(db_session, 12, source=SOURCE_REFERENCE_DERIVED,
                                  source_file="Item Sales.xlsx", source_sheet="data", source_row=17)
        r = await api_client.post(f"/api/v1/proposals/{original.id}/revise", json={
            "proposed_value": 15, "proposed_by": REVIEWER, "note": "invoice packaging says C-15"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        new, old = d["proposal"], d["superseded"]

        # the new proposal: pending, the reviewer's number, evidence kept and annotated
        assert new["id"] != old["id"]
        assert new["status"] == STATUS_PENDING
        assert new["proposed_value"] == 15
        assert new["proposed_by"] == REVIEWER
        assert new["source"] == SOURCE_OPERATOR_ENTERED
        assert new["entity_key"] == NORMALIZED and new["store"]["id"] == str(store_id(STORE))
        assert (new["source_file"], new["source_sheet"], new["source_row"]) == ("Item Sales.xlsx", "data", 17)
        assert new["evidence"]["best"]["kind"] == "reference_ratio"          # original evidence kept
        assert new["evidence"]["revised_from"] == str(original.id)
        assert new["evidence"]["revised_from_value"] == 12
        assert new["evidence"]["revised_from_source"] == SOURCE_REFERENCE_DERIVED
        assert new["evidence"]["revision_note"] == "invoice packaging says C-15"
        assert str(original.id) in new["reason"] and "12 -> 15" in new["reason"]
        assert new["current_value"] is None                                  # still unmapped

        # the original: frozen, names its successor, master data untouched
        assert old["id"] == str(original.id)
        assert old["status"] == STATUS_REJECTED
        assert old["reviewed_by"] == REVIEWER
        assert old["reviewed_at"] is not None
        assert new["id"] in old["review_note"] and "12 -> 15" in old["review_note"]
        assert old["proposed_value"] == 12                                   # history not rewritten
        assert await _mapping(db_session, NORMALIZED) is None

        # the queue now shows the revision, not the original
        queue = (await api_client.get("/api/v1/proposals")).json()
        assert [row["id"] for row in queue["items"]] == [new["id"]]

    async def test_edit_then_approve_writes_the_revised_value_through_the_service(self, api_client, db_session):  # noqa: F811
        original = await _pending(db_session, 12)
        revised = (await api_client.post(f"/api/v1/proposals/{original.id}/revise", json={
            "proposed_value": 15, "proposed_by": "reviewer:edit"})).json()["data"]["proposal"]
        r = await api_client.post("/api/v1/proposals/bulk-approve", json={
            "proposal_ids": [revised["id"]], "reviewed_by": REVIEWER})
        assert r.status_code == 200, r.text

        mapping = await _mapping(db_session, NORMALIZED)
        assert mapping.units_per_case == 15
        assert mapping.source == "APPROVED"
        assert mapping.approved_proposal_id == uuid.UUID(revised["id"])
        # the whole story is answerable from the history
        history = (await api_client.get(f"/api/v1/products/{NORMALIZED}/history",
                                        params={"store_id": str(store_id(STORE))})).json()["data"]
        statuses = [(p["proposed_value"], p["status"], p["proposed_by"], p["reviewed_by"])
                    for p in history["proposals"]]
        assert statuses == [(12, STATUS_REJECTED, "test", "reviewer:edit"),
                            (15, STATUS_APPROVED, "reviewer:edit", REVIEWER)]
        assert history["proposals"][1]["evidence"]["revised_from"] == str(original.id)
        assert history["current_mapping"]["approved_proposal_id"] == revised["id"]

    async def test_the_superseded_original_cannot_be_approved(self, api_client, db_session):  # noqa: F811
        original = await _pending(db_session, 12)
        await api_client.post(f"/api/v1/proposals/{original.id}/revise", json={
            "proposed_value": 15, "proposed_by": REVIEWER})
        r = await api_client.post(f"/api/v1/proposals/{original.id}/approve", json={"reviewed_by": REVIEWER})
        assert r.status_code == 422
        assert await _mapping(db_session, NORMALIZED) is None

    async def test_a_decided_proposal_cannot_be_revised(self, api_client, db_session):  # noqa: F811
        p = await _pending(db_session, 12)
        await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"reviewed_by": REVIEWER})
        r = await api_client.post(f"/api/v1/proposals/{p.id}/revise", json={
            "proposed_value": 15, "proposed_by": REVIEWER})
        assert r.status_code == 422
        assert r.json()["error"]["detail"]["status"] == STATUS_APPROVED
        assert (await _mapping(db_session, NORMALIZED)).units_per_case == 12
        assert (await api_client.get("/api/v1/proposals")).json()["total"] == 0   # no stray new proposal

    async def test_an_unchanged_value_is_not_a_revision(self, api_client, db_session):  # noqa: F811
        p = await _pending(db_session, 12)
        r = await api_client.post(f"/api/v1/proposals/{p.id}/revise", json={
            "proposed_value": 12, "proposed_by": REVIEWER})
        assert r.status_code == 422
        await db_session.refresh(p)
        assert p.status == STATUS_PENDING
        assert (await api_client.get("/api/v1/proposals")).json()["total"] == 1

    async def test_value_bounds_and_author_are_enforced(self, api_client, db_session):  # noqa: F811
        p = await _pending(db_session, 12)
        for body in ({"proposed_value": 0, "proposed_by": REVIEWER},
                     {"proposed_value": 15, "proposed_by": ""},
                     {"proposed_value": 15}):
            assert (await api_client.post(f"/api/v1/proposals/{p.id}/revise", json=body)).status_code == 422
        assert (await api_client.post(f"/api/v1/proposals/{uuid.uuid4()}/revise", json={
            "proposed_value": 15, "proposed_by": REVIEWER})).status_code == 404
        await db_session.refresh(p)
        assert p.status == STATUS_PENDING

    async def test_revising_records_the_current_master_value(self, api_client, db_session):  # noqa: F811
        first = await _pending(db_session, 12)
        await api_client.post(f"/api/v1/proposals/{first.id}/approve", json={"reviewed_by": "r"})
        second = await _pending(db_session, 24)
        new = (await api_client.post(f"/api/v1/proposals/{second.id}/revise", json={
            "proposed_value": 18, "proposed_by": REVIEWER})).json()["data"]["proposal"]
        assert new["current_value"] == 12                 # what the EDI uses today, at revision time
        assert new["current_master_value"] == 12
        assert (await _mapping(db_session, NORMALIZED)).units_per_case == 12   # untouched
