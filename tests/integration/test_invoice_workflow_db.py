"""
tests/integration/test_invoice_workflow_db.py — reference link and Retailed, real API and Postgres.

  * the link: MANAGER+ only, https:// only (also enforced by a CHECK), who/when kept, removable;
  * Retailed: impossible without a ledger-recorded PDI export (ledger off -> never);
    allowed once the recorded file is the file the invoice produces now; refused
    twice, including under concurrent requests; shown as no longer current after
    a line correction or a mapping change, until exported again and re-marked;
    reopenable with a logged reason; never touches mappings or Product Master.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models.document import Document
from app.models.edi_export import EdiExport
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.processing_log import ProcessingLog
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_master import MasterCommercialMapping, MasterProduct
from app.models.store import Store
from app.models.user import UserRole
from tests.integration.conftest import USER_PASSWORD, USER_USERNAME, requires_db, user_id
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse

pytestmark = requires_db

CODE = "07777733333"
PRINTED = CODE + "3"


@pytest.fixture
async def corpus(db_session):
    # A migrated database always has alembic_version; the test database is built
    # by create_all and does not. The ledger reads it (and, without it, aborts the
    # export transaction — a pre-existing behaviour, reported, not changed here).
    await db_session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)"))
    await db_session.execute(text("INSERT INTO alembic_version VALUES ('0032') ON CONFLICT DO NOTHING"))
    store = Store(display_name="AF Workflow", identity_status="confirmed")
    db_session.add(store)
    await db_session.flush()
    document = Document(filename="wf.pdf", mime_type="application/pdf", file_size_bytes=1, file_path="test/wf.pdf",
                        file_hash=uuid.uuid4().hex, uploaded_by_user_id=user_id(UserRole.USER.value))
    db_session.add(document)
    await db_session.flush()
    invoice = Invoice(document_id=document.id, store_id=store.id, invoice_number="WF-1", status="VALIDATED",
                      vendor_name="Test Beverage", invoice_date=date(2026, 9, 1), currency="USD",
                      subtotal=Decimal("60.00"), grand_total=Decimal("60.00"))
    db_session.add(invoice)
    await db_session.flush()
    db_session.add(InvoiceItem(invoice_id=invoice.id, sort_order=0, description="BUD LIGHT 24/12OZ",
                               product_sku=PRINTED, pack_size="24/12OZ", quantity=Decimal("2"),
                               unit_price=Decimal("30.0000"), line_total=Decimal("60.00"), line_type="product"))
    db_session.add(ProductCaseMapping(store_id=store.id, item_code=CODE, units_per_case=24, source="APPROVED"))
    await db_session.commit()
    return {"invoice": str(invoice.id), "invoice_uuid": invoice.id, "document": document.id, "store": store.id}


@pytest.fixture
def ledger(monkeypatch):
    def set_ledger(on: bool):
        monkeypatch.setenv("EDI_EXPORT_LEDGER", "true" if on else "false")
        get_settings.cache_clear()
    yield set_ledger
    get_settings.cache_clear()


async def _detail(client, invoice_id):
    response = await client.get(f"/api/v1/invoices/{invoice_id}")
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _export(client, invoice_id):
    return await client.get(f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"})


async def _list_status(client, invoice_number):
    response = await client.get("/api/v1/invoices", params={"search": invoice_number})
    assert response.status_code == 200, response.text
    [row] = [r for r in response.json()["items"] if r["invoice_number"] == invoice_number]
    return row["completion_status"]


async def _as_user(app):
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    assert (await client.post("/api/v1/auth/login",
                              json={"username": USER_USERNAME, "password": USER_PASSWORD})).status_code == 200
    return client


class TestReferenceLink:
    async def test_manager_saves_who_and_when_and_can_remove_it(self, api_client, corpus, db_session):  # noqa: F811
        url = "https://chat.whatsapp.com/invite/abc?msg=50185"
        saved = await api_client.put(f"/api/v1/invoices/{corpus['invoice']}/reference-link", json={"url": url})
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["url"] == url
        link = (await _detail(api_client, corpus["invoice"]))["reference_link"]
        assert (link["url"], link["set_by"]) == (url, "testadmin") and link["set_at"]

        removed = await api_client.put(f"/api/v1/invoices/{corpus['invoice']}/reference-link", json={"url": None})
        assert removed.status_code == 200 and removed.json()["data"] is None
        assert (await _detail(api_client, corpus["invoice"]))["reference_link"] is None
        events = (await db_session.execute(select(ProcessingLog.payload).where(
            ProcessingLog.document_id == corpus["document"]))).scalars().all()
        assert [e["event"] for e in events if e and e.get("event", "").startswith("reference_link")] == [
            "reference_link_saved", "reference_link_removed"]

    async def test_only_https_and_never_credentials(self, api_client, corpus):  # noqa: F811
        for bad in ("http://example.com/x", "javascript:alert(1)", "https://u:p@example.com/x"):
            response = await api_client.put(f"/api/v1/invoices/{corpus['invoice']}/reference-link", json={"url": bad})
            assert response.status_code == 422, bad
        assert (await _detail(api_client, corpus["invoice"]))["reference_link"] is None

    async def test_a_user_can_see_but_not_save(self, app, api_client, corpus):  # noqa: F811
        await api_client.put(f"/api/v1/invoices/{corpus['invoice']}/reference-link",
                             json={"url": "https://example.com/inv"})
        user = await _as_user(app)
        try:
            refused = await user.put(f"/api/v1/invoices/{corpus['invoice']}/reference-link",
                                     json={"url": "https://example.com/other"})
            assert refused.status_code == 403
            assert (await _detail(user, corpus["invoice"]))["reference_link"]["url"] == "https://example.com/inv"
        finally:
            await user.aclose()

    async def test_the_database_refuses_a_non_https_value_too(self, corpus, db_session):
        with pytest.raises(IntegrityError, match="ck_invoices_reference_url_https"):
            await db_session.execute(text("UPDATE invoices SET reference_url = 'http://x' WHERE id = :id"),
                                     {"id": corpus["invoice_uuid"]})
        await db_session.rollback()


class TestRetailed:
    async def test_without_the_ledger_nothing_can_be_marked(self, api_client, corpus, ledger, db_session):  # noqa: F811
        ledger(False)
        assert (await _export(api_client, corpus["invoice"])).status_code == 200      # delivered, but not recorded
        completion = (await _detail(api_client, corpus["invoice"]))["completion"]
        assert (completion["status"], completion["can_mark"], completion["ledger_enabled"]) == ("not_retailed", False, False)
        assert "ledger is off" in completion["reason"]
        refused = await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")
        assert refused.status_code == 422
        assert await db_session.scalar(select(func.count()).select_from(EdiExport)) == 0

    async def test_mark_after_a_recorded_export_then_refuse_a_second_mark(self, api_client, corpus, ledger, db_session):  # noqa: F811
        ledger(True)
        before = (await _detail(api_client, corpus["invoice"]))["completion"]
        assert before["can_mark"] is False and "No delivered PDI file" in before["reason"]
        assert (await _export(api_client, corpus["invoice"])).status_code == 200
        ready = (await _detail(api_client, corpus["invoice"]))["completion"]
        assert (ready["can_mark"], ready["latest_export_number"]) == (True, 1)

        marked = await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")
        assert marked.status_code == 200, marked.text
        assert (marked.json()["data"]["status"], marked.json()["data"]["retailed_by"]) == ("retailed", "testadmin")
        assert await _list_status(api_client, "WF-1") == "retailed"
        again = await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")
        assert again.status_code == 422 and "Already marked" in again.text

        row = (await db_session.execute(select(Invoice).where(Invoice.id == corpus["invoice_uuid"]))).scalar_one()
        export = (await db_session.execute(select(EdiExport))).scalar_one()
        assert (row.retailed_edi_export_id, row.retailed_pdi_sha256) == (export.id, export.sha256)

    async def test_concurrent_marks_are_decided_once(self, api_client, corpus, ledger):  # noqa: F811
        ledger(True)
        await _export(api_client, corpus["invoice"])
        results = await asyncio.gather(*(api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")
                                         for _ in range(3)))
        assert sorted(r.status_code for r in results) == [200, 422, 422]

    async def test_a_line_correction_makes_it_no_longer_current_until_exported_again(self, api_client, corpus, ledger):  # noqa: F811
        ledger(True)
        await _export(api_client, corpus["invoice"])
        assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 200
        corrected = await api_client.patch(f"/api/v1/invoices/{corpus['invoice']}/items/0",
                                           json={"quantity": "3", "line_total": "90.00", "corrected_by": "testadmin"})
        assert corrected.status_code == 200, corrected.text
        stale = (await _detail(api_client, corpus["invoice"]))["completion"]
        assert (stale["status"], stale["can_mark"]) == ("retailed_stale", False)
        assert "changed after the last recorded export (#1)" in stale["reason"]
        assert await _list_status(api_client, "WF-1") == "retailed_stale"

        await _export(api_client, corpus["invoice"])
        assert (await _detail(api_client, corpus["invoice"]))["completion"]["can_mark"] is True
        assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 200
        assert await _list_status(api_client, "WF-1") == "retailed"

    async def test_a_mapping_change_makes_it_no_longer_current(self, api_client, corpus, ledger):  # noqa: F811
        ledger(True)
        await _export(api_client, corpus["invoice"])
        assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 200
        confirmed = await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/case-mappings",
                                          json={"mappings": [{"item_code": CODE, "units_per_case": 12}]})
        proposal = next(r["pending_proposal_id"] for r in confirmed.json()["data"]["case_mappings"] if r["item_code"] == CODE)
        assert (await api_client.post(f"/api/v1/proposals/{proposal}/approve", json={"note": "t"})).status_code == 200
        assert (await _detail(api_client, corpus["invoice"]))["completion"]["status"] == "retailed_stale"

    async def test_reopen_is_explicit_logged_and_manager_only(self, app, api_client, corpus, ledger, db_session):  # noqa: F811
        ledger(True)
        await _export(api_client, corpus["invoice"])
        user = await _as_user(app)
        try:
            assert (await user.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 403
            assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 200
            assert (await user.post(f"/api/v1/invoices/{corpus['invoice']}/retailed/reopen", json={})).status_code == 403
        finally:
            await user.aclose()
        reopened = await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed/reopen",
                                         json={"note": "credit note arrived"})
        assert reopened.status_code == 200 and reopened.json()["data"]["status"] == "not_retailed"
        assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed/reopen", json={})).status_code == 422
        events = (await db_session.execute(select(ProcessingLog.payload).where(
            ProcessingLog.document_id == corpus["document"]))).scalars().all()
        assert [e["event"] for e in events if e and "retail" in e.get("event", "")] == [
            "invoice_retailed", "invoice_retail_reopened"]
        assert next(e for e in events if e and e.get("event") == "invoice_retail_reopened")["note"] == "credit note arrived"

    async def test_no_mapping_or_product_master_side_effects(self, api_client, corpus, ledger, db_session):  # noqa: F811
        ledger(True)

        async def counts():
            return tuple([await db_session.scalar(select(func.count()).select_from(model))
                          for model in (ProductCaseMapping, MasterCommercialMapping, MasterProduct)])
        before = await counts()
        await _export(api_client, corpus["invoice"])
        await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")
        await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed/reopen", json={})
        assert await counts() == before
        mapping = (await db_session.execute(select(ProductCaseMapping))).scalar_one()
        assert mapping.units_per_case == 24

    async def test_a_blocked_export_records_nothing_and_cannot_be_marked(self, api_client, corpus, ledger, db_session):  # noqa: F811
        ledger(True)
        await db_session.execute(text("DELETE FROM product_case_mappings"))       # the test database only
        await db_session.commit()
        assert (await _export(api_client, corpus["invoice"])).status_code == 422
        assert await db_session.scalar(select(func.count()).select_from(EdiExport)) == 0
        completion = (await _detail(api_client, corpus["invoice"]))["completion"]
        assert completion["can_mark"] is False and "cannot be exported now" in completion["reason"]
