"""
tests/integration/test_inventory_db.py — store inventory against real Postgres.

An invoice processed through the real pipeline (fake LLM) to a physical
store, its units per case proposed and approved as the review UI does, then
received through the inventory API:

  * one product without an approved conversion blocks the whole receipt —
    nothing is written; the charge and the shorted line never block;
  * a discrepancy needs a reason; the accepted quantity and the conversion
    snapshot are what the ledger records; the invoice itself is untouched;
  * an invoice is received once — the API refuses a second receipt and the
    database refuses a duplicate idempotency key or a wrong-signed movement;
  * balances are per store: a source identity is refused, another physical
    store sees nothing, and on-hand exists only after an opening count;
  * a POSTED receipt locks its invoice: every line, store and document change
    is refused (409) until the receipt is reversed; reversing compensates every
    movement, keeps the history, is idempotent, and frees the invoice to be
    corrected and received again; a failure part-way leaves nothing behind;
    a correction waits on the same row lock a receipt holds.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.inventory import InventoryMovement, InventoryReceipt
from app.models.invoice_item import InvoiceItem
from app.repositories.store_repository import StoreRepository
from app.schemas.extraction import ExtractedLineItem
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import TEST_URL, approve_all_pending, requires_db, store_id
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db

STORE = "86357232"
LAGER, ALE, SHORTED = "999000000015", "999000000022", "999000000039"
LAGER_CODE, ALE_CODE = "99900000001", "99900000002"


@pytest.fixture
async def received(api_client, app, db_session, monkeypatch):  # noqa: F811
    """A VALIDATED invoice at a physical store, inventory switched on."""
    from app.api.v1 import inventory as inventory_api
    from app.api.v1.invoices import get_pipeline

    monkeypatch.setattr(inventory_api, "get_settings", lambda: SimpleNamespace(inventory_enabled=True))
    store = await StoreRepository(db_session).get(store_id(STORE))
    store.display_name = "AF NV Midler"                      # a named location: physical
    await db_session.commit()

    invoice = extracted_invoice(
        line_items=[
            ExtractedLineItem(description="NORTHWIND LAGER 12PK", product_code=LAGER, quantity=3.0,
                              unit_price=21.95, line_total=65.85),
            ExtractedLineItem(description="EASTGATE ALE 6PK", product_code=ALE, quantity=2.0,
                              unit_price=10.0, line_total=20.0),
            ExtractedLineItem(description="SOLD OUT STOUT", product_code=SHORTED, quantity=0.0,
                              unit_price=12.0, line_total=0.0),
            ExtractedLineItem(line_type="charge", description="FUEL SURCHARGE", quantity=1.0,
                              unit_price=5.0, line_total=5.0),
        ],
        subtotal=90.85, grand_total=90.85,
    )
    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(invoice))
    accepted = await process_file(api_client, content=build_pdf(["inventory invoice " + "pad " * 300]),
                                  filename="inventory.pdf", store=STORE)
    status = (await api_client.get(accepted["status_url"])).json()["data"]
    detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
    assert detail["status"] == "VALIDATED", detail["status"]
    return SimpleNamespace(invoice_id=status["invoice_id"], store_id=store_id(STORE))


async def approve(api_client, db_session, invoice_id, code, units):  # noqa: F811
    r = await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                              json={"mappings": [{"item_code": code, "units_per_case": units}]})
    assert r.status_code == 200, r.text
    await approve_all_pending(db_session)


async def ledger_counts(db_session):
    return ((await db_session.execute(select(func.count()).select_from(InventoryReceipt))).scalar(),
            (await db_session.execute(select(func.count()).select_from(InventoryMovement))).scalar())


def acceptance(lines, overrides=None, reasons=None):
    overrides, reasons = overrides or {}, reasons or {}
    return [{"invoice_item_id": line["invoice_item_id"],
             "accepted_units": overrides.get(line["item_code"], line["expected_units"]),
             "reason": reasons.get(line["item_code"])} for line in lines]


class TestReceivingAnInvoice:
    async def test_an_unresolved_conversion_blocks_the_whole_receipt(self, api_client, db_session, received):  # noqa: F811
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        url = f"/api/v1/inventory/invoices/{received.invoice_id}/receipt"
        preview = (await api_client.get(url)).json()["data"]
        assert preview["can_post"] is False and preview["eligibility_reasons"] == []
        assert [(b["item_code"], b["description"]) for b in preview["blockers"]] == [(ALE_CODE, "EASTGATE ALE 6PK")]
        assert [line["item_code"] for line in preview["lines"]] == [LAGER_CODE]      # charge and shorted: not lines

        r = await api_client.post(url, json={"received_on": "2026-10-09", "lines": acceptance(preview["lines"])})
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "unresolved_conversions"
        assert await ledger_counts(db_session) == (0, 0)

    async def test_accepted_quantities_and_the_conversion_are_recorded_once(self, api_client, db_session, received):  # noqa: F811
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        url = f"/api/v1/inventory/invoices/{received.invoice_id}/receipt"
        preview = (await api_client.get(url)).json()["data"]
        assert preview["can_post"] is True
        assert [(x["item_code"], Decimal(x["cases"]), x["units_per_case"], Decimal(x["expected_units"]))
                for x in preview["lines"]] == [(LAGER_CODE, 3, 12, 36), (ALE_CODE, 2, 6, 12)]

        no_reason = {"received_on": "2026-10-09", "lines": acceptance(preview["lines"], {LAGER_CODE: 34})}
        r = await api_client.post(url, json=no_reason)
        assert r.status_code == 422 and "a reason is required" in str(r.json()["error"]["detail"])
        assert await ledger_counts(db_session) == (0, 0)

        body = {"received_on": "2026-10-09", "note": "checked in by the night manager",
                "lines": acceptance(preview["lines"], {LAGER_CODE: 34}, {LAGER_CODE: "2 cans crushed"})}
        r = await api_client.post(url, json=body)
        assert r.status_code == 201, r.text
        receipt = r.json()["data"]["receipt"]
        assert (receipt["confirmed_by"], receipt["confirmed_role"], receipt["line_count"],
                receipt["discrepancy_count"]) == ("testadmin", "ADMIN", 2, 1)

        movements = (await db_session.execute(
            select(InventoryMovement).order_by(InventoryMovement.item_code))).scalars().all()
        assert [(m.item_code, m.movement_type, m.quantity, m.expected_quantity, m.source_quantity, m.source_unit,
                 m.units_per_case, m.conversion_path, m.reason, m.store_id, m.business_date)
                for m in movements] == [
            (LAGER_CODE, "RECEIPT", 34, 36, 3, "CASE", 12, "LEGACY_FALLBACK", "2 cans crushed",
             received.store_id, date(2026, 10, 9)),
            (ALE_CODE, "RECEIPT", 12, 12, 2, "CASE", 6, "LEGACY_FALLBACK", None, received.store_id,
             date(2026, 10, 9)),
        ]
        assert all(m.idempotency_key == f"receipt:{m.receipt_id}:{m.invoice_item_id}" for m in movements)

        # The invoice is the invoice: receiving changed none of its lines.
        quantities = (await db_session.execute(
            select(InvoiceItem.product_sku, InvoiceItem.quantity)
            .where(InvoiceItem.invoice_id == uuid.UUID(received.invoice_id)))).all()
        assert {sku: q for sku, q in quantities if sku}[LAGER] == 3

        again = await api_client.post(url, json=body)
        assert again.status_code == 422 and again.json()["error"]["detail"]["reason"] == "already_received"
        assert await ledger_counts(db_session) == (1, 2)

        listed = (await api_client.get(f"/api/v1/inventory/stores/{received.store_id}/invoices")).json()
        assert listed["items"][0]["receipt"]["id"] == receipt["id"]
        assert listed["items"][0]["product_lines"] == 2

    async def test_the_database_refuses_a_duplicate_key_and_a_wrong_sign(self, db_session, received):
        base = {"store_id": received.store_id, "item_code": LAGER_CODE, "base_unit": "SELLABLE_UNIT",
                    "business_date": date(2026, 10, 9), "actor_username": "testadmin"}
        db_session.add(InventoryMovement(movement_type="RECEIPT", quantity=Decimal(5), idempotency_key="k1", **base))
        await db_session.commit()
        db_session.add(InventoryMovement(movement_type="RECEIPT", quantity=Decimal(5), idempotency_key="k1", **base))
        with pytest.raises(IntegrityError, match="uq_inventory_movements_idempotency"):
            await db_session.commit()
        await db_session.rollback()
        db_session.add(InventoryMovement(movement_type="RECEIPT", quantity=Decimal(-5), idempotency_key="k2", **base))
        with pytest.raises(IntegrityError, match="ck_inventory_movements_sign"):
            await db_session.commit()
        await db_session.rollback()
        db_session.add(InventoryMovement(movement_type="SALE", quantity=Decimal(5), idempotency_key="k3", **base))
        with pytest.raises(IntegrityError, match="ck_inventory_movements_sign"):
            await db_session.commit()
        await db_session.rollback()


class TestStoreBalances:
    async def test_balances_are_per_physical_store_and_need_an_opening_count(
        self, api_client, db_session, received,  # noqa: F811
    ):
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        url = f"/api/v1/inventory/invoices/{received.invoice_id}/receipt"
        preview = (await api_client.get(url)).json()["data"]
        r = await api_client.post(url, json={"received_on": "2026-10-09", "lines": acceptance(preview["lines"])})
        assert r.status_code == 201, r.text

        items_url = f"/api/v1/inventory/stores/{received.store_id}/items"
        rows = {i["item_code"]: i for i in (await api_client.get(items_url)).json()["items"]}
        assert {c: (r["status"], r["on_hand"], Decimal(r["received"])) for c, r in rows.items()} == {
            LAGER_CODE: ("OPENING_STOCK_REQUIRED", None, 36), ALE_CODE: ("OPENING_STOCK_REQUIRED", None, 12)}

        counts_url = f"/api/v1/inventory/stores/{received.store_id}/opening-counts"
        body = {"count_date": "2026-10-01", "reason": "full shelf count, two people",
                "entries": [{"item_code": "999000000015", "quantity": 10, "description": "NORTHWIND LAGER 12PK"}]}
        r = await api_client.post(counts_url, json=body)
        assert r.status_code == 201 and r.json()["data"] == {"recorded": 1}
        again = await api_client.post(counts_url, json=body)
        assert again.status_code == 422 and LAGER_CODE in again.json()["error"]["detail"]["entries"]

        rows = {i["item_code"]: i for i in (await api_client.get(items_url)).json()["items"]}
        assert (rows[LAGER_CODE]["status"], Decimal(rows[LAGER_CODE]["on_hand"])) == ("IN_STOCK", 46)
        assert rows[ALE_CODE]["on_hand"] is None

        overview = (await api_client.get(f"/api/v1/inventory/stores/{received.store_id}/overview")).json()["data"]
        assert (overview["tracked_products"], overview["opening_required"], overview["in_stock"]) == (2, 1, 1)
        assert len(overview["recent_receipts"]) == 1

        history = (await api_client.get(f"{items_url}/{LAGER_CODE}/movements")).json()["data"]
        assert [(h["movement_type"], Decimal(h["quantity"])) for h in history] == [("OPENING_COUNT", 10), ("RECEIPT", 36)]
        assert history[1]["invoice_number"] == "INV-001"

        source_identity = await api_client.get(f"/api/v1/inventory/stores/{store_id('47708760')}/items")
        assert source_identity.status_code == 422
        assert source_identity.json()["error"]["detail"]["reason"] == "source_identity"

        other = await StoreRepository(db_session).create(display_name="AF Other Street")
        await db_session.commit()
        assert (await api_client.get(f"/api/v1/inventory/stores/{other.id}/items")).json()["total"] == 0


async def receive(api_client, invoice_id, overrides=None, reasons=None):  # noqa: F811
    url = f"/api/v1/inventory/invoices/{invoice_id}/receipt"
    preview = (await api_client.get(url)).json()["data"]
    r = await api_client.post(url, json={"received_on": "2026-10-09",
                                         "lines": acceptance(preview["lines"], overrides, reasons)})
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def received_and_mapped(api_client, db_session, received):  # noqa: F811
    await approve(api_client, db_session, received.invoice_id, LAGER, 12)
    await approve(api_client, db_session, received.invoice_id, ALE, 6)
    return (await receive(api_client, received.invoice_id))["receipt"]


async def balances(db_session, store):
    rows = (await db_session.execute(
        select(InventoryMovement.item_code, func.sum(InventoryMovement.quantity))
        .where(InventoryMovement.store_id == store).group_by(InventoryMovement.item_code)
    )).all()
    return dict(rows)


def refused_as_received(response):
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["error_code"] == "ERR_INVOICE_RECEIVED" and error["detail"]["reason"] == "invoice_received"
    return error["detail"]["action"]


class TestCorrectionsAfterReceipt:
    async def test_every_change_to_a_received_invoice_is_refused_and_nothing_moves(
        self, api_client, db_session, received,  # noqa: F811
    ):
        await received_and_mapped(api_client, db_session, received)
        base = f"/api/v1/invoices/{received.invoice_id}"
        before = (await api_client.get(base)).json()["data"]
        other = await StoreRepository(db_session).create(display_name="AF Other Street")
        await db_session.commit()

        attempts = {
            "correct_line_item": api_client.patch(f"{base}/items/0", json={"quantity": 2, "corrected_by": "m"}),
            "add_line_item": api_client.post(f"{base}/items", json={"description": "X", "quantity": 1, "added_by": "m"}),
            "void_line_item": api_client.request("DELETE", f"{base}/items/1", json={"voided_by": "m"}),
            "duplicate_decision": api_client.post(f"{base}/items/1/duplicate-decision",
                                                  json={"decision": "same_row", "decided_by": "m"}),
            "assign_store": api_client.post(f"{base}/assign-store", json={"store_id": str(other.id),
                                                                          "assigned_by": "m"}),
            "delete_invoice": api_client.delete(base),
            "move_to_bin": api_client.post(f"/api/v1/documents/{before['document_id']}/move-to-bin"),
            "reprocess": api_client.post(f"/api/v1/documents/{before['document_id']}/reprocess"),
        }
        for action, request in attempts.items():
            response = await request
            if response.status_code == 422 and action == "duplicate_decision":
                continue          # the row is not flagged as a duplicate: refused earlier, also unchanged
            assert refused_as_received(response) == action

        after = (await api_client.get(base)).json()["data"]
        assert after["line_items"] == before["line_items"]
        assert (after["status"], after["store"]["id"]) == (before["status"], before["store"]["id"])
        document = (await api_client.get(f"/api/v1/documents/{before['document_id']}")).json()["data"]
        assert document["status"] != "BINNED"
        assert await ledger_counts(db_session) == (1, 2)

    async def test_a_upc_correction_is_refused_too_and_nothing_changes(
        self, api_client, db_session, received,  # noqa: F811
    ):
        # UPC entry (missing or cut-off codes) goes through the same line correction, so a
        # POSTED receipt locks it like any other change; a valid UPC is refused, not applied.
        await received_and_mapped(api_client, db_session, received)
        base = f"/api/v1/invoices/{received.invoice_id}"
        before = (await api_client.get(base)).json()["data"]["line_items"][0]
        response = await api_client.patch(f"{base}/items/0", json={"product_code": "012000126300"})
        assert refused_as_received(response) == "correct_line_item"
        after = (await api_client.get(base)).json()["data"]["line_items"][0]
        assert after["product_code"] == before["product_code"]
        assert after["correction_history"] == before["correction_history"]
        assert await ledger_counts(db_session) == (1, 2)

    async def test_the_date_and_totals_stay_correctable(self, api_client, db_session, received):  # noqa: F811
        await received_and_mapped(api_client, db_session, received)
        r = await api_client.patch(f"/api/v1/invoices/{received.invoice_id}/date",
                                   json={"invoice_date": "2026-01-16", "corrected_by": "m"})
        assert r.status_code == 200, r.text


class TestReversingAReceipt:
    async def test_reversal_compensates_every_movement_and_keeps_the_history(
        self, api_client, db_session, received,  # noqa: F811
    ):
        receipt = await received_and_mapped(api_client, db_session, received)
        url = f"/api/v1/inventory/invoices/{received.invoice_id}/receipt/reverse"
        assert (await api_client.post(url, json={"receipt_id": receipt["id"], "reason": ""})).status_code == 422

        r = await api_client.post(url, json={"receipt_id": receipt["id"], "reason": "lager line misread: 2 cases"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert (data["movements_reversed"], data["already_reversed"]) == (2, False)
        assert (data["receipt"]["status"], data["receipt"]["reversed_by"],
                data["receipt"]["reversal_reason"]) == ("REVERSED", "testadmin", "lager line misread: 2 cases")

        movements = (await db_session.execute(select(InventoryMovement))).scalars().all()
        originals = {m.id: m for m in movements if m.reversal_of_id is None}
        reversals = [m for m in movements if m.reversal_of_id is not None]
        assert len(originals) == 2 and len(reversals) == 2
        for m in reversals:
            source = originals[m.reversal_of_id]
            assert (m.quantity, m.business_date, m.item_code, m.store_id) == (
                -source.quantity, source.business_date, source.item_code, source.store_id)
            assert m.idempotency_key == f"reversal:{source.id}"
        assert sorted(o.quantity for o in originals.values()) == [12, 36]      # originals untouched
        assert await balances(db_session, received.store_id) == {LAGER_CODE: 0, ALE_CODE: 0}

        again = await api_client.post(url, json={"receipt_id": receipt["id"], "reason": "double click"})
        assert again.status_code == 200 and again.json()["data"]["already_reversed"] is True
        assert again.json()["data"]["receipt"]["reversal_reason"] == "lager line misread: 2 cases"
        assert await ledger_counts(db_session) == (1, 4)

        preview = (await api_client.get(f"/api/v1/inventory/invoices/{received.invoice_id}/receipt")).json()["data"]
        assert preview["receipt"] is None and [h["status"] for h in preview["history"]] == ["REVERSED"]
        assert preview["can_post"] is True

    async def test_after_reversal_the_invoice_is_corrected_and_received_again(
        self, api_client, db_session, received,  # noqa: F811
    ):
        receipt = await received_and_mapped(api_client, db_session, received)
        base = f"/api/v1/invoices/{received.invoice_id}"
        await api_client.post(f"/api/v1/inventory/invoices/{received.invoice_id}/receipt/reverse",
                              json={"receipt_id": receipt["id"], "reason": "lager was 2 cases"})

        r = await api_client.patch(f"{base}/items/0", json={"quantity": 2, "line_total": 43.90, "corrected_by": "m"})
        assert r.status_code == 200, r.text
        r = await api_client.patch(f"{base}/totals", json={"subtotal": 68.90, "grand_total": 68.90, "corrected_by": "m"})
        assert r.status_code == 200, r.text
        assert (await api_client.get(base)).json()["data"]["status"] == "VALIDATED"

        second = await receive(api_client, received.invoice_id)
        assert second["receipt"]["status"] == "POSTED" and second["receipt"]["id"] != receipt["id"]
        assert [h["status"] for h in second["history"]] == ["REVERSED", "POSTED"]
        assert await balances(db_session, received.store_id) == {LAGER_CODE: 24, ALE_CODE: 12}
        refused_as_received(await api_client.patch(f"{base}/items/0", json={"quantity": 3, "corrected_by": "m"}))

        old = await api_client.post(f"/api/v1/inventory/invoices/{received.invoice_id}/receipt/reverse",
                                    json={"receipt_id": receipt["id"], "reason": "stale tab"})
        assert old.json()["data"]["already_reversed"] is True          # the old receipt; the new one stands
        assert await balances(db_session, received.store_id) == {LAGER_CODE: 24, ALE_CODE: 12}

    async def test_a_failure_part_way_leaves_no_partial_receipt_or_reversal(
        self, api_client, db_session, received, monkeypatch,  # noqa: F811
    ):
        from app.services import inventory_service

        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        url = f"/api/v1/inventory/invoices/{received.invoice_id}/receipt"
        preview = (await api_client.get(url)).json()["data"]
        with monkeypatch.context() as broken:
            # The receipt row flushes, then the movements fail (source_unit is VARCHAR(16)).
            broken.setattr(inventory_service, "SOURCE_UNIT_CASE", "C" * 40)
            with pytest.raises(DBAPIError, match="value too long"):
                await api_client.post(url, json={"received_on": "2026-10-09", "lines": acceptance(preview["lines"])})
        assert await ledger_counts(db_session) == (0, 0)

        receipt = (await receive(api_client, received.invoice_id))["receipt"]
        with monkeypatch.context() as broken:
            # Compensating movements are added, then the receipt's new status fails its CHECK.
            broken.setattr(inventory_service, "RECEIPT_REVERSED", "BROKEN")
            with pytest.raises(IntegrityError, match="ck_inventory_receipts_status"):
                await api_client.post(f"{url}/reverse", json={"receipt_id": receipt["id"], "reason": "x"})
        db_session.expire_all()
        assert await ledger_counts(db_session) == (1, 2)
        status = (await db_session.execute(select(InventoryReceipt.status))).scalar()
        assert status == "POSTED"


class TestTheInvoiceLock:
    async def test_a_correction_waits_for_a_receipt_in_progress_then_sees_it(self, db_session, received):
        """Two connections: the receipt holds the invoice row; the correction cannot slip in between."""
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.exceptions import InvoiceReceivedError
        from app.services import inventory_service

        invoice_id = uuid.UUID(received.invoice_id)
        engine = create_async_engine(TEST_URL, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as receiving, AsyncSession(engine) as correcting:
                await inventory_service.lock_invoice(receiving, invoice_id)        # a receipt mid-flight
                await correcting.execute(text("SET lock_timeout = '300ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    await inventory_service.ensure_invoice_changeable(correcting, invoice_id, "correct_line_item")
                await correcting.rollback()

                receiving.add(InventoryReceipt(
                    invoice_id=invoice_id, store_id=received.store_id, status="POSTED", received_on=date(2026, 10, 9),
                    confirmed_by="testadmin", confirmed_role="ADMIN", line_count=0, discrepancy_count=0,
                    resolution_snapshot={}))
                await receiving.commit()

                with pytest.raises(InvoiceReceivedError):
                    await inventory_service.ensure_invoice_changeable(correcting, invoice_id, "correct_line_item")
        finally:
            await engine.dispose()

    async def test_the_database_allows_one_posted_receipt_per_invoice(self, db_session, received):
        row = {"invoice_id": uuid.UUID(received.invoice_id), "store_id": received.store_id, "received_on": date(2026, 10, 9),
                   "confirmed_by": "testadmin", "confirmed_role": "ADMIN", "line_count": 0, "discrepancy_count": 0,
                   "resolution_snapshot": {}}
        db_session.add(InventoryReceipt(status="POSTED", **row))
        await db_session.commit()
        db_session.add(InventoryReceipt(status="POSTED", **row))
        with pytest.raises(IntegrityError, match="uq_inventory_receipts_invoice_posted"):
            await db_session.commit()
        await db_session.rollback()
        db_session.add(InventoryReceipt(status="REVERSED", **row))              # reversed without who/when/why
        with pytest.raises(IntegrityError, match="ck_inventory_receipts_reversal"):
            await db_session.commit()
        await db_session.rollback()


@pytest.fixture(autouse=True)
async def _clean_inventory(db_engine):
    """The shared teardown truncates invoices/stores; the ledger goes first."""
    yield
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE inventory_movements, inventory_receipts CASCADE"))
