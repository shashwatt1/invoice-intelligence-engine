"""
Store inventory API — app/api/v1/inventory.py

Store-scoped and per-user: every route resolves its store through
app.services.store_access, so an account sees and changes inventory only for
physical stores it holds an active grant for (MANAGER and ADMIN alike; USER
never). A store id in the path that is unknown, a source identity or not
granted is the same 404. Invoice-linked routes (receipt review, approval,
rejection, reversal) are authorized inside the service on the invoice's or
receipt's own store, under the invoice lock, and refuse with the same 404 as
an unknown invoice. Every query is filtered by the authorized store. Answers
404 while INVENTORY_ENABLED is off, and while the database has not reached migration 0035.

There is no route that posts a receipt directly: marking the invoice Retailed
submits it, and POST .../receipt/approve posts a pending submission
(app/services/inventory_approval.py).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import require_manager
from app.core.exceptions import RecordNotFoundError
from app.database.session import get_db
from app.models.invoice import Invoice
from app.models.user import User
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.inventory import (
    InventoryItemRow,
    InventoryOverview,
    LineChangeOut,
    MovementRow,
    OpeningCountRequest,
    PendingApprovalRow,
    ReceiptApproveRequest,
    ReceiptBlockerOut,
    ReceiptLineOut,
    ReceiptPreviewOut,
    ReceiptRejectRequest,
    ReceiptReversalOut,
    ReceiptReversalRequest,
    ReceiptSummary,
    StoreInvoiceRow,
    SubmissionOut,
)
from app.schemas.processing import StoreRef
from app.services import inventory_approval as approval
from app.services import inventory_service as inventory
from app.services import store_access
from app.services.export_service import pdi_items
from app.services.receiving_eligibility import receiving_eligibility


async def _enabled(db: AsyncSession = Depends(get_db)) -> None:
    if not get_settings().inventory_enabled:
        raise RecordNotFoundError(message="Inventory is not enabled.", detail={"feature": "inventory"})
    # Switched on before its migration reached the database: refuse plainly rather than fail.
    if not await inventory.approval_schema_ready(db):
        raise RecordNotFoundError(message="Inventory is not available until its database migration (0035) is applied.",
                                  detail={"feature": "inventory", "reason": "inventory_schema_not_migrated"})


router = APIRouter(tags=["Inventory"], dependencies=[Depends(_enabled)])
# Outside the gate: whether to offer Inventory at all (the sidebar asks).
status_router = APIRouter(tags=["Inventory"])


@status_router.get("/inventory/status", response_model=APIResponse[dict], summary="Whether inventory is switched on")
async def inventory_status(db: AsyncSession = Depends(get_db),
                           user: User = Depends(require_manager)) -> APIResponse[dict]:
    """On only when switched on AND its migration (0035) is applied; the database is asked only when switched on."""
    enabled = get_settings().inventory_enabled and await inventory.approval_schema_ready(db)
    return APIResponse(data={"enabled": enabled})


def _receipt(row) -> ReceiptSummary:
    summary = ReceiptSummary.model_validate(row, from_attributes=True)
    submitter = getattr(row, "submitted_by_user_id", None)
    approver = getattr(row, "confirmed_by_user_id", None)
    return summary.model_copy(update={"self_approved": submitter is not None and submitter == approver})


def _line_out(line) -> ReceiptLineOut:
    return ReceiptLineOut(**{k: getattr(line, k) for k in ReceiptLineOut.model_fields})


def _submission_out(review: approval.Review) -> SubmissionOut | None:
    receipt = review.preview.submission
    if receipt is None:
        return None
    snapshot = receipt.submission_snapshot or {}
    return SubmissionOut(
        receipt=_receipt(receipt),
        proposed_lines=[ReceiptLineOut(**{k: line.get(k) for k in ReceiptLineOut.model_fields})
                        for line in snapshot.get("lines", [])],
        proposed_blockers=[ReceiptBlockerOut(**b) for b in snapshot.get("blockers", [])],
        export_number=(snapshot.get("export") or {}).get("number"),
        stale_reasons=review.stale_reasons,
        line_changes=[LineChangeOut(**vars(c)) for c in review.line_changes],
    )


async def _preview_out(db: AsyncSession, invoice_id: uuid.UUID, user: User) -> ReceiptPreviewOut:
    review = await approval.review(db, invoice_id, user)
    p = review.preview
    return ReceiptPreviewOut(
        invoice_id=p.invoice.id, invoice_number=p.invoice.invoice_number,
        store=StoreRef.from_store(p.store) if p.store else None, eligibility_reasons=p.eligibility_reasons,
        lines=[_line_out(line) for line in p.lines],
        blockers=[ReceiptBlockerOut(**vars(b)) for b in p.blockers],
        receipt=_receipt(p.receipt) if p.receipt else None, submission=_submission_out(review),
        history=[_receipt(r) for r in p.history], can_approve=review.can_approve,
    )


@router.get("/inventory/stores", response_model=APIResponse[list[StoreRef]],
            summary="The physical stores the signed-in account may use in inventory")
async def stores(db: AsyncSession = Depends(get_db),
                 user: User = Depends(require_manager)) -> APIResponse[list[StoreRef]]:
    return APIResponse(data=[StoreRef.from_store(s) for s in await store_access.accessible_stores(db, user)])


@router.get("/inventory/stores/{store_id}/overview", response_model=APIResponse[InventoryOverview],
            summary="A physical store's inventory at a glance")
async def overview(store_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                   user: User = Depends(require_manager)) -> APIResponse[InventoryOverview]:
    store = await store_access.ensure_store_access(db, user, store_id)
    balances = await inventory.store_balances(db, store.id)
    return APIResponse(data=InventoryOverview(
        store=StoreRef.from_store(store), tracked_products=len(balances),
        opening_required=sum(1 for b in balances if b.status == inventory.STATUS_OPENING_REQUIRED),
        out_of_stock=sum(1 for b in balances if b.status == inventory.STATUS_OUT_OF_STOCK),
        in_stock=sum(1 for b in balances if b.status == inventory.STATUS_IN_STOCK),
        last_recorded_at=await inventory.last_recorded(db, store.id),
        recent_receipts=[_receipt(r) for r in await inventory.recent_receipts(db, store.id)],
        pending_approvals=await approval.pending_count(db, store.id),
    ))


@router.get("/inventory/stores/{store_id}/items", response_model=PaginatedResponse[InventoryItemRow],
            summary="A physical store's products and their balances")
async def items(
    store_id: uuid.UUID,
    search: str | None = Query(default=None, max_length=64),
    status: Literal["OPENING_STOCK_REQUIRED", "OUT_OF_STOCK", "IN_STOCK"] | None = Query(default=None),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db), user: User = Depends(require_manager),
) -> PaginatedResponse[InventoryItemRow]:
    store = await store_access.ensure_store_access(db, user, store_id)
    rows = await inventory.store_balances(db, store.id)
    term = (search or "").strip().lower()
    if term:
        rows = [b for b in rows if term in b.item_code or term in (b.description or "").lower()]
    if status:
        rows = [b for b in rows if b.status == status]
    start = (page - 1) * page_size
    return PaginatedResponse(items=[InventoryItemRow(**vars(b)) for b in rows[start:start + page_size]],
                             total=len(rows), page=page, page_size=page_size)


@router.get("/inventory/stores/{store_id}/items/{item_code}/movements",
            response_model=APIResponse[list[MovementRow]], summary="Every movement behind one product's balance")
async def movements(store_id: uuid.UUID, item_code: str, db: AsyncSession = Depends(get_db),
                    user: User = Depends(require_manager)) -> APIResponse[list[MovementRow]]:
    store = await store_access.ensure_store_access(db, user, store_id)
    rows = await inventory.item_movements(db, store.id, item_code)
    invoice_ids = {m.invoice_id for m in rows if m.invoice_id}
    numbers = dict((await db.execute(
        select(Invoice.id, Invoice.invoice_number).where(Invoice.id.in_(invoice_ids))
    )).tuples().all()) if invoice_ids else {}
    return APIResponse(data=[
        MovementRow(**{k: getattr(m, k) for k in MovementRow.model_fields
                       if k not in ("recorded_at", "invoice_number")},
                    recorded_at=m.created_at, invoice_number=numbers.get(m.invoice_id) if m.invoice_id else None)
        for m in rows
    ])


@router.get("/inventory/stores/{store_id}/invoices", response_model=PaginatedResponse[StoreInvoiceRow],
            summary="A physical store's invoices and their receipts")
async def invoices(store_id: uuid.UUID, page: int = Query(default=1, ge=1),
                   page_size: int = Query(default=25, ge=1, le=100), db: AsyncSession = Depends(get_db),
                   user: User = Depends(require_manager)) -> PaginatedResponse[StoreInvoiceRow]:
    store = await store_access.ensure_store_access(db, user, store_id)
    rows, receipts, total = await inventory.store_invoices(db, store.id, page=page, page_size=page_size)
    return PaginatedResponse(items=[
        StoreInvoiceRow(
            invoice_id=i.id, invoice_number=i.invoice_number, invoice_date=i.invoice_date,
            vendor=i.vendor.label if i.vendor else i.vendor_name, status=i.status,
            product_lines=len(pdi_items(i)),
            receipt=_receipt(receipts[i.id]) if i.id in receipts else None,
            receivable_reasons=list(receiving_eligibility(i, store, i.document).reasons),
        )
        for i in rows
    ], total=total, page=page, page_size=page_size)


@router.get("/inventory/invoices/{invoice_id}/receipt", response_model=APIResponse[ReceiptPreviewOut],
            summary="The invoice's receipt: the pending submission (with staleness), the posted receipt, history")
async def receipt_preview(invoice_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require_manager)) -> APIResponse[ReceiptPreviewOut]:
    return APIResponse(data=await _preview_out(db, invoice_id, user))


@router.get("/inventory/stores/{store_id}/approvals", response_model=APIResponse[list[PendingApprovalRow]],
            summary="Receipt submissions awaiting approval at a physical store, oldest first")
async def approvals(store_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                    user: User = Depends(require_manager)) -> APIResponse[list[PendingApprovalRow]]:
    rows = await approval.pending_approvals(db, store_id, user)
    return APIResponse(data=[
        PendingApprovalRow(
            receipt=_receipt(row.receipt), invoice_id=row.invoice.id if row.invoice else None,
            invoice_number=row.receipt.invoice_number,
            invoice_date=row.invoice.invoice_date if row.invoice else None,
            vendor=(row.invoice.vendor.label if row.invoice.vendor else row.invoice.vendor_name) if row.invoice else None,
            stale_reasons=row.stale_reasons, blockers=row.blockers,
        )
        for row in rows
    ])


@router.post("/inventory/invoices/{invoice_id}/receipt/approve", response_model=APIResponse[ReceiptPreviewOut],
             summary="Approve a pending receipt submission and post it (MANAGER/ADMIN with a grant for the store)",
             responses={404: {"description": "No such submission, or no access to its store"},
                        422: {"description": "Not pending, stale, not receivable, or a line needs a reason"}})
async def approve_receipt(invoice_id: uuid.UUID, payload: ReceiptApproveRequest, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require_manager)) -> APIResponse[ReceiptPreviewOut]:
    await approval.approve_receipt(
        db, invoice_id, receipt_id=payload.receipt_id, received_on=payload.received_on, note=payload.note,
        user=user, accepted={line.invoice_item_id: line.accepted_units for line in payload.lines},
        reasons={line.invoice_item_id: line.reason for line in payload.lines if line.reason},
    )
    await db.commit()
    return APIResponse(data=await _preview_out(db, invoice_id, user))


@router.post("/inventory/invoices/{invoice_id}/receipt/reject", response_model=APIResponse[ReceiptPreviewOut],
             summary="Reject a pending receipt submission, with a reason (MANAGER/ADMIN with a grant for the store)",
             responses={404: {"description": "No such submission, or no access to its store"},
                        422: {"description": "Not pending, or no reason"}})
async def reject_receipt(invoice_id: uuid.UUID, payload: ReceiptRejectRequest, db: AsyncSession = Depends(get_db),
                         user: User = Depends(require_manager)) -> APIResponse[ReceiptPreviewOut]:
    await approval.reject_receipt(db, invoice_id, receipt_id=payload.receipt_id, reason=payload.reason, user=user)
    await db.commit()
    return APIResponse(data=await _preview_out(db, invoice_id, user))


@router.post("/inventory/invoices/{invoice_id}/receipt/reverse", response_model=APIResponse[ReceiptReversalOut],
             summary="Reverse the invoice's posted receipt (MANAGER/ADMIN); the Retailed mark is not changed",
             responses={404: {"description": "No such receipt on this invoice"}})
async def reverse_receipt(invoice_id: uuid.UUID, payload: ReceiptReversalRequest, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require_manager)) -> APIResponse[ReceiptReversalOut]:
    result = await inventory.reverse_receipt(db, invoice_id, receipt_id=payload.receipt_id, reason=payload.reason,
                                             user=user)
    await db.commit()
    return APIResponse(data=ReceiptReversalOut(receipt=_receipt(result.receipt),
                                               movements_reversed=len(result.movements),
                                               already_reversed=result.already_reversed))


@router.post("/inventory/stores/{store_id}/opening-counts", response_model=APIResponse[dict], status_code=201,
             summary="Record audited opening counts for a physical store (MANAGER/ADMIN)")
async def opening_counts(store_id: uuid.UUID, payload: OpeningCountRequest, db: AsyncSession = Depends(get_db),
                         user: User = Depends(require_manager)) -> APIResponse[dict]:
    recorded = await inventory.record_opening_counts(
        db, store_id, count_date=payload.count_date, reason=payload.reason, user=user,
        entries=[inventory.OpeningCountEntry(e.item_code, e.quantity, e.description) for e in payload.entries],
    )
    await db.commit()
    return APIResponse(data={"recorded": len(recorded)})
