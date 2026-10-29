"""
Recycle Bin — app/api/v1/recycle_bin.py

    GET   /recycle-bin                                   items (default: in the bin)
    GET   /recycle-bin/summary                           counts for the sidebar
    GET   /recycle-bin/deletion-requests                 requests (default: pending)
    POST  /recycle-bin/{entry_id}/restore                restore (ADMIN: or as a reference copy)
    POST  /recycle-bin/{entry_id}/deletion-requests      ask an administrator to delete permanently
    POST  /recycle-bin/{entry_id}/purge                  ADMIN: delete permanently
    POST  /recycle-bin/deletion-requests/{id}/cancel     the requester (or an ADMIN)
    POST  /recycle-bin/deletion-requests/{id}/approve    ADMIN: approve, which deletes permanently
    POST  /recycle-bin/deletion-requests/{id}/reject     ADMIN: reject with a note

Moving a document in is POST /documents/{id}/move-to-bin. Who may see and do
what is decided in app.services.recycle_bin: a USER sees and acts on items it
uploaded or binned; MANAGER and ADMIN on every item; permanent deletion and
reference-copy restores are ADMIN only. Items another account may not see
are a plain 404.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin, require_authenticated_user
from app.core.exceptions import StorageError
from app.core.logging import get_logger
from app.database.session import get_db
from app.models.document import Document
from app.models.recycle_bin import (
    ENTRY_IN_BIN,
    REQUEST_PENDING,
    BinDeletionRequest,
    RecycleBinEntry,
)
from app.models.user import ROLE_RANK, User, UserRole
from app.repositories.store_repository import StoreRepository
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.processing import StoreRef
from app.schemas.recycle_bin import (
    DecisionResultOut,
    DeletionDecision,
    DeletionRequestCreate,
    DeletionRequestOut,
    PurgeRequest,
    PurgeResultOut,
    RecycleBinEntryOut,
    RecycleBinSummary,
    RestoreRequest,
    RestoreResultOut,
)
from app.services import recycle_bin as bin_service
from app.services.storage_service import get_storage_service

logger = get_logger(__name__)
router = APIRouter(prefix="/recycle-bin", tags=["Recycle Bin"], dependencies=[Depends(require_authenticated_user)])


def _sees_everything(user: User) -> bool:
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK[UserRole.MANAGER.value]


def _entry_scope(user: User):
    if _sees_everything(user):
        return true()
    return or_(RecycleBinEntry.uploaded_by_user_id == user.id, RecycleBinEntry.binned_by_user_id == user.id)


def _request_out(request: BinDeletionRequest, entry: RecycleBinEntry | None) -> DeletionRequestOut:
    return DeletionRequestOut(
        id=request.id, entry_id=request.entry_id, requested_at=request.requested_at,
        requested_by=request.requested_by, requested_by_role=request.requested_by_role, reason=request.reason,
        status=request.status, decided_at=request.decided_at, decided_by=request.decided_by,
        decision_note=request.decision_note, filename=entry.filename if entry else None,
        invoice_number=entry.invoice_number if entry else None, vendor_name=entry.vendor_name if entry else None,
    )


async def _entries_out(db: AsyncSession, entries: list[RecycleBinEntry]) -> list[RecycleBinEntryOut]:
    stores = await StoreRepository(db).labels([e.store_id for e in entries if e.store_id])
    ids = [e.id for e in entries]
    pending = {r.entry_id: r for r in (await db.execute(
        select(BinDeletionRequest).where(BinDeletionRequest.entry_id.in_(ids),
                                         BinDeletionRequest.status == REQUEST_PENDING)
    )).scalars().all()} if ids else {}
    out = []
    for entry in entries:
        conflict = None
        if entry.status == ENTRY_IN_BIN and entry.document_id is not None:
            document = await db.get(Document, entry.document_id)
            if document is not None:
                found = await bin_service.active_conflict(db, document)
                conflict = found.id if found else None
        out.append(RecycleBinEntryOut(
            id=entry.id, document_id=entry.document_id, filename=entry.filename, invoice_id=entry.invoice_id,
            invoice_number=entry.invoice_number, vendor_name=entry.vendor_name,
            store=StoreRef.from_store(stores[entry.store_id]) if entry.store_id in stores else None,
            previous_status=entry.previous_status, binned_at=entry.binned_at, binned_by=entry.binned_by,
            binned_by_role=entry.binned_by_role, reason=entry.reason, status=entry.status,
            closed_at=entry.closed_at, closed_by=entry.closed_by, close_note=entry.close_note,
            reuploaded_as_document_id=entry.reuploaded_as_document_id, conflicting_document_id=conflict,
            pending_request=_request_out(pending[entry.id], entry) if entry.id in pending else None,
        ))
    return out


async def _remove_files(paths: list[str], entry_id: uuid.UUID) -> None:
    """After the delete has committed: a leftover stored file is not user-visible, so failures are only logged."""
    for path in paths:
        try:
            await get_storage_service().delete(path)
        except StorageError as exc:
            logger.warning("bin_purge_file_cleanup_failed", entry_id=str(entry_id), file_path=path, error=str(exc))


@router.get("", response_model=PaginatedResponse[RecycleBinEntryOut], summary="Items in the Recycle Bin")
async def list_entries(
    status: Literal["IN_BIN", "RESTORED", "RESTORED_AS_REFERENCE", "PURGED"] = Query(default="IN_BIN"),
    search: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db), user: User = Depends(require_authenticated_user),
) -> PaginatedResponse[RecycleBinEntryOut]:
    await bin_service.require_schema(db)
    conditions = [RecycleBinEntry.status == status, _entry_scope(user)]
    term = (search or "").strip()
    if term:
        pattern = f"%{term}%"
        conditions.append(or_(RecycleBinEntry.filename.ilike(pattern), RecycleBinEntry.invoice_number.ilike(pattern),
                              RecycleBinEntry.vendor_name.ilike(pattern)))
    total = (await db.execute(select(func.count()).select_from(RecycleBinEntry).where(*conditions))).scalar_one()
    entries = list((await db.execute(
        select(RecycleBinEntry).where(*conditions)
        .order_by(RecycleBinEntry.binned_at.desc(), RecycleBinEntry.id)
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all())
    return PaginatedResponse(items=await _entries_out(db, entries), total=total, page=page, page_size=page_size)


@router.get("/summary", response_model=APIResponse[RecycleBinSummary], summary="Recycle Bin counts")
async def summary(db: AsyncSession = Depends(get_db),
                  user: User = Depends(require_authenticated_user)) -> APIResponse[RecycleBinSummary]:
    if not await bin_service.bin_schema_ready(db):
        return APIResponse(data=RecycleBinSummary(in_bin=0, pending_requests=0))
    in_bin = (await db.execute(select(func.count()).select_from(RecycleBinEntry).where(
        RecycleBinEntry.status == ENTRY_IN_BIN, _entry_scope(user)))).scalar_one()
    mine = true() if user.role == UserRole.ADMIN.value else BinDeletionRequest.requested_by_user_id == user.id
    pending = (await db.execute(select(func.count()).select_from(BinDeletionRequest).where(
        BinDeletionRequest.status == REQUEST_PENDING, mine))).scalar_one()
    return APIResponse(data=RecycleBinSummary(in_bin=in_bin, pending_requests=pending))


@router.get("/deletion-requests", response_model=PaginatedResponse[DeletionRequestOut],
            summary="Permanent-deletion requests (ADMIN: all; others: their own)")
async def list_requests(
    status: Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"] = Query(default="PENDING"),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db), user: User = Depends(require_authenticated_user),
) -> PaginatedResponse[DeletionRequestOut]:
    await bin_service.require_schema(db)
    conditions = [BinDeletionRequest.status == status]
    if user.role != UserRole.ADMIN.value:
        conditions.append(BinDeletionRequest.requested_by_user_id == user.id)
    total = (await db.execute(select(func.count()).select_from(BinDeletionRequest).where(*conditions))).scalar_one()
    rows = (await db.execute(
        select(BinDeletionRequest, RecycleBinEntry).join(RecycleBinEntry, RecycleBinEntry.id == BinDeletionRequest.entry_id)
        .where(*conditions).order_by(BinDeletionRequest.requested_at.desc(), BinDeletionRequest.id)
        .offset((page - 1) * page_size).limit(page_size)
    )).all()
    return PaginatedResponse(items=[_request_out(r, e) for r, e in rows], total=total, page=page, page_size=page_size)


@router.post("/{entry_id}/restore", response_model=APIResponse[RestoreResultOut],
             summary="Restore an item; ADMIN may restore a conflicting one as a read-only reference copy",
             responses={404: {"description": "Not found"}, 409: {"description": "An active document holds this invoice"},
                        422: {"description": "No longer in the bin"}})
async def restore(entry_id: uuid.UUID, body: RestoreRequest, db: AsyncSession = Depends(get_db),
                  user: User = Depends(require_authenticated_user)) -> APIResponse[RestoreResultOut]:
    result = await bin_service.restore(db, entry_id, user, as_reference=body.as_reference, note=body.note)
    await db.commit()
    [entry] = await _entries_out(db, [result.entry])
    return APIResponse(data=RestoreResultOut(entry=entry, document_status=result.document.status,
                                             as_reference=result.as_reference))


@router.post("/{entry_id}/deletion-requests", response_model=APIResponse[DeletionRequestOut], status_code=201,
             summary="Ask an administrator to delete an item permanently")
async def request_deletion(entry_id: uuid.UUID, body: DeletionRequestCreate, db: AsyncSession = Depends(get_db),
                           user: User = Depends(require_authenticated_user)) -> APIResponse[DeletionRequestOut]:
    request = await bin_service.request_deletion(db, entry_id, user, body.reason)
    await db.commit()
    entry = await db.get(RecycleBinEntry, request.entry_id)
    return APIResponse(data=_request_out(request, entry))


@router.post("/{entry_id}/purge", response_model=APIResponse[PurgeResultOut],
             summary="ADMIN: delete an item permanently (its record stays)")
async def purge(entry_id: uuid.UUID, body: PurgeRequest, db: AsyncSession = Depends(get_db),
                user: User = Depends(require_admin)) -> APIResponse[PurgeResultOut]:
    result = await bin_service.purge(db, entry_id, user, body.reason)
    await db.commit()
    await db.refresh(result.entry)
    await _remove_files(result.file_paths, entry_id)
    [entry] = await _entries_out(db, [result.entry])
    return APIResponse(data=PurgeResultOut(entry=entry))


@router.post("/deletion-requests/{request_id}/cancel", response_model=APIResponse[DeletionRequestOut],
             summary="Withdraw a pending deletion request")
async def cancel_request(request_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         user: User = Depends(require_authenticated_user)) -> APIResponse[DeletionRequestOut]:
    request = await bin_service.cancel_request(db, request_id, user)
    await db.commit()
    return APIResponse(data=_request_out(request, await db.get(RecycleBinEntry, request.entry_id)))


async def _decide(db: AsyncSession, request_id: uuid.UUID, user: User, body: DeletionDecision,
                  approve: bool) -> APIResponse[DecisionResultOut]:
    request, result = await bin_service.decide_request(db, request_id, user, approve=approve, note=body.note)
    await db.commit()
    entry = await db.get(RecycleBinEntry, request.entry_id)
    await db.refresh(entry)
    if result is not None:
        await _remove_files(result.file_paths, request.entry_id)
    [entry_out] = await _entries_out(db, [entry])
    return APIResponse(data=DecisionResultOut(request=_request_out(request, entry), entry=entry_out))


@router.post("/deletion-requests/{request_id}/approve", response_model=APIResponse[DecisionResultOut],
             summary="ADMIN: approve a deletion request (deletes the item permanently)")
async def approve_request(request_id: uuid.UUID, body: DeletionDecision, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require_admin)) -> APIResponse[DecisionResultOut]:
    return await _decide(db, request_id, user, body, approve=True)


@router.post("/deletion-requests/{request_id}/reject", response_model=APIResponse[DecisionResultOut],
             summary="ADMIN: reject a deletion request (the item stays in the bin)")
async def reject_request(request_id: uuid.UUID, body: DeletionDecision, db: AsyncSession = Depends(get_db),
                         user: User = Depends(require_admin)) -> APIResponse[DecisionResultOut]:
    return await _decide(db, request_id, user, body, approve=False)
