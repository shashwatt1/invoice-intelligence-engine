"""
tests/integration/test_inactive_documents_db.py — invoices out of the active workflow, against real Postgres.

A document in the Recycle Bin (BINNED) or a read-only reference copy
(REFERENCE) can still be read, but:
  * every change, the PDI and CSV exports, the Retailed mark, duplicate-row
    decisions and reprocessing are refused with ERR_INVOICE_INACTIVE (409),
    and nothing is written;
  * a reference copy is never received into inventory: approving its pending
    receipt is refused and no movement is written;
  * it is not counted or offered for work: the default invoice list, the
    dashboard, the mapping queue, store invoices, supply history and vendor
    detail leave it out; asking for its status still lists it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.document import Document
from app.models.inventory import RECEIPT_SUBMITTED, InventoryMovement, InventoryReceipt
from app.models.invoice import Invoice
from app.models.processing_log import ProcessingLog
from tests.integration.conftest import requires_db
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.integration.test_inventory_db import (  # noqa: F401 — fixture reuse
    ALE,
    LAGER,
    _clean_inventory,
    approve,
    approve_submission,
    received,
    submit,
)

pytestmark = requires_db


async def set_status(db_session, document_id, status):
    document = await db_session.get(Document, document_id)
    document.status = status
    await db_session.commit()


def refused(response, reason):
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["error_code"] == "ERR_INVOICE_INACTIVE" and error["detail"]["reason"] == reason, error


@pytest.fixture(params=[("BINNED", "in_recycle_bin"), ("REFERENCE", "reference_copy")], ids=["binned", "reference"])
def inactive(request):
    return request.param


class TestNothingChanges:
    async def test_every_change_export_and_retailed_mark_is_refused_and_nothing_is_written(
        self, api_client, db_session, received, inactive,  # noqa: F811
    ):
        status, reason = inactive
        iid = received.invoice_id
        before = (await api_client.get(f"/api/v1/invoices/{iid}")).json()["data"]
        await set_status(db_session, received.document_id, status)
        base = f"/api/v1/invoices/{iid}"
        attempts = [
            ("PATCH", f"{base}/items/1", {"quantity": 9, "corrected_by": "t", "note": "x"}),
            ("POST", f"{base}/items", {"description": "EXTRA", "quantity": 1, "unit_price": 1, "added_by": "t"}),
            ("DELETE", f"{base}/items/1", {"voided_by": "t", "note": "x"}),
            ("PATCH", f"{base}/date", {"invoice_date": "2026-10-01", "corrected_by": "t"}),
            ("PATCH", f"{base}/totals", {"subtotal": 1, "corrected_by": "t"}),
            ("POST", f"{base}/case-mappings", {"mappings": [{"item_code": "99900000001", "units_per_case": 12}]}),
            ("POST", f"{base}/retailed", None),
            ("PUT", f"{base}/reference-link", {"url": "https://example.com/x"}),
        ]
        for method, url, body in attempts:
            refused(await api_client.request(method, url, json=body), reason)
        for fmt in ("pdi", "csv"):
            refused(await api_client.get(f"{base}/export", params={"format": fmt}), reason)
        assert (await api_client.get(f"{base}/export", params={"format": "json"})).status_code == 200

        after = (await api_client.get(f"/api/v1/invoices/{iid}")).json()["data"]
        assert after["pdi_export_allowed"] is False and after["pdi_export_blocked_reason"]
        for key in ("line_items", "invoice_date", "subtotal", "correction_history"):
            assert after[key] == before[key], key


class TestNotCountedOrOffered:
    async def test_lists_counts_and_queues_leave_it_out_and_a_status_filter_still_finds_it(
        self, api_client, db_session, received, inactive,  # noqa: F811
    ):
        status, _ = inactive
        sid, iid = received.store_id, received.invoice_id
        vendor_id = (await db_session.execute(select(Invoice.vendor_id).where(Invoice.id == iid))).scalar()

        async def snapshot():
            listed = [r["invoice_id"] for r in (await api_client.get("/api/v1/invoices")).json()["items"]]
            dash = (await api_client.get("/api/v1/dashboard/summary")).json()["data"]
            queue = (await api_client.get("/api/v1/mapping-queue")).json()["items"]
            store_rows = (await api_client.get(f"/api/v1/inventory/stores/{sid}/invoices")).json()["items"]
            supply = (await api_client.get("/api/v1/inventory/supply")).json()["items"]
            vendor = ((await api_client.get(f"/api/v1/vendors/{vendor_id}")).json()["data"]["recent_invoices"]
                      if vendor_id else [])
            return {
                "listed": iid in listed, "total": dash["total_documents"],
                "recent": iid in [r["invoice_id"] for r in dash["recent"]],
                "queued": bool(queue),
                "store": iid in [r["invoice_id"] for r in store_rows],
                "supply": iid in [r["invoice_id"] for r in supply],
                "vendor": iid in [str(r["invoice_id"]) for r in vendor],
            }

        active = await snapshot()
        assert active["listed"] and active["store"] and active["supply"] and active["queued"]
        await set_status(db_session, received.document_id, status)
        gone = await snapshot()
        assert gone == {"listed": False, "total": active["total"] - 1, "recent": False, "queued": False,
                        "store": False, "supply": False, "vendor": False}, gone
        filtered = (await api_client.get("/api/v1/invoices", params={"status": status})).json()["items"]
        assert [r["invoice_id"] for r in filtered] == [iid]
        assert (await api_client.get(f"/api/v1/invoices/{iid}")).status_code == 200           # still readable


class TestGuardedOperations:
    """The remaining paths that would change lines, re-extract or receive an inactive invoice."""

    async def test_a_duplicate_row_decision_is_refused(self, api_client, db_session, received, inactive):  # noqa: F811
        status, reason = inactive
        before = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]["line_items"]
        await set_status(db_session, received.document_id, status)
        r = await api_client.post(f"/api/v1/invoices/{received.invoice_id}/items/1/duplicate-decision",
                                  json={"decision": "same_row", "decided_by": "t"})
        refused(r, reason)
        after = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]["line_items"]
        assert after == before

    async def test_reprocessing_is_refused(self, api_client, db_session, received, inactive):  # noqa: F811
        status, reason = inactive

        async def log_count():
            return (await db_session.execute(select(func.count()).select_from(ProcessingLog)
                                             .where(ProcessingLog.document_id == received.document_id))).scalar()

        before = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        await set_status(db_session, received.document_id, status)
        logs_before = await log_count()
        refused(await api_client.post(f"/api/v1/documents/{received.document_id}/reprocess"), reason)
        after = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        assert (after["line_items"], after["status"]) == (before["line_items"], before["status"])
        assert await log_count() == logs_before            # refused before any extraction step ran

    async def test_a_reference_copy_is_never_received_into_inventory(self, api_client, db_session, received):  # noqa: F811
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        preview = await submit(api_client, received.invoice_id)            # a pending (SUBMITTED) receipt
        await set_status(db_session, received.document_id, "REFERENCE")
        r = await approve_submission(api_client, received.invoice_id, preview)
        # Staleness is checked first, but it looks only at the invoice (status, Retailed mark, attested
        # values, store, lines), never the document — so the refusal is always receiving eligibility.
        assert r.status_code == 422, r.text
        detail = r.json()["error"]["detail"]
        assert detail["reason"] == "not_receivable" and "document_withdrawn" in detail["reasons"], detail
        db_session.expire_all()
        receipt = (await db_session.execute(select(InventoryReceipt)
                                            .where(InventoryReceipt.invoice_id == received.invoice_id))).scalars().one()
        assert receipt.status == RECEIPT_SUBMITTED                 # still pending: nothing was posted
        moved = (await db_session.execute(select(func.count()).select_from(InventoryMovement))).scalar()
        assert moved == 0
