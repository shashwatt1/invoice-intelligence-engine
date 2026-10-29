"""
Recycle Bin — app/services/recycle_bin.py

The one place documents enter and leave the bin. Every function authorizes
the signed-in account itself, locks the rows it changes, and flushes only;
the caller commits (and removes stored files after a purge has committed).

  * move_to_bin: any role on a document it may act on (USER: own uploads).
    A reason is required. Refused while the invoice is received into
    inventory (reverse the receipt first) or once it was exported to EDI
    (an EDI ledger record, or marked Retailed). Idempotent.
  * While a document is in the bin its file no longer counts as a
    duplicate: the same file can be processed again at once, by anyone who
    may upload, without waiting for an administrator. The new upload is
    linked to the bin entry ("re-uploaded as").
  * restore: back to the status it had (a run that was mid-pipeline comes
    back STOPPED). If an active document now holds the same file or the same
    invoice, it cannot come back as it was: only an administrator can restore
    it, as a read-only REFERENCE copy that is never changed, exported,
    received or counted (see document_lifecycle.ensure_document_operable).
  * request_deletion / cancel_request: a non-administrator asks for
    permanent deletion, with a reason; an administrator approves (purges)
    or rejects (it stays in the bin). Restoring cancels a pending request.
  * purge: ADMIN only. Deletes the document and everything derived from it;
    its bin entry stays, PURGED, with who, when, why and a snapshot of what
    was deleted. An administrator's direct delete of a document that was
    never binned leaves the same record.

Before migration 0037 only move_to_bin works (status and processing log, as
before); everything else reports that the Recycle Bin is not available.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    PermissionDeniedError,
    RecordNotFoundError,
    RestoreConflictError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.document import INACTIVE_DOCUMENT_STATUSES, Document, DocumentStatus
from app.models.document_page import DocumentPage
from app.models.edi_export import EdiExport
from app.models.invoice import Invoice
from app.models.processing_log import LogStatus, PipelineStage
from app.models.recycle_bin import (
    ENTRY_IN_BIN,
    ENTRY_PURGED,
    ENTRY_RESTORED,
    ENTRY_RESTORED_AS_REFERENCE,
    REQUEST_APPROVED,
    REQUEST_CANCELLED,
    REQUEST_PENDING,
    REQUEST_REJECTED,
    BinDeletionRequest,
    RecycleBinEntry,
)
from app.models.user import ROLE_RANK, User, UserRole
from app.repositories.document_repository import DocumentRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.services.document_lifecycle import ACTIVE_DOCUMENT_STATUSES, ensure_document_visible
from app.services.inventory_service import ensure_document_changeable

logger = get_logger(__name__)

REASON_MAX_LENGTH = 1000
# A run that was mid-pipeline when binned has nothing running any more: it comes back STOPPED.
_RESUMES_AS_STOPPED = ACTIVE_DOCUMENT_STATUSES - {DocumentStatus.STORE_CONFIRMATION_REQUIRED}

_schema_seen = False


async def bin_schema_ready(session: AsyncSession) -> bool:
    """Whether migration 0037 is applied. Only the final state is cached."""
    global _schema_seen
    if not _schema_seen:
        _schema_seen = bool((await session.execute(
            text("SELECT to_regclass('public.recycle_bin_entries') IS NOT NULL"))).scalar())
    return _schema_seen


async def require_schema(session: AsyncSession) -> None:
    if not await bin_schema_ready(session):
        raise ValidationError(message="The Recycle Bin is not available until migration 0037 is applied.",
                              detail={"reason": "schema_missing"})


def _now() -> datetime:
    return datetime.now(UTC)


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN.value


def _require_admin(user: User) -> None:
    if not (user.is_active and _is_admin(user)):
        raise PermissionDeniedError(message="Only an administrator can do this.",
                                    detail={"required_role": UserRole.ADMIN.value})


def _reason(value: str | None, field: str = "reason") -> str:
    why = (value or "").strip()
    if not why:
        raise ValidationError(message="Give a reason; it is kept with the Recycle Bin record.",
                              detail={"field": field, "reason": "required"})
    if len(why) > REASON_MAX_LENGTH:
        raise ValidationError(message=f"The reason is limited to {REASON_MAX_LENGTH} characters.",
                              detail={"field": field, "reason": "too_long"})
    return why


def _actor(user: User, prefix: str) -> dict:
    return {f"{prefix}_by_user_id": user.id, f"{prefix}_by": user.username, f"{prefix}_by_role": user.role}


async def _locked_document(session: AsyncSession, document_id: uuid.UUID) -> Document:
    document = (await session.execute(
        select(Document).where(Document.id == document_id).with_for_update().execution_options(populate_existing=True)
    )).scalars().first()
    if document is None:
        raise RecordNotFoundError(message="Document not found.", detail={"document_id": str(document_id)})
    return document


async def _invoice_of(session: AsyncSession, document_id: uuid.UUID) -> Invoice | None:
    return (await session.execute(select(Invoice).where(Invoice.document_id == document_id))).scalars().first()


async def edi_exported(session: AsyncSession, invoice: Invoice | None) -> bool:
    """Exported to EDI: an export ledger record exists, or the invoice was marked Retailed (which needs one)."""
    if invoice is None:
        return False
    if invoice.retailed_at is not None:
        return True
    return bool((await session.execute(select(exists().where(EdiExport.invoice_id == invoice.id)))).scalar())


async def _log(session: AsyncSession, document: Document, user: User, event: str, message: str, **payload) -> None:
    await ProcessingLogRepository(session).add(
        document_id=document.id, stage=PipelineStage.LIFECYCLE, status=LogStatus.SUCCESS, message=message,
        payload={"event": event, "actor_user_id": str(user.id), "actor_username": user.username,
                 "role": user.role, **payload},
    )


def _snapshot(document: Document, invoice: Invoice | None) -> dict:
    return {
        "document_id": document.id, "filename": document.filename[:255], "file_hash": document.file_hash,
        "invoice_id": invoice.id if invoice else None,
        "invoice_number": (invoice.invoice_number or None) if invoice else None,
        "vendor_name": ((invoice.vendor_name or "")[:255] or None) if invoice else None,
        "store_id": (invoice.store_id if invoice and invoice.store_id else document.store_id),
        "uploaded_by_user_id": document.uploaded_by_user_id,
    }


async def open_entry(session: AsyncSession, document_id: uuid.UUID, *, lock: bool = False) -> RecycleBinEntry | None:
    query = select(RecycleBinEntry).where(RecycleBinEntry.document_id == document_id,
                                          RecycleBinEntry.status == ENTRY_IN_BIN)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(query)).scalars().first()


# ---- binning ------------------------------------------------------------------------------------

async def move_to_bin(session: AsyncSession, document: Document, user: User, reason: str | None) -> Document:
    """Move `document` into the bin on behalf of `user`. Idempotent for a document already binned."""
    ensure_document_visible(document, user)
    why = _reason(reason)
    document = await _locked_document(session, document.id)
    if document.status == DocumentStatus.BINNED:
        return document
    # Received goods are recorded against the invoice: reverse the receipt first.
    await ensure_document_changeable(session, document.id, "move_to_bin")
    invoice = await _invoice_of(session, document.id)
    if await edi_exported(session, invoice):
        raise ValidationError(
            message="This invoice was exported to EDI, so it cannot be moved to the Recycle Bin.",
            detail={"document_id": str(document.id), "reason": "edi_exported"})
    previous = document.status
    document.status = DocumentStatus.BINNED
    if await bin_schema_ready(session):
        session.add(RecycleBinEntry(**_snapshot(document, invoice), previous_status=previous, binned_at=_now(),
                                    **_actor(user, "binned"), reason=why, status=ENTRY_IN_BIN))
    await session.flush()
    await _log(session, document, user, "document_binned", f"Moved to the Recycle Bin by {user.username} ({user.role}).",
               previous_status=previous, reason=why)
    logger.info("document_binned", document_id=str(document.id), actor_user_id=str(user.id), role=user.role,
                previous_status=previous)
    return document


async def link_reupload(session: AsyncSession, document: Document, page_hashes: list[str]) -> None:
    """Record `document` as the re-upload of any binned document with the same file or a shared photo."""
    if not await bin_schema_ready(session):
        return
    hashes = {document.file_hash, *page_hashes}
    shares_photo = exists().where(DocumentPage.document_id == RecycleBinEntry.document_id,
                                  DocumentPage.file_hash.in_(hashes))
    entries = (await session.execute(
        select(RecycleBinEntry).where(RecycleBinEntry.status == ENTRY_IN_BIN,
                                      RecycleBinEntry.reuploaded_as_document_id.is_(None),
                                      RecycleBinEntry.document_id != document.id,
                                      or_(RecycleBinEntry.file_hash.in_(hashes), shares_photo))
    )).scalars().all()
    for entry in entries:
        entry.reuploaded_as_document_id = document.id
    if entries:
        await session.flush()


# ---- restoring ----------------------------------------------------------------------------------

async def active_conflict(session: AsyncSession, document: Document) -> Document | None:
    """
    An active document (not binned, not a reference copy) holding the same file, one of the same
    photos, or the same invoice (same store, vendor and invoice number) as `document`.
    """
    active = Document.status.notin_(INACTIVE_DOCUMENT_STATUSES)
    page_hashes = list((await session.execute(
        select(DocumentPage.file_hash).where(DocumentPage.document_id == document.id))).scalars().all())
    hashes = {document.file_hash, *page_hashes}
    shares_photo = exists().where(DocumentPage.document_id == Document.id, DocumentPage.file_hash.in_(hashes))
    same_file = (await session.execute(
        select(Document).where(Document.id != document.id, active,
                               or_(Document.file_hash.in_(hashes), shares_photo))
        .order_by(Document.created_at).limit(1)
    )).scalars().first()
    if same_file is not None:
        return same_file
    invoice = await _invoice_of(session, document.id)
    if invoice is None or not (invoice.invoice_number or "").strip():
        return None
    same_vendor = (Invoice.vendor_id == invoice.vendor_id if invoice.vendor_id is not None
                   else func.lower(func.coalesce(Invoice.vendor_name, "")) == (invoice.vendor_name or "").lower())
    store_id = invoice.store_id or document.store_id
    return (await session.execute(
        select(Document).join(Invoice, Invoice.document_id == Document.id)
        .where(Document.id != document.id, active, same_vendor,
               func.lower(Invoice.invoice_number) == invoice.invoice_number.strip().lower(),
               func.coalesce(Invoice.store_id, Document.store_id) == store_id if store_id
               else func.coalesce(Invoice.store_id, Document.store_id).is_(None))
        .order_by(Document.created_at).limit(1)
    )).scalars().first()


def _may_act_on_entry(entry: RecycleBinEntry, user: User) -> bool:
    if ROLE_RANK.get(user.role, 0) >= ROLE_RANK[UserRole.MANAGER.value]:
        return True
    return user.id is not None and user.id in (entry.uploaded_by_user_id, entry.binned_by_user_id)


async def _locked_entry(session: AsyncSession, entry_id: uuid.UUID, user: User) -> RecycleBinEntry:
    entry = (await session.execute(
        select(RecycleBinEntry).where(RecycleBinEntry.id == entry_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().first()
    if entry is None or not _may_act_on_entry(entry, user):
        raise RecordNotFoundError(message="Recycle Bin item not found.", detail={"entry_id": str(entry_id)})
    return entry


def _require_open(entry: RecycleBinEntry) -> None:
    if entry.status != ENTRY_IN_BIN:
        raise ValidationError(message=f"This item is no longer in the Recycle Bin ({entry.status.lower()}).",
                              detail={"entry_id": str(entry.id), "reason": "not_in_bin", "status": entry.status})


async def _close(entry: RecycleBinEntry, status: str, user: User, note: str | None) -> None:
    entry.status = status
    entry.closed_at = max(_now(), entry.binned_at)
    entry.closed_by_user_id, entry.closed_by, entry.closed_by_role = user.id, user.username, user.role
    entry.close_note = note


async def _cancel_pending(session: AsyncSession, entry: RecycleBinEntry, user: User, note: str,
                          keep: uuid.UUID | None = None) -> None:
    pending = (await session.execute(
        select(BinDeletionRequest).where(BinDeletionRequest.entry_id == entry.id,
                                         BinDeletionRequest.status == REQUEST_PENDING).with_for_update()
    )).scalars().all()
    for request in pending:
        if request.id != keep:
            _decide(request, REQUEST_CANCELLED, user, note)


@dataclass
class RestoreResult:
    entry: RecycleBinEntry
    document: Document
    as_reference: bool


async def restore(session: AsyncSession, entry_id: uuid.UUID, user: User, *, as_reference: bool = False,
                  note: str | None = None) -> RestoreResult:
    await require_schema(session)
    entry = await _locked_entry(session, entry_id, user)
    _require_open(entry)
    document = await _locked_document(session, entry.document_id)
    note = (note or "").strip() or None
    previous = entry.previous_status

    if previous == DocumentStatus.REFERENCE:
        # A reference copy that was binned comes back as the same reference copy.
        document.status = DocumentStatus.REFERENCE
        status, as_reference = ENTRY_RESTORED, True
    else:
        conflict = await active_conflict(session, document)
        if as_reference:
            _require_admin(user)
            if conflict is None:
                raise ValidationError(message="No active document holds this invoice; restore it normally.",
                                      detail={"entry_id": str(entry.id), "reason": "no_conflict"})
            document.status = DocumentStatus.REFERENCE
            document.reference_of_document_id = conflict.id
            status = ENTRY_RESTORED_AS_REFERENCE
        elif conflict is not None:
            raise RestoreConflictError(
                message=("An active document already holds this invoice, so it cannot be restored as it was. "
                         "An administrator can restore it as a read-only reference copy."),
                detail={"entry_id": str(entry.id), "conflicting_document_id": str(conflict.id),
                        "conflicting_filename": conflict.filename})
        else:
            document.status = (DocumentStatus.STOPPED if previous in _RESUMES_AS_STOPPED else previous)
            status = ENTRY_RESTORED

    await _close(entry, status, user, note)
    await _cancel_pending(session, entry, user, "Closed: the document was restored.")
    await session.flush()
    await _log(session, document, user, "document_restored",
               f"Restored from the Recycle Bin by {user.username} ({user.role})"
               + (" as a read-only reference copy." if status == ENTRY_RESTORED_AS_REFERENCE else "."),
               entry_id=str(entry.id), restored_status=document.status, note=note)
    logger.info("document_restored", document_id=str(document.id), entry_id=str(entry.id),
                actor_user_id=str(user.id), restored_status=document.status)
    return RestoreResult(entry, document, status == ENTRY_RESTORED_AS_REFERENCE or as_reference)


# ---- deletion requests --------------------------------------------------------------------------

def _decide(request: BinDeletionRequest, status: str, user: User, note: str | None) -> None:
    request.status = status
    request.decided_at = max(_now(), request.requested_at)
    request.decided_by_user_id, request.decided_by, request.decided_by_role = user.id, user.username, user.role
    request.decision_note = note


async def request_deletion(session: AsyncSession, entry_id: uuid.UUID, user: User,
                           reason: str | None) -> BinDeletionRequest:
    await require_schema(session)
    why = _reason(reason)
    entry = await _locked_entry(session, entry_id, user)
    _require_open(entry)
    request = BinDeletionRequest(entry_id=entry.id, requested_at=_now(), **_actor(user, "requested"),
                                 reason=why, status=REQUEST_PENDING)
    try:
        async with session.begin_nested():
            session.add(request)
            await session.flush()
    except IntegrityError as exc:
        if "uq_bin_deletion_requests_pending" not in str(exc.orig):
            raise
        raise ValidationError(message="A deletion request for this item is already waiting for an administrator.",
                              detail={"entry_id": str(entry.id), "reason": "already_requested"}) from exc
    logger.info("bin_deletion_requested", entry_id=str(entry.id), request_id=str(request.id), by=user.username)
    return request


async def _locked_request(session: AsyncSession, request_id: uuid.UUID, user: User) -> BinDeletionRequest:
    request = (await session.execute(
        select(BinDeletionRequest).where(BinDeletionRequest.id == request_id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().first()
    if request is None or not (_is_admin(user) or request.requested_by_user_id == user.id):
        raise RecordNotFoundError(message="Deletion request not found.", detail={"request_id": str(request_id)})
    if request.status != REQUEST_PENDING:
        raise ValidationError(message=f"This request was already {request.status.lower()}.",
                              detail={"request_id": str(request.id), "reason": "already_decided",
                                      "status": request.status})
    return request


async def cancel_request(session: AsyncSession, request_id: uuid.UUID, user: User) -> BinDeletionRequest:
    await require_schema(session)
    request = await _locked_request(session, request_id, user)
    _decide(request, REQUEST_CANCELLED, user, "Withdrawn by the requester." if not _is_admin(user) else None)
    await session.flush()
    return request


@dataclass
class PurgeResult:
    entry: RecycleBinEntry
    file_paths: list[str] = field(default_factory=list)


async def decide_request(session: AsyncSession, request_id: uuid.UUID, user: User, *, approve: bool,
                         note: str | None) -> tuple[BinDeletionRequest, PurgeResult | None]:
    """ADMIN: approve (the document is purged in the same transaction) or reject (it stays in the bin)."""
    await require_schema(session)
    _require_admin(user)
    request = await _locked_request(session, request_id, user)
    note = (note or "").strip() or None
    if not approve:
        if note is None:
            raise ValidationError(message="Say why the request is rejected; the requester sees it.",
                                  detail={"field": "note", "reason": "required"})
        _decide(request, REQUEST_REJECTED, user, note)
        await session.flush()
        return request, None
    entry = await _locked_entry(session, request.entry_id, user)
    result = await _purge_entry(session, entry, user, reason=f"Deletion request approved: {request.reason}",
                                note=note, keep_request=request.id)
    _decide(request, REQUEST_APPROVED, user, note)
    await session.flush()
    return request, result


# ---- permanent deletion -------------------------------------------------------------------------

async def _delete_document(session: AsyncSession, document: Document, user: User) -> list[str]:
    """Release the invoice's receipts (inside this transaction) and delete the document; return its file paths."""
    from app.services import inventory_approval

    documents = DocumentRepository(session)
    paths = [document.file_path] + [p.file_path for p in await documents.pages(document)
                                    if p.file_path != document.file_path]
    invoice = await _invoice_of(session, document.id)
    if invoice is not None:
        release = await inventory_approval.release_invoice_for_deletion(session, invoice.id, user)
        inventory_approval.log_deletion(release, invoice.id, user)
    await documents.delete(document)
    await session.flush()
    return paths


