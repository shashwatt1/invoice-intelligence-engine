"""
tests/integration/test_proposal_review_api.py — the review UI's contract.

The Data Review page is an observation-and-decision layer over
product_data_proposals. These tests pin that the endpoints it calls
(a) show the queue and the audit trail faithfully, (b) route every
decision through proposal_service — the one writer of authoritative
mappings — and (c) never let a decided proposal be decided again.
"""

from __future__ import annotations

import uuid

from app.models.product_data_proposal import (
    SOURCE_BEER_INVENTORY_EXPLICIT,
    SOURCE_OPERATOR_ENTERED,
    SOURCE_REFERENCE_DERIVED,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from tests.integration.conftest import requires_db
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse
from tests.integration.test_proposal_governance import (  # noqa: F401 — fixture reuse
    NORMALIZED,
    STORE,
    confirm,
    line,
    process,
)

pytestmark = requires_db

OTHER = "01820023986"


async def _pending(db_session, value=4, key=NORMALIZED, source=SOURCE_REFERENCE_DERIVED,
                   invoice_id=None, **cols):
    p = await ProductDataProposalRepository(db_session).create(
        store_number=STORE, entity_type="case_mapping", entity_key=key,
        field="units_per_case", proposed_value=value, current_value=None,
        source=source, proposed_by="test", invoice_id=invoice_id,
        evidence={"invoice_description": "BUSCH 4/6/160Z CAN", "best": {"kind": "reference_ratio"}},
        reason="ratio hit a pack", **cols,
    )
    await db_session.commit()
    return p


class TestTheQueue:
    async def test_defaults_to_pending_newest_first(self, api_client, db_session):  # noqa: F811
        older = await _pending(db_session, 4)
        newer = await _pending(db_session, 24, key=OTHER)
        done = await _pending(db_session, 6, key="01820000801")
        # decide one so it drops out of the default view
        r = await api_client.post(f"/api/v1/proposals/{done.id}/reject",
                                  json={"reviewed_by": "reviewer:test"})
        assert r.status_code == 200, r.text

        body = (await api_client.get("/api/v1/proposals")).json()
        assert body["total"] == 2
        assert [row["id"] for row in body["items"]] == [str(newer.id), str(older.id)]
        assert {row["status"] for row in body["items"]} == {STATUS_PENDING}
        # every column the table shows is on the row
        row = body["items"][0]
        for col in ("entity_key", "field", "proposed_value", "current_value", "source",
                    "source_file", "source_sheet", "source_row", "invoice_id", "proposed_by",
                    "status", "reviewed_by", "reviewed_at", "review_note", "created_at"):
            assert col in row, col

    async def test_filters(self, api_client, db_session):  # noqa: F811
        a = await _pending(db_session, 4, source=SOURCE_REFERENCE_DERIVED)
        b = await _pending(db_session, 3, key=OTHER, source=SOURCE_BEER_INVENTORY_EXPLICIT,
                           source_file="Beer Inventory.xlsx", source_sheet="Sheet1", source_row=56)
        await api_client.post(f"/api/v1/proposals/{b.id}/approve", json={"reviewed_by": "r"})

        async def ids(**params):
            return {row["id"] for row in (await api_client.get("/api/v1/proposals", params=params)).json()["items"]}

        assert await ids(status="ALL") == {str(a.id), str(b.id)}
        assert await ids(status="APPROVED") == {str(b.id)}
        assert await ids(status="ALL", source=SOURCE_BEER_INVENTORY_EXPLICIT) == {str(b.id)}
        assert await ids(status="ALL", item_code=NORMALIZED) == {str(a.id)}
        # the UPC filter normalizes like everything else: check digit stripped
        assert await ids(status="ALL", item_code=NORMALIZED + "8") == {str(a.id)}
        assert await ids(status="ALL", item_code="99999999999") == set()

    async def test_filter_by_invoice_and_pagination(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await process(api_client, app, [line()], "q1.pdf", "queue invoice",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 4)
        await _pending(db_session, 24, key=OTHER)        # unrelated to the invoice

        by_invoice = (await api_client.get("/api/v1/proposals", params={"invoice_id": invoice_id})).json()
        assert by_invoice["total"] == 1
        assert by_invoice["items"][0]["entity_key"] == NORMALIZED
        assert by_invoice["items"][0]["source"] == "document_ambiguous"

        page = (await api_client.get("/api/v1/proposals", params={"page_size": 1, "page": 2})).json()
        assert page["total"] == 2 and page["page"] == 2 and len(page["items"]) == 1

    async def test_bad_filters_are_rejected(self, api_client):  # noqa: F811
        assert (await api_client.get("/api/v1/proposals", params={"status": "MAYBE"})).status_code == 422
        assert (await api_client.get("/api/v1/proposals", params={"source": "guess"})).status_code == 422


class TestDetail:
    async def test_detail_carries_evidence_and_master_state(self, api_client, db_session):  # noqa: F811
        p = await _pending(db_session, 4, source_file="Item Sales.xlsx", source_sheet="data", source_row=17)
        r = await api_client.get(f"/api/v1/proposals/{p.id}")
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["evidence"]["best"]["kind"] == "reference_ratio"
        assert d["reason"] == "ratio hit a pack"
        assert (d["source_file"], d["source_sheet"], d["source_row"]) == ("Item Sales.xlsx", "data", 17)
        assert d["current_master_value"] is None          # nothing authoritative yet
        assert d["resulting_mapping"] is None

    async def test_unknown_proposal_is_404(self, api_client):  # noqa: F811
        assert (await api_client.get(f"/api/v1/proposals/{uuid.uuid4()}")).status_code == 404


class TestDecisions:
    async def test_approve_writes_the_mapping_through_the_service(self, api_client, db_session):  # noqa: F811
        p = await _pending(db_session, 4)
        r = await api_client.post(f"/api/v1/proposals/{p.id}/approve",
                                  json={"reviewed_by": "data-team:shashwat", "note": "ratio + doc agree"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["applied_to"] == f"product_case_mappings:{STORE}:{NORMALIZED}"
        assert d["proposal"]["status"] == STATUS_APPROVED
        assert d["proposal"]["reviewed_by"] == "data-team:shashwat"
        assert d["proposal"]["reviewed_at"] is not None
        assert d["proposal"]["review_note"] == "ratio + doc agree"
        # the linkage is visible in the response…
        assert d["proposal"]["resulting_mapping"]["approved_proposal_id"] == str(p.id)
        assert d["proposal"]["resulting_mapping"]["source"] == "APPROVED"
        assert d["proposal"]["current_master_value"] == 4
        # …and real in the table
        mapping = await ProductCaseMappingRepository(db_session).get(STORE, NORMALIZED)
        assert mapping.units_per_case == 4 and mapping.approved_proposal_id == p.id

    async def test_reject_leaves_master_data_untouched(self, api_client, db_session):  # noqa: F811
        p = await _pending(db_session, 4)
        r = await api_client.post(f"/api/v1/proposals/{p.id}/reject",
                                  json={"reviewed_by": "data-team:shashwat", "note": "wrong pack"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["applied_to"] is None
        assert d["proposal"]["status"] == STATUS_REJECTED
        assert d["proposal"]["review_note"] == "wrong pack"
        assert await ProductCaseMappingRepository(db_session).get(STORE, NORMALIZED) is None

    async def test_a_decided_proposal_cannot_be_decided_again(self, api_client, db_session):  # noqa: F811
        p = await _pending(db_session, 4)
        await api_client.post(f"/api/v1/proposals/{p.id}/reject", json={"reviewed_by": "r1"})
        again = await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"reviewed_by": "r2"})
        assert again.status_code == 422
        assert again.json()["error"]["detail"]["status"] == STATUS_REJECTED
        assert await ProductCaseMappingRepository(db_session).get(STORE, NORMALIZED) is None
        await db_session.refresh(p)
        assert p.reviewed_by == "r1"                      # the first decision stands

    async def test_reviewer_name_is_required(self, api_client, db_session):  # noqa: F811
        p = await _pending(db_session, 4)
        assert (await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={})).status_code == 422
        assert (await api_client.post(f"/api/v1/proposals/{p.id}/approve",
                                      json={"reviewed_by": ""})).status_code == 422
        await db_session.refresh(p)
        assert p.status == STATUS_PENDING

    async def test_deciding_an_unknown_proposal_is_404(self, api_client):  # noqa: F811
        r = await api_client.post(f"/api/v1/proposals/{uuid.uuid4()}/approve", json={"reviewed_by": "r"})
        assert r.status_code == 404

    async def test_approval_unblocks_the_invoice(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await process(api_client, app, [line()], "d1.pdf", "unblock",
                                   subtotal=18.75, grand_total=18.75)
        await confirm(api_client, invoice_id, 24)
        [p] = await ProductDataProposalRepository(db_session).list(entity_key=NORMALIZED)
        before = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert before["pdi_export_allowed"] is False
        assert before["case_mappings"][0]["pending_value"] == 24

        await api_client.post(f"/api/v1/proposals/{p.id}/approve", json={"reviewed_by": "r"})
        after = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert after["pdi_export_allowed"] is True
        assert after["case_mappings"][0]["units_per_case"] == 24
        assert after["case_mappings"][0]["suggestion_source"] == "database"
        assert after["case_mappings"][0]["pending_value"] is None


class TestProductHistory:
    async def test_tells_the_whole_story_of_one_upc(self, api_client, db_session):  # noqa: F811
        first = await _pending(db_session, 4)
        await api_client.post(f"/api/v1/proposals/{first.id}/reject", json={"reviewed_by": "r", "note": "no"})
        second = await _pending(db_session, 24, source=SOURCE_OPERATOR_ENTERED)
        await api_client.post(f"/api/v1/proposals/{second.id}/approve", json={"reviewed_by": "r"})
        third = await _pending(db_session, 6)             # still pending, would change 24 -> 6

        r = await api_client.get(f"/api/v1/products/{NORMALIZED}8/history",     # as printed, check digit on
                                 params={"store_number": STORE})
        assert r.status_code == 200
        h = r.json()["data"]
        assert h["item_code"] == NORMALIZED
        assert h["current_mapping"]["units_per_case"] == 24
        assert h["current_mapping"]["approved_proposal_id"] == str(second.id)
        assert [p["id"] for p in h["proposals"]] == [str(first.id), str(second.id), str(third.id)]
        assert [p["status"] for p in h["proposals"]] == [STATUS_REJECTED, STATUS_APPROVED, STATUS_PENDING]
        # only the proposal the mapping points at claims to have produced it
        assert [p["resulting_mapping"] is not None for p in h["proposals"]] == [False, True, False]
        assert {p["current_master_value"] for p in h["proposals"]} == {24}

    async def test_unknown_product_has_an_empty_history(self, api_client):  # noqa: F811
        h = (await api_client.get("/api/v1/products/99999999999/history",
                                  params={"store_number": STORE})).json()["data"]
        assert h["current_mapping"] is None and h["proposals"] == []
        assert (await api_client.get("/api/v1/products/abc/history",
                                     params={"store_number": STORE})).status_code == 422
        # the store is not optional: a UPC's history is one store's history
        assert (await api_client.get(f"/api/v1/products/{NORMALIZED}/history")).status_code == 422
