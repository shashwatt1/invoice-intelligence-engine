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
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.models.document import Document
from app.models.edi_export import EdiExport
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.processing_log import LogStatus, PipelineStage, ProcessingLog
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_master import MasterCommercialMapping, MasterProduct
from app.models.store import Store
from app.models.user import UserRole
from app.repositories.invoice_repository import InvoiceRepository
from app.services.invoice_workflow_service import mark_retailed, reopen_retailed
from tests.integration.conftest import (
    ADMIN_USERNAME,
    MANAGER_USERNAME,
    USER_PASSWORD,
    USER_USERNAME,
    requires_db,
    user_id,
)
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse

pytestmark = requires_db

CODE = "07777733333"
PRINTED = CODE + "3"


@pytest.fixture
async def corpus(db_session):
    # A migrated database always has alembic_version; the test database is built
    # by create_all and does not. The ledger reads it (and, without it, aborts the
    # export transaction — a pre-existing behaviour, reported, not changed here).
    # Created for each test and dropped afterwards, in the test database only.
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
    # The pipeline's validation entry: revalidation after a correction carries
    # its OCR/AI confidence forward, so a consistent correction comes back
    # VALIDATED and an inconsistent one REVIEW_REQUIRED, as in production.
    db_session.add(ProcessingLog(document_id=document.id, stage=PipelineStage.VALIDATION, status=LogStatus.SUCCESS,
                                 payload={"confidence": {"ocr_confidence": 0.99, "ai_confidence": 0.99}}))
    await db_session.commit()
    yield {"invoice": str(invoice.id), "invoice_uuid": invoice.id, "document": document.id, "store": store.id}
    await db_session.rollback()
    await db_session.execute(text("DROP TABLE IF EXISTS alembic_version"))   # the test database only
    await db_session.commit()


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


