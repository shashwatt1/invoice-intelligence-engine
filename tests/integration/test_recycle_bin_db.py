"""
tests/integration/test_recycle_bin_db.py — the Recycle Bin workflow against real Postgres.

The `received` fixture is a VALIDATED invoice processed through the real
pipeline (fake LLM) at a physical store. Covered:

  * binning needs a reason, records who/when/why/previous status, is
    idempotent, follows the USER ownership rule, and is refused once the
    invoice was exported to EDI;
  * while a document is in the bin the same file is processed again at once
    (no duplicate error, no administrator) and linked as its re-upload; a
    duplicate of an ACTIVE document is still refused;
  * restore returns the previous status (mid-pipeline comes back STOPPED);
    with an active document holding the same file or invoice it is refused,
    and only an administrator can restore it as a read-only reference copy;
  * deletion requests: one pending per item, withdrawn by the requester,
    rejected with a note (stays in the bin) or approved — which deletes only
    the binned document and leaves a PURGED record with its snapshot;
  * an administrator's direct delete leaves the same record; history rows
    are never edited.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.document import Document
from app.models.inventory import InventoryMovement, InventoryReceipt
from app.models.invoice import Invoice
from app.models.recycle_bin import BinDeletionRequest, ImmutableBinHistoryError, RecycleBinEntry
from tests.integration.conftest import (
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    USER_PASSWORD,
    USER_USERNAME,
    requires_db,
)
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.integration.test_inventory_db import (  # noqa: F401 — fixture reuse
    ALE,
    LAGER,
    STORE,
    _clean_inventory,
    approve,
    received,
)
from tests.integration.test_store_access_db import client_as
from tests.pdf_builder import build_pdf

pytestmark = requires_db

SAME_FILE = build_pdf(["inventory invoice " + "pad " * 300])      # the bytes the `received` fixture uploaded


@pytest.fixture
async def manager(app, api_client):  # noqa: F811
    client = await client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
    yield client
    await client.aclose()


@pytest.fixture
async def user(app, api_client):  # noqa: F811
    client = await client_as(app, USER_USERNAME, USER_PASSWORD)
    yield client
    await client.aclose()


async def bin_doc(client, document_id, reason="wrong photo"):
    return await client.post(f"/api/v1/documents/{document_id}/move-to-bin", json={"reason": reason})


async def entries(client, status="IN_BIN"):
    r = await client.get("/api/v1/recycle-bin", params={"status": status})
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def doc_status(db_session, document_id):
    db_session.expire_all()
    return (await db_session.execute(select(Document.status).where(Document.id == document_id))).scalar()


class TestBinning:
    async def test_a_reason_is_required_and_the_entry_records_who_why_and_what(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        assert (await bin_doc(manager, received.document_id, reason="  ")).status_code == 422
        assert (await manager.post(f"/api/v1/documents/{received.document_id}/move-to-bin")).status_code == 422
        r = await bin_doc(manager, received.document_id)
        assert r.status_code == 200 and r.json()["data"]["status"] == "BINNED"
        assert (await bin_doc(manager, received.document_id, reason="again")).status_code == 200      # idempotent
        [entry] = await entries(api_client)
        assert (entry["document_id"], entry["previous_status"], entry["binned_by"], entry["binned_by_role"],
                entry["reason"]) == (received.document_id, "COMPLETED", "testmanager", "MANAGER", "wrong photo")
        assert entry["invoice_id"] == received.invoice_id and entry["filename"] == "inventory.pdf"
        assert entry["store"]["id"] == str(received.store_id) and entry["pending_request"] is None
        total = (await db_session.execute(select(func.count()).select_from(RecycleBinEntry))).scalar()
        assert total == 1

    async def test_a_user_bins_only_their_own_uploads_and_sees_only_their_own_items(
        self, api_client, user, received,  # noqa: F811
    ):
        assert (await bin_doc(user, received.document_id)).status_code == 403    # uploaded by the admin
        own = await process_file(user, content=build_pdf(["user upload " + "pad " * 300]), filename="mine.pdf",
                                 store=STORE)
        assert (await bin_doc(user, own["document_id"])).status_code == 200
        assert [e["document_id"] for e in await entries(user)] == [own["document_id"]]
        await bin_doc(api_client, received.document_id)
        assert len(await entries(api_client)) == 2 and len(await entries(user)) == 1
        summary = (await user.get("/api/v1/recycle-bin/summary")).json()["data"]
        assert summary == {"in_bin": 1, "pending_requests": 0}

    async def test_an_invoice_exported_to_edi_cannot_be_binned(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        export = await api_client.get(f"/api/v1/invoices/{received.invoice_id}/export", params={"format": "pdi"})
        assert export.status_code == 200, export.text                   # recorded in the EDI export ledger
        r = await bin_doc(manager, received.document_id)
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "edi_exported"
        assert await doc_status(db_session, received.document_id) == "COMPLETED"


class TestReprocessingWhileBinned:
    async def test_the_same_file_processes_again_at_once_and_is_linked_while_an_active_duplicate_is_refused(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        dup = await manager.post("/api/v1/invoices/process", files={"file": ("again.pdf", SAME_FILE, "application/pdf")},
                                 data={"store_id": str(received.store_id)})
        assert dup.status_code == 409                                       # active: still a duplicate
        await bin_doc(manager, received.document_id)
        again = await process_file(manager, content=SAME_FILE, filename="again.pdf", store=STORE)
        [entry] = await entries(api_client)
        assert entry["reuploaded_as_document_id"] == again["document_id"]
        assert entry["conflicting_document_id"] == again["document_id"]     # restoring it as it was would clash

    async def test_a_failed_run_is_binned_and_retried_without_waiting_for_an_administrator(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        document = await db_session.get(Document, received.document_id)
        document.status = "FAILED"
        await db_session.commit()
        await bin_doc(manager, received.document_id, reason="OCR failed; retrying")
        [entry] = await entries(manager)
        req = await manager.post(f"/api/v1/recycle-bin/{entry['id']}/deletion-requests", json={"reason": "duplicate run"})
        assert req.status_code == 201
        retry = await process_file(manager, content=SAME_FILE, filename="retry.pdf", store=STORE)
        # The administrator approves later: only the binned run is deleted; the retry is untouched.
        ok = await api_client.post(f"/api/v1/recycle-bin/deletion-requests/{req.json()['data']['id']}/approve",
                                   json={"note": "confirmed duplicate"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["entry"]["status"] == "PURGED" and ok.json()["data"]["request"]["status"] == "APPROVED"
        assert await doc_status(db_session, received.document_id) is None
        assert await doc_status(db_session, retry["document_id"]) is not None


class TestRestoring:
    async def test_restore_returns_the_previous_status_and_cancels_a_pending_request(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        await bin_doc(manager, received.document_id)
        [entry] = await entries(manager)
        await manager.post(f"/api/v1/recycle-bin/{entry['id']}/deletion-requests", json={"reason": "not needed"})
        r = await manager.post(f"/api/v1/recycle-bin/{entry['id']}/restore", json={})
        assert r.status_code == 200, r.text
        assert (r.json()["data"]["document_status"], r.json()["data"]["entry"]["status"]) == ("COMPLETED", "RESTORED")
        assert await doc_status(db_session, received.document_id) == "COMPLETED"
        request = (await db_session.execute(select(BinDeletionRequest))).scalars().one()
        assert request.status == "CANCELLED"
        assert (await manager.post(f"/api/v1/recycle-bin/{entry['id']}/restore", json={})).status_code == 422

    async def test_a_run_binned_mid_pipeline_comes_back_stopped(self, manager, db_session, received):  # noqa: F811
        document = await db_session.get(Document, received.document_id)
        document.status = "AI_PROCESSING"
        await db_session.commit()
        await bin_doc(manager, received.document_id)
        [entry] = await entries(manager)
        r = await manager.post(f"/api/v1/recycle-bin/{entry['id']}/restore", json={})
        assert r.json()["data"]["document_status"] == "STOPPED"

    async def test_a_conflict_is_refused_and_only_an_administrator_restores_it_as_a_read_only_reference_copy(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        await bin_doc(manager, received.document_id)
        again = await process_file(manager, content=SAME_FILE, filename="again.pdf", store=STORE)
        [entry] = await entries(manager)
        url = f"/api/v1/recycle-bin/{entry['id']}/restore"
        for client in (manager, api_client):
            r = await client.post(url, json={})
            assert r.status_code == 409 and r.json()["error"]["error_code"] == "ERR_RESTORE_CONFLICT"
            assert r.json()["error"]["detail"]["conflicting_document_id"] == again["document_id"]
        assert (await manager.post(url, json={"as_reference": True})).status_code == 403
        r = await api_client.post(url, json={"as_reference": True, "note": "keep for debugging"})
        assert r.status_code == 200, r.text
        assert (r.json()["data"]["document_status"], r.json()["data"]["entry"]["status"]) == (
            "REFERENCE", "RESTORED_AS_REFERENCE")
        document = await db_session.get(Document, received.document_id)
        await db_session.refresh(document)
        assert str(document.reference_of_document_id) == again["document_id"]
        # Read-only and never counted.
        r = await api_client.patch(f"/api/v1/invoices/{received.invoice_id}/date",
                                   json={"invoice_date": "2026-10-01", "corrected_by": "t"})
        assert r.status_code == 409 and r.json()["error"]["detail"]["reason"] == "reference_copy"
        listed = [row["document_id"] for row in (await api_client.get("/api/v1/invoices")).json()["items"]]
        assert received.document_id not in listed and again["document_id"] in listed

    async def test_the_same_invoice_in_a_different_file_is_also_a_conflict(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        await bin_doc(manager, received.document_id)
        other = await process_file(manager, content=build_pdf(["a second photo " + "pad " * 300]),
                                   filename="second.pdf", store=STORE)            # same fake invoice, new bytes
        [entry] = await entries(manager)
        assert entry["conflicting_document_id"] == other["document_id"]
        r = await manager.post(f"/api/v1/recycle-bin/{entry['id']}/restore", json={})
        assert r.status_code == 409


class TestDeletionRequestsAndPurge:
    async def test_requests_are_single_withdrawable_and_rejected_only_with_a_note(
        self, app, api_client, manager, user, db_session, received,  # noqa: F811
    ):
        await bin_doc(manager, received.document_id)
        [entry] = await entries(manager)
        url = f"/api/v1/recycle-bin/{entry['id']}/deletion-requests"
        assert (await manager.post(url, json={"reason": " "})).status_code == 422
        first = (await manager.post(url, json={"reason": "duplicate"})).json()["data"]
        again = await manager.post(url, json={"reason": "again"})
        assert again.status_code == 422 and again.json()["error"]["detail"]["reason"] == "already_requested"
        assert (await user.post(f"/api/v1/recycle-bin/deletion-requests/{first['id']}/cancel")).status_code == 404
        cancelled = await manager.post(f"/api/v1/recycle-bin/deletion-requests/{first['id']}/cancel")
        assert cancelled.json()["data"]["status"] == "CANCELLED"
        second = (await manager.post(url, json={"reason": "duplicate"})).json()["data"]
        reject = f"/api/v1/recycle-bin/deletion-requests/{second['id']}/reject"
        assert (await manager.post(reject, json={"note": "no"})).status_code == 403
        assert (await api_client.post(reject, json={})).status_code == 422
        r = await api_client.post(reject, json={"note": "keep it for the audit"})
        assert r.json()["data"]["request"]["status"] == "REJECTED"
        assert await doc_status(db_session, received.document_id) == "BINNED"
        queue = (await api_client.get("/api/v1/recycle-bin/deletion-requests", params={"status": "REJECTED"})).json()
        assert [q["decision_note"] for q in queue["items"]] == ["keep it for the audit"]

    async def test_only_an_administrator_purges_and_the_record_survives_the_document(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        await bin_doc(manager, received.document_id)
        [entry] = await entries(manager)
        url = f"/api/v1/recycle-bin/{entry['id']}/purge"
        assert (await manager.post(url, json={"reason": "x"})).status_code == 403
        assert (await api_client.post(url, json={"reason": ""})).status_code == 422
        r = await api_client.post(url, json={"reason": "test upload, never real"})
        assert r.status_code == 200, r.text
        purged = r.json()["data"]["entry"]
        assert (purged["status"], purged["document_id"], purged["closed_by"], purged["close_note"]) == (
            "PURGED", None, "testadmin", "test upload, never real")
        assert (purged["invoice_number"], purged["filename"]) == (entry["invoice_number"], "inventory.pdf")
        assert await doc_status(db_session, received.document_id) is None
        assert (await db_session.execute(select(Invoice).where(Invoice.id == received.invoice_id))).first() is None
        assert [e["id"] for e in await entries(api_client, "PURGED")] == [entry["id"]]

    async def test_a_direct_administrator_delete_is_recorded_too(self, api_client, db_session, received):  # noqa: F811
        r = await api_client.delete(f"/api/v1/invoices/{received.invoice_id}", params={"reason": "uploaded twice"})
        assert r.status_code == 200, r.text
        [record] = await entries(api_client, "PURGED")
        assert (record["previous_status"], record["reason"], record["binned_by"], record["document_id"]) == (
            "COMPLETED", "uploaded twice", "testadmin", None)


class TestDeletingADocumentThatWasRestoredBefore:
    """
    Regression: deleting a document clears document_id on EVERY bin entry for it, including an
    earlier RESTORED one. That used to violate ck_recycle_bin_entries_document and roll the
    delete back. Closed entries may now lose the link; open entries may not.
    """

    async def _restored_once(self, api_client, manager, db_session, received):  # noqa: F811
        # An unrelated opening count at the same store: the deletion must leave the ledger alone.
        r = await api_client.post(f"/api/v1/inventory/stores/{received.store_id}/opening-counts", json={
            "count_date": "2026-10-10", "reason": "shelf count",
            "entries": [{"item_code": "99900000099", "quantity": 7}]})
        assert r.status_code == 201, r.text
        assert (await bin_doc(manager, received.document_id, reason="first time")).status_code == 200
        [first] = await entries(manager)
        assert (await manager.post(f"/api/v1/recycle-bin/{first['id']}/restore", json={})).status_code == 200
        return first

    async def _ledger(self, db_session):
        db_session.expire_all()
        return ((await db_session.execute(select(func.count()).select_from(InventoryMovement))).scalar(),
                (await db_session.execute(select(func.count()).select_from(InventoryReceipt))).scalar())

    async def _history(self, db_session, received):  # noqa: F811
        db_session.expire_all()
        return (await db_session.execute(
            select(RecycleBinEntry).where(RecycleBinEntry.invoice_id == received.invoice_id)
            .order_by(RecycleBinEntry.binned_at))).scalars().all()

    async def test_bin_restore_bin_then_purge_deletes_it_and_keeps_both_entries(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        first = await self._restored_once(api_client, manager, db_session, received)
        ledger = await self._ledger(db_session)
        assert (await bin_doc(manager, received.document_id, reason="second time")).status_code == 200
        [second] = await entries(manager)
        r = await api_client.post(f"/api/v1/recycle-bin/{second['id']}/purge", json={"reason": "duplicate test upload"})
        assert r.status_code == 200, r.text

        assert await doc_status(db_session, received.document_id) is None
        assert (await db_session.execute(select(Invoice).where(Invoice.id == received.invoice_id))).first() is None
        history = await self._history(db_session, received)
        assert [(e.status, e.reason, e.document_id) for e in history] == [
            ("RESTORED", "first time", None), ("PURGED", "second time", None)]
        assert [str(e.id) for e in history] == [first["id"], second["id"]]
        assert history[0].closed_by == "testmanager" and history[1].close_note == "duplicate test upload"
        assert all(e.filename == "inventory.pdf" and e.invoice_number for e in history)   # snapshots kept
        assert await self._ledger(db_session) == ledger                                  # ledger untouched
        assert await entries(api_client) == []                                           # nothing left open

    async def test_bin_restore_then_direct_admin_delete_succeeds_and_is_recorded(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        await self._restored_once(api_client, manager, db_session, received)
        ledger = await self._ledger(db_session)
        r = await api_client.delete(f"/api/v1/invoices/{received.invoice_id}", params={"reason": "uploaded twice"})
        assert r.status_code == 200, r.text

        assert await doc_status(db_session, received.document_id) is None
        history = await self._history(db_session, received)
        assert [(e.status, e.reason, e.document_id) for e in history] == [
            ("RESTORED", "first time", None), ("PURGED", "uploaded twice", None)]
        assert history[1].binned_by == "testadmin" and history[1].previous_status == "COMPLETED"
        assert await self._ledger(db_session) == ledger

    async def test_an_open_entry_still_cannot_lose_its_document(self, manager, db_session, received):  # noqa: F811
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        await bin_doc(manager, received.document_id)
        with pytest.raises(IntegrityError):
            await db_session.execute(text("UPDATE recycle_bin_entries SET document_id = NULL WHERE status = 'IN_BIN'"))
        await db_session.rollback()


class TestHistoryIsImmutable:
    async def test_a_closed_entry_and_a_decided_request_never_change(
        self, api_client, manager, db_session, received,  # noqa: F811
    ):
        await bin_doc(manager, received.document_id)
        [entry] = await entries(manager)
        await manager.post(f"/api/v1/recycle-bin/{entry['id']}/restore", json={})
        row = await db_session.get(RecycleBinEntry, entry["id"])
        await db_session.refresh(row)
        row.reason = "rewritten"
        with pytest.raises(ImmutableBinHistoryError):
            await db_session.flush()
        await db_session.rollback()
        row = await db_session.get(RecycleBinEntry, entry["id"])
        await db_session.delete(row)
        with pytest.raises(ImmutableBinHistoryError):
            await db_session.flush()
        await db_session.rollback()