async def _purge_entry(session: AsyncSession, entry: RecycleBinEntry, user: User, *, reason: str,
                       note: str | None = None, keep_request: uuid.UUID | None = None) -> PurgeResult:
    _require_open(entry)
    document = await _locked_document(session, entry.document_id)
    invoice = await _invoice_of(session, document.id)
    if await edi_exported(session, invoice):
        raise ValidationError(message="This invoice was exported to EDI; it cannot be deleted from the Recycle Bin.",
                              detail={"entry_id": str(entry.id), "reason": "edi_exported"})
    await _close(entry, ENTRY_PURGED, user, note or reason)
    await _cancel_pending(session, entry, user, "Closed: the document was deleted.", keep=keep_request)
    await session.flush()               # PURGED before the document goes, so the entry may lose its link
    paths = await _delete_document(session, document, user)
    logger.info("document_purged", entry_id=str(entry.id), document_id=str(document.id), by=user.username)
    return PurgeResult(entry, paths)


async def purge(session: AsyncSession, entry_id: uuid.UUID, user: User, reason: str | None) -> PurgeResult:
    """ADMIN: permanently delete a document in the bin. Its entry stays as the record of what was deleted."""
    await require_schema(session)
    _require_admin(user)
    why = _reason(reason)
    entry = await _locked_entry(session, entry_id, user)
    return await _purge_entry(session, entry, user, reason=why)


async def record_direct_delete(session: AsyncSession, document: Document, user: User, reason: str | None) -> None:
    """
    ADMIN deleting a document outside the bin (DELETE /invoices/{id}, DELETE /documents/{id}): leave
    the same PURGED record, so the deletion is explained after the document is gone. Call before the
    document is deleted, in the same transaction. A document already in the bin closes its entry.
    """
    if not await bin_schema_ready(session):
        return
    why = (reason or "").strip()[:REASON_MAX_LENGTH] or "Deleted directly by an administrator (no reason given)."
    entry = await open_entry(session, document.id, lock=True)
    if entry is not None:
        await _close(entry, ENTRY_PURGED, user, why)
        await _cancel_pending(session, entry, user, "Closed: the document was deleted.")
    else:
        now = _now()
        invoice = await _invoice_of(session, document.id)
        session.add(RecycleBinEntry(**_snapshot(document, invoice), previous_status=document.status, binned_at=now,
                                    **_actor(user, "binned"), reason=why, status=ENTRY_PURGED, closed_at=now,
                                    closed_by_user_id=user.id, closed_by=user.username, closed_by_role=user.role,
                                    close_note=why))
    await session.flush()