async def _set_status(db_session, corpus, status):
    """Set the invoice's validation status directly (test database only), to isolate one rule at a time."""
    await db_session.execute(text("UPDATE invoices SET status = :s WHERE id = :i"),
                             {"s": status, "i": corpus["invoice_uuid"]})
    await db_session.commit()


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
        for bad in ("http://example.com/x", "HTTP://example.com/x", "javascript:alert(1)", "JavaScript:alert(1)",
                    "ftp://example.com/x", "data:text/html,x", "https://u:p@example.com/x", "https:///no-host"):
            response = await api_client.put(f"/api/v1/invoices/{corpus['invoice']}/reference-link", json={"url": bad})
            assert response.status_code == 422, (bad, response.status_code, response.text)
        assert (await _detail(api_client, corpus["invoice"]))["reference_link"] is None

    async def test_any_case_of_https_is_saved_with_a_lower_case_scheme(self, api_client, corpus):  # noqa: F811
        for given, stored in (("https://chat.example.com/m/50185", "https://chat.example.com/m/50185"),
                              ("HTTPS://Chat.Example.com/M/50185?x=Y", "https://Chat.Example.com/M/50185?x=Y"),
                              ("Https://example.com/a", "https://example.com/a")):
            response = await api_client.put(f"/api/v1/invoices/{corpus['invoice']}/reference-link", json={"url": given})
            assert response.status_code == 200, (given, response.status_code, response.text)
            assert response.json()["data"]["url"] == stored
            assert (await _detail(api_client, corpus["invoice"]))["reference_link"]["url"] == stored

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
        assert row.retailed_edi_export_id == export.id
        assert len(row.retailed_attestation_sha256) == 64 and row.retailed_attestation_sha256 != export.sha256

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
        assert "must be VALIDATED" in stale["reason"]           # subtotal no longer matches the lines
        assert await _list_status(api_client, "WF-1") == "retailed_stale"

        totals = await api_client.patch(f"/api/v1/invoices/{corpus['invoice']}/totals",
                                        json={"subtotal": "90.00", "grand_total": "90.00", "corrected_by": "x"})
        assert totals.status_code == 200, totals.text
        revalidated = (await _detail(api_client, corpus["invoice"]))
        assert revalidated["status"] == "VALIDATED", revalidated.get("review_reasons")
        assert "changed after the last recorded export (#1)" in revalidated["completion"]["reason"]

        await _export(api_client, corpus["invoice"])
        assert (await _detail(api_client, corpus["invoice"]))["completion"]["can_mark"] is True
        assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 200
        assert await _list_status(api_client, "WF-1") == "retailed"

    async def test_a_review_required_invoice_cannot_be_marked_even_with_a_recorded_export(
        self, api_client, corpus, ledger, db_session,  # noqa: F811
    ):
        ledger(True)
        await _set_status(db_session, corpus, "REVIEW_REQUIRED")
        assert (await _export(api_client, corpus["invoice"])).status_code == 200      # export is still allowed
        completion = (await _detail(api_client, corpus["invoice"]))["completion"]
        assert completion["can_mark"] is False and "must be VALIDATED" in completion["reason"]
        refused = await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")
        assert refused.status_code == 422 and "must be VALIDATED" in refused.text

    async def test_leaving_validated_makes_it_stale_and_returning_restores_it(
        self, api_client, corpus, ledger, db_session,  # noqa: F811
    ):
        ledger(True)
        await _export(api_client, corpus["invoice"])
        assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 200
        await _set_status(db_session, corpus, "REVIEW_REQUIRED")
        assert (await _detail(api_client, corpus["invoice"]))["completion"]["status"] == "retailed_stale"
        await _set_status(db_session, corpus, "VALIDATED")
        assert (await _detail(api_client, corpus["invoice"]))["completion"]["status"] == "retailed"

    @pytest.mark.parametrize("path, body", [
        ("items/0", {"line_total": "60.01"}),                          # a line total only (not in the PDI file)
        ("items/0", {"unit_discount": "0.50"}),                        # a line discount
        ("items/0", {"unit_deposit": "0.10"}),                         # a line deposit
        ("totals", {"subtotal": "60.01", "corrected_by": "x"}),        # the subtotal only
        ("totals", {"discount_amount": "1.00", "corrected_by": "x"}),  # an invoice discount
        ("totals", {"deposit_total": "1.20", "corrected_by": "x"}),    # an invoice deposit total
    ])
    async def test_attested_values_outside_the_pdi_file_still_make_it_stale(
        self, api_client, corpus, ledger, db_session, path, body,  # noqa: F811
    ):
        ledger(True)
        await _export(api_client, corpus["invoice"])
        assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 200
        response = await api_client.patch(f"/api/v1/invoices/{corpus['invoice']}/{path}", json=body)
        assert response.status_code == 200, response.text
        await _set_status(db_session, corpus, "VALIDATED")     # isolate the fingerprint from the status gate
        completion = (await _detail(api_client, corpus["invoice"]))["completion"]
        assert completion["status"] == "retailed_stale", body
        assert await _list_status(api_client, "WF-1") == "retailed_stale"

    async def test_a_reference_link_change_does_not_affect_it(self, api_client, corpus, ledger):  # noqa: F811
        ledger(True)
        await _export(api_client, corpus["invoice"])
        assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 200
        await api_client.put(f"/api/v1/invoices/{corpus['invoice']}/reference-link", json={"url": "https://x.example/1"})
        assert (await _detail(api_client, corpus["invoice"]))["completion"]["status"] == "retailed"

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


def _actors():
    """The seeded accounts of the current test (ids are re-seeded per test)."""
    return (SimpleNamespace(id=user_id(UserRole.MANAGER.value), username=MANAGER_USERNAME),
            SimpleNamespace(id=user_id(UserRole.ADMIN.value), username=ADMIN_USERNAME))


