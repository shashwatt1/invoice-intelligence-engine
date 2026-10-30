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
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import require_admin, require_manager
from app.core.exceptions import RecordNotFoundError, ValidationError
from app.core.logging import get_logger
from app.database.session import get_db
from app.models.invoice import Invoice
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.inventory import (
    InventoryAllStores,
    InventoryBaselineOut,
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
    StoreInventoryEntry,
    StoreInvoiceRow,
    SubmissionOut,
    SupplyOptions,
    SupplyRow,
    SupplyVendorOption,
    ZeroStartRequest,
    ZeroStartResult,
)
from app.schemas.processing import StoreRef
from app.services import inventory_approval as approval
from app.services import inventory_baseline as baselines
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


_logger = get_logger(__name__)
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


async def _overview_out(db: AsyncSession, store, opening: inventory.OpeningSummary | None = None) -> InventoryOverview:
    """One authorized store's overview. The caller has already checked the grant."""
    balances = await inventory.store_balances(db, store.id)
    baseline = await inventory.store_baseline(db, store.id)
    if opening is None:
        opening = (await inventory.opening_summaries(db, [store.id])).get(store.id) or inventory.OpeningSummary()
    return InventoryOverview(
        store=StoreRef.from_store(store), tracked_products=len(balances),
        opening_required=sum(1 for b in balances if b.status == inventory.STATUS_OPENING_REQUIRED),
        out_of_stock=sum(1 for b in balances if b.status == inventory.STATUS_OUT_OF_STOCK),
        in_stock=sum(1 for b in balances if b.status == inventory.STATUS_IN_STOCK),
        last_recorded_at=await inventory.last_recorded(db, store.id),
        recent_receipts=[_receipt(r) for r in await inventory.recent_receipts(db, store.id)],
        pending_approvals=await approval.pending_count(db, store.id),
        baseline=InventoryBaselineOut.model_validate(baseline, from_attributes=True) if baseline else None,
        opening_state=inventory.opening_state(baseline is not None, opening.counted_products),
        opening_counted_products=opening.counted_products, opening_last_counted_on=opening.last_counted_on,
        opening_last_counted_by=opening.last_counted_by, on_hand_units=inventory.on_hand_units(balances),
    )


@router.get("/inventory/overview", response_model=APIResponse[InventoryAllStores],
            summary="Every physical store this account can see, with its inventory summary")
async def all_stores(db: AsyncSession = Depends(get_db),
                     user: User = Depends(require_manager)) -> APIResponse[InventoryAllStores]:
    """
    The Inventory page's store list. A configured physical store is listed whether or not it has invoices,
    receipts or movements. A MANAGER sees the stores granted to them; an ADMIN also sees every other active
    physical store as an entry without figures (`access_granted` false) so a missing grant is visible.
    Figures are computed only for stores the account holds a grant for.
    """
    listed = await store_access.visible_physical_stores(db, user)
    opening = await inventory.opening_summaries(db, [s.id for s, granted in listed if granted])
    entries = [
        StoreInventoryEntry(
            store=StoreRef.from_store(store), access_granted=granted,
            overview=await _overview_out(db, store, opening.get(store.id) or inventory.OpeningSummary())
            if granted else None)
        for store, granted in listed
    ]
    return APIResponse(data=InventoryAllStores(
        stores=entries, total_physical=len(entries), not_granted=sum(1 for e in entries if not e.access_granted)))


@router.get("/inventory/stores/{store_id}/overview", response_model=APIResponse[InventoryOverview],
            summary="A physical store's inventory at a glance")
async def overview(store_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                   user: User = Depends(require_manager)) -> APIResponse[InventoryOverview]:
    store = await store_access.ensure_store_access(db, user, store_id)
    return APIResponse(data=await _overview_out(db, store))


@router.post("/inventory/stores/{store_id}/zero-start", response_model=APIResponse[ZeroStartResult],
             summary="Start a store's inventory at zero (ADMIN; once per store, idempotent)")
async def zero_start(store_id: uuid.UUID, payload: ZeroStartRequest, db: AsyncSession = Depends(get_db),
                     user: User = Depends(require_admin)) -> APIResponse[ZeroStartResult]:
    """
    Declare that this store starts tracking at zero on a date. It is NOT a physical count: the store is shown
    as "Zero start", products without their own opening count start at zero on that date and every approved
    receipt counts. Writes one store_inventory_baselines row and nothing else — no movement, no receipt, no
    balance change for a product that has its own count. Repeating it writes nothing and returns the existing
    start (`created` false). Needs the administrator's grant for the store, like every store route.
    """
    store = await store_access.ensure_store_access(db, user, store_id)
    if not await inventory.baseline_schema_ready(db):
        raise ValidationError(message="Migration 0036 (store_inventory_baselines) is not applied.",
                              detail={"reason": "schema_missing"})
    why = baselines.require_reason(payload.reason)
    today = datetime.now(UTC).date()
    when = payload.initialization_date or today
    if when > today:
        raise ValidationError(message="The start date cannot be in the future.",
                              detail={"field": "initialization_date", "reason": "future_date"})
    result = await baselines.initialize_store(db, store.id, actor=user, reason=why, initialization_date=when)
    if result.outcome == baselines.FAILED:
        raise ValidationError(message=result.detail, detail={"field": "initialization_date", "reason": "after_first_receipt"})
    created = result.outcome == baselines.INITIALIZED
    baseline = await inventory.store_baseline(db, store.id)
    await db.commit()
    _logger.info("inventory_zero_start", store_id=str(store.id), actor=user.username, created=created,
                 baseline_date=str(baseline.baseline_date) if baseline else None)
    return APIResponse(data=ZeroStartResult(
        created=created, baseline=InventoryBaselineOut.model_validate(baseline, from_attributes=True)))


