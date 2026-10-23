"""
tests/integration/test_inventory_approval_db.py — receipt approval against real Postgres.

The real flow: an invoice processed by the pipeline (fake LLM), its units per
case approved, its PDI file exported into the EDI export ledger, then marked
Retailed through the API, which submits the receipt.

  * Marking Retailed with inventory off submits nothing, and switching it on
    later back-fills nothing. With it on, the submission records who, the
    attestation, the export and the proposed lines, and writes no movement.
  * Two concurrent approvals post exactly once; a second approval is refused.
    A granted MANAGER approves an ADMIN's submission (recorded apart); USER is
    refused.
  * A rejection is terminal at the service and at the database; a change to
    the invoice makes a submission stale until re-marked, which supersedes it;
    reopening Retailed withdraws it. None of these moves stock.
  * Deleting an invoice withdraws a pending submission, or reverses a posted
    receipt and deletes in one transaction; with inventory off or without the
    deleter's grant it is refused unchanged; a failure after the reversal rolls
    everything back and a retry reverses once; concurrent deletes, and a
    reversal racing a delete, compensate once; the ledger's own foreign keys
    refuse removing its rows.
  * A wrong-store invoice is corrected by deletion (reversal at the old store)
    and re-upload: stock ends up only at the right store.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.exceptions import ValidationError
from app.models.edi_export import EdiExport
from app.models.inventory import ImmutableReceiptError, InventoryMovement, InventoryReceipt
from app.models.invoice import Invoice
from app.models.user import User
from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.user_repository import UserRepository
from app.services import inventory_approval as approval
from app.services import inventory_service, store_access
from tests.integration.conftest import (
    KNOWN_USERS,
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    PHYSICAL_STORE,
    TEST_URL,
    USER_PASSWORD,
    USER_USERNAME,
    requires_db,
    store_id,
)
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.integration.test_inventory_db import (  # noqa: F401 — fixture reuse
    ALE,
    ALE_CODE,
    LAGER,
    LAGER_CODE,
    _clean_inventory,
    approve,
    approve_submission,
    balances,
    grant,
    ledger_counts,
    preview_of,
    receive,
    received,
    set_inventory,
    submit,
)
from tests.integration.test_store_access_db import client_as
from tests.pdf_builder import build_pdf

pytestmark = requires_db


async def mapped(api_client, db_session, received):  # noqa: F811
    await approve(api_client, db_session, received.invoice_id, LAGER, 12)
    await approve(api_client, db_session, received.invoice_id, ALE, 6)


async def receipts(db_session):
    db_session.expire_all()
    return (await db_session.execute(select(InventoryReceipt).order_by(InventoryReceipt.created_at))).scalars().all()


async def movements(db_session):
    db_session.expire_all()
    return (await db_session.execute(select(InventoryMovement))).scalars().all()


def engine():
    return create_async_engine(TEST_URL, poolclass=NullPool)


async def admin(session):
    return await session.get(User, KNOWN_USERS["ADMIN"])


class TestSubmission:
    async def test_with_inventory_off_retailed_submits_nothing_and_nothing_is_back_filled(
        self, api_client, db_session, received, monkeypatch,  # noqa: F811
    ):
        await mapped(api_client, db_session, received)
        set_inventory(monkeypatch, False)
        assert (await api_client.get(f"/api/v1/invoices/{received.invoice_id}/export",
                                     params={"format": "pdi"})).status_code == 200
        assert (await api_client.post(f"/api/v1/invoices/{received.invoice_id}/retailed")).status_code == 200
        assert await ledger_counts(db_session) == (0, 0)
        set_inventory(monkeypatch, True)
        preview = await preview_of(api_client, received.invoice_id)
        assert preview["submission"] is None and preview["history"] == []           # no back-fill
        assert await ledger_counts(db_session) == (0, 0)

    async def test_a_submission_records_the_proposal_and_moves_nothing(self, api_client, db_session, received):  # noqa: F811
        await mapped(api_client, db_session, received)
        preview = await submit(api_client, received.invoice_id)
        [receipt] = await receipts(db_session)
        receipt_id, invoice_number = receipt.id, receipt.invoice_number   # before movements() expires the session
        invoice = await db_session.get(Invoice, uuid.UUID(received.invoice_id))
        await db_session.refresh(invoice)
        export = (await db_session.execute(select(EdiExport).order_by(EdiExport.export_number.desc()))).scalars().first()
        assert (receipt.status, receipt.submitted_by, receipt.submitted_role, receipt.store_id) == (
            "SUBMITTED", "testadmin", "ADMIN", received.store_id)
        assert receipt.attestation_sha256 == invoice.retailed_attestation_sha256
        assert receipt.edi_export_id == export.id == invoice.retailed_edi_export_id
        assert receipt.received_on is None and receipt.confirmed_by is None and receipt.approved_at is None
        assert [line["item_code"] for line in receipt.submission_snapshot["lines"]] == [LAGER_CODE, ALE_CODE]
        assert await movements(db_session) == []
        assert preview["submission"]["stale_reasons"] == [] and preview["can_approve"] is True

        queue = (await api_client.get(f"/api/v1/inventory/stores/{received.store_id}/approvals")).json()["data"]
        assert [(q["receipt"]["id"], q["invoice_number"], q["stale_reasons"]) for q in queue] == [
            (str(receipt_id), invoice_number, [])]
        overview = (await api_client.get(f"/api/v1/inventory/stores/{received.store_id}/overview")).json()["data"]
        assert overview["pending_approvals"] == 1 and overview["recent_receipts"] == []


class TestApproval:
    async def test_two_concurrent_approvals_post_exactly_once(self, api_client, db_session, received):  # noqa: F811
        await mapped(api_client, db_session, received)
        preview = await submit(api_client, received.invoice_id)
        invoice_id, receipt_id = uuid.UUID(received.invoice_id), uuid.UUID(preview["submission"]["receipt"]["id"])
        accepted = {uuid.UUID(x["invoice_item_id"]): Decimal(x["expected_units"]) for x in preview["lines"]}
        db = engine()

        async def approve_once():
            async with AsyncSession(db, expire_on_commit=False) as session:
                try:
                    await approval.approve_receipt(session, invoice_id, receipt_id=receipt_id, accepted=accepted,
                                                   reasons={}, received_on=date(2026, 10, 9), note=None,
                                                   user=await admin(session))
                    await session.commit()
                    return "posted"
                except ValidationError as exc:
                    await session.rollback()
                    return exc.detail["reason"]
        try:
            results = await asyncio.gather(approve_once(), approve_once())
        finally:
            await db.dispose()
        assert sorted(results) == ["not_pending", "posted"]
        assert await ledger_counts(db_session) == (1, 2)
        again = await approve_submission(api_client, received.invoice_id, preview)
        assert again.status_code == 422 and again.json()["error"]["detail"]["reason"] == "not_pending"
        assert await ledger_counts(db_session) == (1, 2)

    async def test_a_granted_manager_approves_and_user_is_refused(self, app, api_client, db_session, received):  # noqa: F811
        await mapped(api_client, db_session, received)
        preview = await submit(api_client, received.invoice_id)
        user = await client_as(app, USER_USERNAME, USER_PASSWORD)
        manager = await client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            assert (await approve_submission(user, received.invoice_id, preview)).status_code == 403
            assert (await approve_submission(manager, received.invoice_id, preview)).status_code == 404  # no grant
            await grant(db_session, "MANAGER", received.store_id)
            r = await approve_submission(manager, received.invoice_id, preview)
            assert r.status_code == 200, r.text
        finally:
            await user.aclose()
            await manager.aclose()
        receipt = r.json()["data"]["receipt"]
        assert (receipt["submitted_by"], receipt["confirmed_by"], receipt["confirmed_role"], receipt["self_approved"]) \
            == ("testadmin", "testmanager", "MANAGER", False)
        assert {m.actor_username for m in await movements(db_session)} == {"testmanager"}

    async def test_a_rejection_is_terminal_here_and_in_the_database(self, api_client, db_session, received):  # noqa: F811
        await mapped(api_client, db_session, received)
        preview = await submit(api_client, received.invoice_id)
        url = f"/api/v1/inventory/invoices/{received.invoice_id}/receipt"
        rid = preview["submission"]["receipt"]["id"]
        assert (await api_client.post(f"{url}/reject", json={"receipt_id": rid, "reason": "  "})).status_code == 422
        r = await api_client.post(f"{url}/reject", json={"receipt_id": rid, "reason": "delivered to the wrong dock"})
        assert r.status_code == 200, r.text
        assert (await api_client.post(f"{url}/reject", json={"receipt_id": rid, "reason": "again"})).status_code == 422
        refused = await approve_submission(api_client, received.invoice_id, preview)
        assert refused.status_code == 422 and refused.json()["error"]["detail"]["reason"] == "not_pending"

        with pytest.raises(IntegrityError, match="ck_inventory_receipts_rejection"):
            await db_session.execute(text("UPDATE inventory_receipts SET status = 'SUBMITTED' WHERE id = :i"),
                                     {"i": uuid.UUID(rid)})
        await db_session.rollback()
        [row] = await receipts(db_session)
        row.status = "SUBMITTED"
        row.rejected_at = row.rejected_by = row.rejected_role = row.rejection_reason = None
        with pytest.raises(ImmutableReceiptError):
            await db_session.flush()
        await db_session.rollback()
        [row] = await receipts(db_session)
        assert (row.status, row.rejected_by, row.rejection_reason) == ("REJECTED", "testadmin",
                                                                       "delivered to the wrong dock")
        assert await movements(db_session) == []

        # Retailed is untouched by a rejection; reopening and re-marking submits afresh, linked back.
        base = f"/api/v1/invoices/{received.invoice_id}"
        assert (await api_client.post(f"{base}/retailed/reopen", json={})).status_code == 200
        assert (await api_client.post(f"{base}/retailed")).status_code == 200
        fresh = (await preview_of(api_client, received.invoice_id))["submission"]["receipt"]
        assert fresh["previous_receipt_id"] == rid and fresh["status"] == "SUBMITTED"

    async def test_a_change_after_submission_is_stale_until_re_marked(self, api_client, db_session, received):  # noqa: F811
        await mapped(api_client, db_session, received)
        first = await submit(api_client, received.invoice_id)
        base = f"/api/v1/invoices/{received.invoice_id}"
        r = await api_client.patch(f"{base}/items/0", json={"quantity": 2, "line_total": 43.90, "corrected_by": "m"})
        assert r.status_code == 200, r.text            # a pending submission does not lock the invoice
        r = await api_client.patch(f"{base}/totals", json={"subtotal": 68.90, "grand_total": 68.90,
                                                           "corrected_by": "m"})
        assert r.status_code == 200, r.text

        stale = await preview_of(api_client, received.invoice_id)
        reasons = stale["submission"]["stale_reasons"]
        assert approval.STALE_INVOICE_CHANGED in reasons and approval.STALE_LINES_CHANGED in reasons
        assert [(c["item_code"], c["change"], Decimal(c["proposed_units"]), Decimal(c["current_units"]))
                for c in stale["submission"]["line_changes"]] == [(LAGER_CODE, "changed", 36, 24)]
        assert stale["can_approve"] is False
        refused = await approve_submission(api_client, received.invoice_id, stale)
        assert refused.status_code == 422 and refused.json()["error"]["detail"]["reason"] == "stale_submission"
        assert await movements(db_session) == []

        second = await submit(api_client, received.invoice_id)        # export again and re-mark
        old, new = await receipts(db_session)
        assert (old.status, new.status, new.previous_receipt_id) == ("SUPERSEDED", "SUBMITTED", old.id)
        assert str(old.id) == first["submission"]["receipt"]["id"]
        r = await approve_submission(api_client, received.invoice_id, second)
        assert r.status_code == 200, r.text
        assert await balances(db_session, received.store_id) == {LAGER_CODE: 24, ALE_CODE: 12}

    async def test_reopening_retailed_withdraws_the_pending_submission(self, api_client, db_session, received):  # noqa: F811
        await mapped(api_client, db_session, received)
        preview = await submit(api_client, received.invoice_id)
        r = await api_client.post(f"/api/v1/invoices/{received.invoice_id}/retailed/reopen",
                                  json={"note": "lager count looks wrong"})
        assert r.status_code == 200, r.text
        [row] = await receipts(db_session)
        assert (row.status, row.withdrawn_by) == ("WITHDRAWN", "testadmin")
        assert row.withdrawal_reason == "Retailed mark removed by testadmin: lager count looks wrong"
        refused = await approve_submission(api_client, received.invoice_id, preview)
        assert refused.status_code == 422 and refused.json()["error"]["detail"]["reason"] == "not_pending"
        assert await movements(db_session) == []


class TestDeletion:
    async def test_an_unposted_invoice_is_deleted_and_its_submission_withdrawn_and_kept(
        self, api_client, db_session, received,  # noqa: F811
    ):
        await mapped(api_client, db_session, received)
        await submit(api_client, received.invoice_id)
        r = await api_client.delete(f"/api/v1/invoices/{received.invoice_id}")
        assert r.status_code == 200, r.text
        [row] = await receipts(db_session)
        assert (row.status, row.invoice_id, row.invoice_number, row.withdrawn_by) == (
            "WITHDRAWN", None, "INV-001", "testadmin")
        assert row.withdrawal_reason == "Invoice #INV-001 deleted by testadmin"
        assert await movements(db_session) == []

    async def test_a_posted_invoice_is_reversed_then_deleted_in_one_transaction(
        self, api_client, db_session, received,  # noqa: F811
    ):
        await mapped(api_client, db_session, received)
        await receive(api_client, received.invoice_id)
        r = await api_client.delete(f"/api/v1/invoices/{received.invoice_id}")
        assert r.status_code == 200, r.text
        assert (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).status_code == 404
        [row] = await receipts(db_session)
        assert (row.status, row.invoice_id, row.invoice_number, row.reversed_by) == (
            "REVERSED", None, "INV-001", "testadmin")
        assert row.reversal_reason == "Invoice #INV-001 deleted by testadmin"
        rows = await movements(db_session)
        assert len(rows) == 4 and all(m.invoice_id is None and m.invoice_item_id is None for m in rows)
        assert sum(1 for m in rows if m.reversal_of_id is not None) == 2
        assert await balances(db_session, received.store_id) == {LAGER_CODE: 0, ALE_CODE: 0}

    async def test_without_inventory_or_a_grant_the_delete_is_refused_and_nothing_changes(
        self, api_client, db_session, received, monkeypatch,  # noqa: F811
    ):
        await mapped(api_client, db_session, received)
        await receive(api_client, received.invoice_id)
        url = f"/api/v1/invoices/{received.invoice_id}"

        set_inventory(monkeypatch, False)
        r = await api_client.delete(url)
        assert r.status_code == 409 and r.json()["error"]["detail"]["blocked_by"] == "inventory_disabled"
        set_inventory(monkeypatch, True)

        users = UserRepository(db_session)
        actor = await users.get(KNOWN_USERS["ADMIN"])
        await store_access.revoke_store_access(db_session, user_id=actor.id, store_id=received.store_id, actor=actor,
                                               reason="test: no longer works this store")
        await db_session.commit()
        r = await api_client.delete(url)
        assert r.status_code == 409 and r.json()["error"]["detail"]["blocked_by"] == "no_store_access"

        assert (await api_client.get(url)).status_code == 200
        [row] = await receipts(db_session)
        assert row.status == "POSTED" and row.invoice_id == uuid.UUID(received.invoice_id)
        assert await ledger_counts(db_session) == (1, 2)

    async def test_a_failure_after_the_reversal_rolls_back_and_a_retry_reverses_once(
        self, api_client, db_session, received, monkeypatch,  # noqa: F811
    ):
        await mapped(api_client, db_session, received)
        await receive(api_client, received.invoice_id)
        url = f"/api/v1/invoices/{received.invoice_id}"

        async def broken_delete(_self, _document):
            raise RuntimeError("simulated failure deleting the document")

        with monkeypatch.context() as broken:
            broken.setattr(DocumentRepository, "delete", broken_delete)
            try:
                response = await api_client.delete(url)
            except RuntimeError:
                response = None
            assert response is None or response.status_code == 500
        with monkeypatch.context() as broken:
            # The reversal's compensating movements fail a column limit: a database error mid-delete.
            broken.setattr(inventory_service, "reversal_movement_key", lambda _m: "k" * 200)
            with pytest.raises(DBAPIError, match="value too long"):
                await api_client.delete(url)

        assert (await api_client.get(url)).status_code == 200
        [row] = await receipts(db_session)
        assert row.status == "POSTED" and await ledger_counts(db_session) == (1, 2)

        assert (await api_client.delete(url)).status_code == 200                  # the retry
        assert await ledger_counts(db_session) == (1, 4)                           # compensated once
        assert (await api_client.delete(url)).status_code == 404

    async def test_concurrent_deletes_reverse_once(self, api_client, db_session, received):  # noqa: F811
        await mapped(api_client, db_session, received)
        await receive(api_client, received.invoice_id)
        invoice_id = uuid.UUID(received.invoice_id)
        db = engine()

        async def delete_once():
            async with AsyncSession(db, expire_on_commit=False) as session:
                await approval.release_invoice_for_deletion(session, invoice_id, await admin(session))
                invoice = await InvoiceRepository(session).get_detail(invoice_id, fresh=True)
                if invoice is None:
                    await session.rollback()
                    return "gone"
                await DocumentRepository(session).delete(invoice.document)
                await session.commit()
                return "deleted"
        try:
            results = await asyncio.gather(delete_once(), delete_once())
        finally:
            await db.dispose()
        assert sorted(results) == ["deleted", "gone"]
        assert await ledger_counts(db_session) == (1, 4)

    async def test_a_reversal_racing_a_delete_compensates_once(self, api_client, db_session, received):  # noqa: F811
        await mapped(api_client, db_session, received)
        receipt = (await receive(api_client, received.invoice_id))["receipt"]
        invoice_id = uuid.UUID(received.invoice_id)
        db = engine()

        async def delete_once():
            async with AsyncSession(db, expire_on_commit=False) as session:
                release = await approval.release_invoice_for_deletion(session, invoice_id, await admin(session))
                invoice = await InvoiceRepository(session).get_detail(invoice_id, fresh=True)
                await DocumentRepository(session).delete(invoice.document)
                await session.commit()
                return release
        try:
            async with AsyncSession(db, expire_on_commit=False) as reversing:
                await inventory_service.reverse_receipt(reversing, invoice_id, receipt_id=uuid.UUID(receipt["id"]),
                                                        reason="miscounted", user=await admin(reversing))
                deleting = asyncio.create_task(delete_once())
                await asyncio.sleep(0.5)
                assert not deleting.done()                 # waiting on the invoice lock the reversal holds
                await reversing.commit()
            release = await deleting
        finally:
            await db.dispose()
        assert release.reversed is None                    # it found the receipt already reversed
        assert await ledger_counts(db_session) == (1, 4)
        [row] = await receipts(db_session)
        assert (row.status, row.reversal_reason, row.invoice_id) == ("REVERSED", "miscounted", None)

    async def test_the_ledger_refuses_removing_its_own_rows(self, api_client, db_session, received):  # noqa: F811
        await mapped(api_client, db_session, received)
        await receive(api_client, received.invoice_id)
        assert (await api_client.delete(f"/api/v1/invoices/{received.invoice_id}")).status_code == 200
        with pytest.raises(IntegrityError, match="inventory_movements"):
            await db_session.execute(text("DELETE FROM inventory_receipts"))
        await db_session.rollback()
        with pytest.raises(IntegrityError, match="inventory_movements"):
            await db_session.execute(text("DELETE FROM inventory_movements WHERE reversal_of_id IS NULL"))
        await db_session.rollback()
        [row] = await receipts(db_session)
        with pytest.raises(ImmutableReceiptError):
            await db_session.delete(row)
            await db_session.flush()
        await db_session.rollback()
        assert await ledger_counts(db_session) == (1, 4)


class TestWrongStore:
    async def test_delete_and_re_upload_moves_the_stock_to_the_right_store(
        self, api_client, db_session, received,  # noqa: F811
    ):
        wrong, right = received.store_id, store_id(PHYSICAL_STORE)
        await mapped(api_client, db_session, received)
        first = (await receive(api_client, received.invoice_id))["receipt"]

        assert (await api_client.delete(f"/api/v1/invoices/{received.invoice_id}")).status_code == 200
        accepted = await process_file(api_client, content=build_pdf(["inventory invoice again " + "pad " * 300]),
                                      filename="inventory-right-store.pdf", store=PHYSICAL_STORE)
        status = (await api_client.get(accepted["status_url"])).json()["data"]
        invoice_id = status["invoice_id"]
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert (detail["status"], detail["store"]["id"]) == ("VALIDATED", str(right))

        await grant(db_session, "ADMIN", right)
        await approve(api_client, db_session, invoice_id, LAGER, 12)
        await approve(api_client, db_session, invoice_id, ALE, 6)
        second = (await receive(api_client, invoice_id))["receipt"]

        assert await balances(db_session, wrong) == {LAGER_CODE: 0, ALE_CODE: 0}
        assert await balances(db_session, right) == {LAGER_CODE: 36, ALE_CODE: 12}
        old, new = await receipts(db_session)
        assert (str(old.id), old.status, old.store_id, old.invoice_id) == (first["id"], "REVERSED", wrong, None)
        assert (str(new.id), new.status, new.store_id) == (second["id"], "POSTED", right)
        assert new.previous_receipt_id is None              # a new invoice: nothing reused
        assert len({m.idempotency_key for m in await movements(db_session)}) == 6
        total = (await db_session.execute(select(func.count()).select_from(InventoryMovement))).scalar()
        assert total == 6
