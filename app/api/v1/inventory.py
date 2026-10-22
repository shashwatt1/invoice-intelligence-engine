"""
Store inventory API — app/api/v1/inventory.py

Store-scoped: every store route refuses a source identity (only physical
stores have inventory) and every query is filtered by the store in the path;
a receipt always posts to its invoice's own store. MANAGER and ADMIN only.
Answers 404 while INVENTORY_ENABLED is off.
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
    MovementRow,
    OpeningCountRequest,
    ReceiptBlockerOut,
    ReceiptLineOut,
    ReceiptPostRequest,
    ReceiptPreviewOut,
    ReceiptReversalOut,
    ReceiptReversalRequest,
    ReceiptSummary,
    StoreInvoiceRow,
)
from app.schemas.processing import StoreRef
from app.services import inventory_service as inventory
from app.services.export_service import pdi_items
from app.services.receiving_eligibility import receiving_eligibility


async def _enabled() -> None:
    if not get_settings().inventory_enabled:
        raise RecordNotFoundError(message="Inventory is not enabled.", detail={"feature": "inventory"})


router = APIRouter(tags=["Inventory"], dependencies=[Depends(_enabled)])
# Outside the gate: whether to offer Inventory at all (the sidebar asks).
status_router = APIRouter(tags=["Inventory"])


@status_router.get("/inventory/status", response_model=APIResponse[dict], summary="Whether inventory is switched on")
async def inventory_status(user: User = Depends(require_manager)) -> APIResponse[dict]:
    return APIResponse(data={"enabled": get_settings().inventory_enabled})


def _receipt(row) -> ReceiptSummary:
    return ReceiptSummary.model_validate(row, from_attributes=True)


async def _preview_out(db: AsyncSession, invoice_id: uuid.UUID) -> ReceiptPreviewOut:
    p = await inventory.receipt_preview(db, invoice_id)
    return ReceiptPreviewOut(
        invoice_id=p.invoice.id, invoice_number=p.invoice.invoice_number,
        store=StoreRef.from_store(p.store) if p.store else None, eligibility_reasons=p.eligibility_reasons,
        lines=[ReceiptLineOut(**{k: getattr(line, k) for k in ReceiptLineOut.model_fields}) for line in p.lines],
        blockers=[ReceiptBlockerOut(**vars(b)) for b in p.blockers],
        receipt=_receipt(p.receipt) if p.receipt else None, history=[_receipt(r) for r in p.history],
        can_post=p.can_post,
    )


@router.get("/inventory/stores/{store_id}/overview", response_model=APIResponse[InventoryOverview],
            summary="A physical store's inventory at a glance")
async def overview(store_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                   user: User = Depends(require_manager)) -> APIResponse[InventoryOverview]:
    store = await inventory.physical_store(db, store_id)
    balances = await inventory.store_balances(db, store.id)
    return APIResponse(data=InventoryOverview(
        store=StoreRef.from_store(store), tracked_products=len(balances),
        opening_required=sum(1 for b in balances if b.status == inventory.STATUS_OPENING_REQUIRED),
        out_of_stock=sum(1 for b in balances if b.status == inventory.STATUS_OUT_OF_STOCK),
        in_stock=sum(1 for b in balances if b.status == inventory.STATUS_IN_STOCK),
        last_recorded_at=await inventory.last_recorded(db, store.id),
        recent_receipts=[_receipt(r) for r in await inventory.recent_receipts(db, store.id)],
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
    store = await inventory.physical_store(db, store_id)
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
    store = await inventory.physical_store(db, store_id)
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
    store = await inventory.physical_store(db, store_id)
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
            summary="What a receipt for this invoice would record — or the receipt already posted")
async def receipt_preview(invoice_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                          user: User = Depends(require_manager)) -> APIResponse[ReceiptPreviewOut]:
    return APIResponse(data=await _preview_out(db, invoice_id))


@router.post("/inventory/invoices/{invoice_id}/receipt", response_model=APIResponse[ReceiptPreviewOut],
             status_code=201, summary="Confirm the invoice's goods were received (MANAGER/ADMIN)",
             responses={422: {"description": "Not receivable, unresolved conversions, or a line needs a reason"}})
async def post_receipt(invoice_id: uuid.UUID, payload: ReceiptPostRequest, db: AsyncSession = Depends(get_db),
                       user: User = Depends(require_manager)) -> APIResponse[ReceiptPreviewOut]:
    await inventory.post_receipt(
        db, invoice_id, received_on=payload.received_on, note=payload.note, user=user,
        accepted={line.invoice_item_id: line.accepted_units for line in payload.lines},
        reasons={line.invoice_item_id: line.reason for line in payload.lines if line.reason},
    )
    await db.commit()
    return APIResponse(data=await _preview_out(db, invoice_id))


@router.post("/inventory/invoices/{invoice_id}/receipt/reverse", response_model=APIResponse[ReceiptReversalOut],
             summary="Reverse the invoice's receipt so it can be corrected and received again (MANAGER/ADMIN)",
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