@router.get("/inventory/stores/{store_id}/items", response_model=PaginatedResponse[InventoryItemRow],
            summary="A physical store's products and their balances")
async def items(
    store_id: uuid.UUID,
    search: str | None = Query(default=None, max_length=64),
    status: Literal["OPENING_STOCK_REQUIRED", "OUT_OF_STOCK", "IN_STOCK"] | None = Query(default=None),
    sort: Literal["code", "name", "on_hand_desc", "on_hand_asc", "received_desc", "recent"] = Query(default="code"),
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
    rows = _sorted_items(rows, sort)
    start = (page - 1) * page_size
    return PaginatedResponse(items=[InventoryItemRow(**vars(b)) for b in rows[start:start + page_size]],
                             total=len(rows), page=page, page_size=page_size)


def _sorted_items(rows: list, sort: str) -> list:
    """Balances arrive sorted by item code. Products without an on-hand figure (no opening
    count yet) sort after those with one, either way; ties keep item-code order."""
    if sort == "name":
        return sorted(rows, key=lambda b: ((b.description or "").lower() or "\uffff", b.item_code))
    if sort in ("on_hand_desc", "on_hand_asc"):
        sign = -1 if sort == "on_hand_desc" else 1
        return sorted(rows, key=lambda b: (b.on_hand is None, sign * (b.on_hand or 0)))
    if sort == "received_desc":
        return sorted(rows, key=lambda b: -b.received)
    if sort == "recent":
        return sorted(rows, key=lambda b: (b.last_movement is None, -(b.last_movement.toordinal() if b.last_movement else 0)))
    return rows


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
                   page_size: int = Query(default=25, ge=1, le=100),
                   search: str | None = Query(default=None, max_length=64),
                   receipt: Literal["awaiting", "received", "not_received"] | None = Query(default=None),
                   db: AsyncSession = Depends(get_db),
                   user: User = Depends(require_manager)) -> PaginatedResponse[StoreInvoiceRow]:
    store = await store_access.ensure_store_access(db, user, store_id)
    rows, receipts, total = await inventory.store_invoices(db, store.id, page=page, page_size=page_size,
                                                          search=search, receipt=receipt)
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


@router.get("/inventory/supply/options", response_model=APIResponse[SupplyOptions],
            summary="Stores and vendors for supply-history filters (only granted stores)")
async def supply_options(db: AsyncSession = Depends(get_db),
                         user: User = Depends(require_manager)) -> APIResponse[SupplyOptions]:
    stores = await store_access.accessible_stores(db, user)
    links = await inventory.supply_vendors(db, [s.id for s in stores])
    vendors = (await db.execute(select(Vendor).where(Vendor.id.in_(links)))).scalars().all() if links else []
    order = {s.id: i for i, s in enumerate(stores)}
    return APIResponse(data=SupplyOptions(
        stores=[StoreRef.from_store(s) for s in stores],
        vendors=sorted((SupplyVendorOption(id=v.id, label=v.label, store_ids=sorted(links[v.id], key=order.get))
                        for v in vendors), key=lambda o: o.label.lower()),
    ))


@router.get("/inventory/supply", response_model=PaginatedResponse[SupplyRow],
            summary="Supply history across granted stores, by vendor, store and delivery date")
async def supply(
    vendor_id: uuid.UUID | None = Query(default=None),
    store_id: uuid.UUID | None = Query(default=None),
    delivered_from: date | None = Query(default=None, description="Inclusive. With delivered_to equal, one day."),
    delivered_to: date | None = Query(default=None, description="Inclusive."),
    delivery: Literal["missing", "recorded"] | None = Query(default=None),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db), user: User = Depends(require_manager),
) -> PaginatedResponse[SupplyRow]:
    """Every filter combines with AND. Supply history records deliveries; it is not stock on hand."""
    if delivered_from and delivered_to and delivered_from > delivered_to:
        raise ValidationError(message="The delivery-date range ends before it starts.",
                              detail={"delivered_from": str(delivered_from), "delivered_to": str(delivered_to)})
    if delivery == "missing" and (delivered_from or delivered_to):
        raise ValidationError(message="Invoices without a delivery date cannot also be filtered by delivery date.",
                              detail={"delivery": delivery})
    if store_id is not None:
        stores = [await store_access.ensure_store_access(db, user, store_id)]   # same 404 as other routes
    else:
        stores = await store_access.accessible_stores(db, user)
    if vendor_id is not None and await db.get(Vendor, vendor_id) is None:
        raise RecordNotFoundError(message="Vendor not found.", detail={"vendor_id": str(vendor_id)})
    by_id = {s.id: s for s in stores}
    rows, receipts, total = await inventory.supply_history(
        db, list(by_id), page=page, page_size=page_size, vendor_id=vendor_id,
        delivered_from=delivered_from, delivered_to=delivered_to, delivery=delivery)
    items = []
    for i in rows:
        store, receipt = by_id[i.store_id], receipts.get(i.id)
        items.append(SupplyRow(
            invoice_id=i.id, invoice_number=i.invoice_number, invoice_date=i.invoice_date,
            vendor=i.vendor.label if i.vendor else i.vendor_name, vendor_id=i.vendor_id, status=i.status,
            product_lines=len(pdi_items(i)), receipt=_receipt(receipt) if receipt else None,
            receivable_reasons=list(receiving_eligibility(i, store, i.document).reasons),
            store=StoreRef.from_store(store),
            delivered_on=receipt.received_on if receipt is not None and receipt.status == "POSTED" else None,
        ))
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size)


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