async def _waiting_on_lock(probe: AsyncSession, timeout: float = 5.0) -> None:
    """Wait until some backend of this test database is blocked on a lock (deterministic interleaving)."""
    for _ in range(int(timeout / 0.05)):
        waiting = await probe.scalar(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"))
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the competing session never waited on the invoice lock")


async def _events(session: AsyncSession, document_id) -> list[tuple[str, str]]:
    rows = (await session.execute(select(ProcessingLog.payload).where(ProcessingLog.document_id == document_id)
                                  .order_by(ProcessingLog.created_at, ProcessingLog.id))).scalars().all()
    return [(p["event"], p["by"]) for p in rows if p and "retail" in p.get("event", "")]


class TestRetailedConcurrency:
    """Two real database sessions, genuinely interleaved on the invoice row lock."""

    @pytest.fixture
    def sessions(self, db_engine):
        return async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)

    async def test_only_the_first_of_two_interleaved_marks_wins(self, api_client, corpus, ledger, sessions):  # noqa: F811
        manager, admin = _actors()
        ledger(True)
        assert (await _export(api_client, corpus["invoice"])).status_code == 200
        invoice_id = corpus["invoice_uuid"]
        async with sessions() as first, sessions() as second, sessions() as probe:
            # 'second' holds a stale copy loaded before the lock, kept alive as a request would keep it
            # (an unreferenced copy may be garbage-collected and reloaded, which would hide the race).
            stale = await InvoiceRepository(second).get_detail(invoice_id)
            assert stale.retailed_at is None
            won = await mark_retailed(first, invoice_id, manager)            # holds the row lock, not committed
            assert won.status == "retailed"
            competing = asyncio.create_task(mark_retailed(second, invoice_id, admin))
            await _waiting_on_lock(probe)
            assert not competing.done()
            await first.commit()
            with pytest.raises(ValidationError, match="Already marked"):
                await competing
            await second.rollback()

            row = (await probe.execute(select(Invoice.retailed_by, Invoice.retailed_at)
                                       .where(Invoice.id == invoice_id))).one()
            assert (row.retailed_by, row.retailed_at) == (MANAGER_USERNAME, won.retailed_at)
            assert await _events(probe, corpus["document"]) == [("invoice_retailed", MANAGER_USERNAME)]
            assert stale is not None

    async def test_a_mark_waiting_behind_a_reopen_decides_on_the_reopened_state(self, api_client, corpus, ledger, sessions):  # noqa: F811
        manager, admin = _actors()
        ledger(True)
        await _export(api_client, corpus["invoice"])
        assert (await api_client.post(f"/api/v1/invoices/{corpus['invoice']}/retailed")).status_code == 200
        invoice_id = corpus["invoice_uuid"]
        async with sessions() as first, sessions() as second, sessions() as probe:
            stale = await InvoiceRepository(second).get_detail(invoice_id)  # stale, kept alive: sees it Retailed
            assert stale.retailed_at is not None
            reopened = await reopen_retailed(first, invoice_id, manager, "credit note")
            assert reopened.status == "not_retailed"
            competing = asyncio.create_task(mark_retailed(second, invoice_id, admin))
            await _waiting_on_lock(probe)
            await first.commit()
            marked = await competing                                         # sees the reopen, so it may mark again
            assert (marked.status, marked.retailed_by) == ("retailed", ADMIN_USERNAME)
            await second.commit()
            assert await _events(probe, corpus["document"]) == [
                ("invoice_retailed", ADMIN_USERNAME), ("invoice_retail_reopened", MANAGER_USERNAME),
                ("invoice_retailed", ADMIN_USERNAME)]
            assert stale is not None

    async def test_a_reopen_from_a_stale_session_sees_the_committed_mark(self, api_client, corpus, ledger, sessions):  # noqa: F811
        manager, admin = _actors()
        ledger(True)
        await _export(api_client, corpus["invoice"])
        invoice_id = corpus["invoice_uuid"]
        async with sessions() as stale, sessions() as other, sessions() as probe:
            cached = await InvoiceRepository(stale).get_detail(invoice_id)  # cached before the mark, kept alive
            assert cached.retailed_at is None
            await mark_retailed(other, invoice_id, manager)
            await other.commit()
            reopened = await reopen_retailed(stale, invoice_id, admin, "wrong invoice")
            assert reopened.status == "not_retailed"                         # not refused as "not marked"
            await stale.commit()
            row = (await probe.execute(select(Invoice.retailed_at).where(Invoice.id == invoice_id))).one()
            assert row.retailed_at is None
            assert cached is not None
