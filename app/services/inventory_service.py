"""
Store inventory — app/services/inventory_service.py

Receipts and opening counts become movements in the append-only ledger
(app/models/inventory.py); balances are folded from those movements and
never stored.

Rules (business decisions, 2026-10-09):
  * Opening stock comes only from an audited physical count (OPENING_COUNT),
    in sellable units, with its count date, actor and reason. Until a product
    has one, it shows "Opening stock required", never an on-hand figure. The
    count is the closing stock of its count date: movements on or before that
    date are already in it; only later movements change the balance. The POS
    "Current Inventory Count" is never used.
  * A receipt is a person's explicit confirmation — never inferred from an
    invoice being validated or exported. It may be posted only for an invoice
    that receiving_eligibility allows (VALIDATED, physical store, document not
    withdrawn), and only when every line the PDI export would carry (product,
    quantity > 0, not voided) has an approved conversion under the live
    commercial resolution — otherwise the whole receipt is blocked and the
    blocking products are named. Each line's accepted quantity is recorded in
    sellable units; one that differs from expected (invoice quantity x units
    per case) needs a reason. The invoice and its correction history are
    never touched.
  * Every movement has a unique idempotency key: the same receipt line or
    opening count can never post twice.
  * A POSTED receipt locks the invoice: no change to its lines, store or
    document (ensure_invoice_changeable) until the receipt is reversed. A
    reversal compensates every movement (same business date, pointing at the
    original), keeps the receipt as history and frees the invoice to be
    corrected and received again. Posting, reversing and every guarded change
    take the same row lock on the invoice, so they cannot interleave.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import InvoiceReceivedError, RecordNotFoundError, ValidationError
from app.models.inventory import (
    BASE_UNIT_SELLABLE,
    MOVEMENT_OPENING_COUNT,
    MOVEMENT_RECEIPT,
    RECEIPT_POSTED,
    RECEIPT_REVERSED,
    InventoryMovement,
    InventoryReceipt,
)
from app.models.invoice import Invoice
from app.models.store import Store
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.store_repository import StoreRepository
from app.services.case_mapping_service import invoice_commercial_resolution
from app.services.export_service import normalize_item_code, pdi_items
from app.services.product_master.commercial_resolution import (
    PATH_CONFLICT,
    PATH_LEGACY_FALLBACK,
    PATH_PRODUCT_MASTER_GLOBAL,
    PATH_PRODUCT_MASTER_PHYSICAL,
    CommercialResolution,
)
from app.services.receiving_eligibility import receiving_eligibility
from app.services.store_guard import ensure_physical_store

# Conversions the receipt accepts: exactly the ones the EDI export accepts.
APPROVED_PATHS = frozenset({PATH_PRODUCT_MASTER_PHYSICAL, PATH_PRODUCT_MASTER_GLOBAL, PATH_LEGACY_FALLBACK})

STATUS_OPENING_REQUIRED = "OPENING_STOCK_REQUIRED"
STATUS_OUT_OF_STOCK = "OUT_OF_STOCK"
STATUS_IN_STOCK = "IN_STOCK"
VALID_STATUSES = frozenset({STATUS_OPENING_REQUIRED, STATUS_OUT_OF_STOCK, STATUS_IN_STOCK})

SOURCE_UNIT_CASE = "CASE"


# ---- receipt lines (pure) ---------------------------------------------------------

@dataclass(frozen=True)
class ReceiptLine:
    invoice_item_id: uuid.UUID
    sort_order: int
    description: str | None
    item_code: str
    cases: Decimal
    units_per_case: int
    expected_units: Decimal
    conversion_path: str
    conversion_mapping_id: str | None
    conversion_scope: str | None


@dataclass(frozen=True)
class ReceiptBlocker:
    sort_order: int
    description: str | None
    item_code: str | None
    reason: str


def build_receipt_lines(invoice, resolution: CommercialResolution) -> tuple[list[ReceiptLine], list[ReceiptBlocker]]:
    """
    The lines a receipt covers — exactly those the PDI export would carry
    (pdi_items: no charges, voided or duplicate rows, no zero-quantity shorts) —
    each converted to sellable units, or the reason it cannot be.
    """
    lines: list[ReceiptLine] = []
    blockers: list[ReceiptBlocker] = []
    for item in pdi_items(invoice):
        code = normalize_item_code(item.product_sku)
        if code is None:
            blockers.append(ReceiptBlocker(item.sort_order, item.description, None,
                                           "no item code: the line cannot be keyed to a product"))
            continue
        resolved = resolution.lines.get(code)
        if resolved is None or resolved.path not in APPROVED_PATHS or resolved.units_per_case is None:
            why = ("Product Master mappings for this item disagree" if resolved and resolved.path == PATH_CONFLICT
                   else "no approved units-per-case mapping")
            blockers.append(ReceiptBlocker(item.sort_order, item.description, code, why))
            continue
        cases = Decimal(str(item.quantity))
        lines.append(ReceiptLine(
            invoice_item_id=item.id, sort_order=item.sort_order, description=item.description, item_code=code,
            cases=cases, units_per_case=resolved.units_per_case, expected_units=cases * resolved.units_per_case,
            conversion_path=resolved.path, conversion_mapping_id=resolved.mapping_id,
            conversion_scope=resolved.scope_label,
        ))
    return lines, blockers


def accepted_quantities(
    lines: Sequence[ReceiptLine], accepted: Mapping[uuid.UUID, Decimal], reasons: Mapping[uuid.UUID, str],
) -> list[tuple[ReceiptLine, Decimal, str | None]]:
    """
    Each line with the quantity the receiver accepted, in sellable units.
    Every line must be stated (the form prefills the expected quantity); a
    quantity must be a whole number of at least 0; one that differs from the
    expected quantity needs a reason. Refuses the whole receipt otherwise.
    """
    by_id = {line.invoice_item_id: line for line in lines}
    problems: dict[str, str] = {}
    for unknown in set(accepted) - set(by_id):
        problems[str(unknown)] = "not a line this receipt covers"
    result = []
    for line in lines:
        key = str(line.invoice_item_id)
        if line.invoice_item_id not in accepted:
            problems[key] = "accepted quantity missing"
            continue
        quantity = Decimal(str(accepted[line.invoice_item_id]))
        if quantity < 0:
            problems[key] = "accepted quantity cannot be negative"
            continue
        if quantity != quantity.to_integral_value():
            problems[key] = "accepted quantity must be a whole number of sellable units"
            continue
        reason = (reasons.get(line.invoice_item_id) or "").strip() or None
        if quantity != line.expected_units and reason is None:
            problems[key] = f"accepted {quantity} differs from expected {line.expected_units}: a reason is required"
            continue
        result.append((line, quantity, reason))
    if problems:
        raise ValidationError(message=f"{len(problems)} line(s) cannot be received as entered; nothing was posted.",
                              detail={"lines": problems})
    return result


# ---- balances (pure) ----------------------------------------------------------------

@dataclass
class ItemBalance:
    item_code: str
    description: str | None = None
    opening_date: date | None = None
    opening_quantity: Decimal | None = None
    received: Decimal = Decimal("0")          # receipts after the opening count (all receipts when there is none)
    sold: Decimal = Decimal("0")
    other: Decimal = Decimal("0")             # returns, transfers, adjustments
    on_hand: Decimal | None = None            # only once an opening count exists
    status: str = STATUS_OPENING_REQUIRED
    last_movement: date | None = None


def fold_balances(rows: Sequence[Any]) -> list[ItemBalance]:
    """
    Per item code, from movements summed by (item_code, movement_type,
    business_date): the opening count, what moved after it, and the on-hand
    balance — none until an opening count exists. Sorted by item code.
    """
    items: dict[str, ItemBalance] = {}
    openings: dict[str, tuple[date, Decimal]] = {}
    for row in rows:
        balance = items.setdefault(row["item_code"], ItemBalance(row["item_code"]))
        balance.description = row.get("description") or balance.description
        day = row["business_date"]
        balance.last_movement = max(filter(None, (balance.last_movement, day)))
        if row["movement_type"] == MOVEMENT_OPENING_COUNT:
            openings[row["item_code"]] = (day, Decimal(str(row["quantity"])))
    for row in rows:
        if row["movement_type"] == MOVEMENT_OPENING_COUNT:
            continue
        balance = items[row["item_code"]]
        opening = openings.get(row["item_code"])
        if opening is not None and row["business_date"] <= opening[0]:
            continue                                   # already in the count
        quantity = Decimal(str(row["quantity"]))
        if row["movement_type"] == MOVEMENT_RECEIPT:
            balance.received += quantity
        elif row["movement_type"] == "SALE":
            balance.sold += quantity
        else:
            balance.other += quantity
    for code, balance in items.items():
        if code in openings:
            balance.opening_date, balance.opening_quantity = openings[code]
            balance.on_hand = balance.opening_quantity + balance.received + balance.sold + balance.other
            balance.status = STATUS_OUT_OF_STOCK if balance.on_hand <= 0 else STATUS_IN_STOCK
    return [items[code] for code in sorted(items)]


# ---- database ----------------------------------------------------------------------

_ledger_present = False


async def _ledger_exists(session: AsyncSession) -> bool:
    """
    Whether migration 0031 has been applied. The correction guard runs on
    every invoice change, whatever INVENTORY_ENABLED says (a receipt must keep
    locking its invoice even if the feature is switched off again), so it must
    not fail on a database the migration has not reached yet. Once seen, the
    tables are assumed to stay.
    """
    global _ledger_present
    if not _ledger_present:
        _ledger_present = bool((await session.execute(
            text("SELECT to_regclass('public.inventory_receipts') IS NOT NULL")
        )).scalar())
    return _ledger_present


async def lock_invoice(session: AsyncSession, invoice_id: uuid.UUID) -> None:
    """Row lock on the invoice until the transaction ends — taken first by posting, reversing and every guarded change."""
    await session.execute(select(Invoice.id).where(Invoice.id == invoice_id).with_for_update())


async def posted_receipt(session: AsyncSession, invoice_id: uuid.UUID) -> InventoryReceipt | None:
    return (await session.execute(
        select(InventoryReceipt).where(InventoryReceipt.invoice_id == invoice_id,
                                       InventoryReceipt.status == RECEIPT_POSTED)
    )).scalars().first()


async def ensure_invoice_changeable(session: AsyncSession, invoice_id: uuid.UUID, action: str) -> None:
    """
    Lock the invoice and refuse `action` while a POSTED receipt holds it.
    Call before any write that changes what a receipt would have recorded —
    a line's quantity, code or type, the lines themselves, the store, or the
    document's standing. Holds the lock until the caller's transaction ends.
    """
    await lock_invoice(session, invoice_id)
    if not await _ledger_exists(session):
        return
    receipt = await posted_receipt(session, invoice_id)
    if receipt is not None:
        raise InvoiceReceivedError(
            message=(f"This invoice was received into inventory on {receipt.received_on.isoformat()} by "
                     f"{receipt.confirmed_by}. Reverse the receipt (Inventory → Invoices & receipts) before "
                     "changing it."),
            detail={"invoice_id": str(invoice_id), "reason": "invoice_received", "action": action,
                    "receipt_id": str(receipt.id)},
        )


async def physical_store(session: AsyncSession, store_id: uuid.UUID) -> Store:
    """The store, refused unless it is a physical store (never a source identity such as 47708760)."""
    store = await StoreRepository(session).get(store_id)
    if store is None:
        raise RecordNotFoundError(message="Store not found.", detail={"store_id": str(store_id)})
    return ensure_physical_store(store)


async def ensure_document_changeable(session: AsyncSession, document_id: uuid.UUID, action: str) -> None:
    """ensure_invoice_changeable for the document's invoice, if it has one."""
    invoice_id = (await session.execute(
        select(Invoice.id).where(Invoice.document_id == document_id)
    )).scalars().first()
    if invoice_id is not None:
        await ensure_invoice_changeable(session, invoice_id, action)


@dataclass
class ReceiptPreview:
    invoice: Invoice
    store: Store | None
    eligibility_reasons: list[str]
    lines: list[ReceiptLine]
    blockers: list[ReceiptBlocker]
    receipt: InventoryReceipt | None                 # the POSTED one, if any
    resolution: CommercialResolution = field(repr=False, default_factory=CommercialResolution)
    history: list[InventoryReceipt] = field(default_factory=list)   # every receipt, oldest first

    @property
    def can_post(self) -> bool:
        return not self.eligibility_reasons and not self.blockers and self.receipt is None and bool(self.lines)


async def receipt_preview(session: AsyncSession, invoice_id: uuid.UUID) -> ReceiptPreview:
    invoice = await InvoiceRepository(session).get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    store = await StoreRepository(session).get(invoice.store_id) if invoice.store_id else None
    eligibility = receiving_eligibility(invoice, store, invoice.document)
    resolution = await invoice_commercial_resolution(session, invoice)
    lines, blockers = build_receipt_lines(invoice, resolution)
    history = list((await session.execute(
        select(InventoryReceipt).where(InventoryReceipt.invoice_id == invoice.id)
        .order_by(InventoryReceipt.created_at)
    )).scalars().all())
    receipt = next((r for r in history if r.status == RECEIPT_POSTED), None)
    return ReceiptPreview(invoice, store, list(eligibility.reasons), lines, blockers, receipt, resolution, history)


async def post_receipt(
    session: AsyncSession, invoice_id: uuid.UUID, *, accepted: Mapping[uuid.UUID, Decimal],
    reasons: Mapping[uuid.UUID, str], received_on: date, note: str | None, user,
) -> InventoryReceipt:
    """Record a confirmed receipt and its movements in one transaction. Flushes; the caller commits."""
    await lock_invoice(session, invoice_id)     # a concurrent correction waits, or has already committed
    preview = await receipt_preview(session, invoice_id)
    if preview.receipt is not None:
        raise ValidationError(message="This invoice has already been received.",
                              detail={"invoice_id": str(invoice_id), "reason": "already_received",
                                      "receipt_id": str(preview.receipt.id)})
    if preview.eligibility_reasons:
        raise ValidationError(message="This invoice cannot be received yet.",
                              detail={"invoice_id": str(invoice_id), "reasons": preview.eligibility_reasons})
    if preview.blockers:
        raise ValidationError(
            message=f"{len(preview.blockers)} product line(s) have no approved units-per-case conversion; "
                    "nothing was received.",
            detail={"invoice_id": str(invoice_id), "reason": "unresolved_conversions",
                    "blockers": [vars(b) for b in preview.blockers]},
        )
    if not preview.lines:
        raise ValidationError(message="This invoice has no product lines to receive.",
                              detail={"invoice_id": str(invoice_id)})
    confirmed = accepted_quantities(preview.lines, accepted, reasons)
    invoice, store = preview.invoice, preview.store
    assert store is not None

    receipt = InventoryReceipt(
        invoice_id=invoice.id, invoice_number=invoice.invoice_number, store_id=store.id, status=RECEIPT_POSTED,
        received_on=received_on, confirmed_by_user_id=user.id, confirmed_by=user.username,
        confirmed_role=user.role, note=(note or "").strip() or None, line_count=len(confirmed),
        discrepancy_count=sum(1 for line, qty, _ in confirmed if qty != line.expected_units),
        resolution_snapshot={"enabled": preview.resolution.enabled, "paths": preview.resolution.paths()},
    )
    session.add(receipt)
    try:
        await session.flush()            # the partial unique index refuses a second POSTED receipt
    except IntegrityError as exc:
        if "uq_inventory_receipts_invoice_posted" not in str(exc.orig):
            raise
        raise ValidationError(message="This invoice has already been received.",
                              detail={"invoice_id": str(invoice_id), "reason": "already_received"}) from exc
    session.add_all([
        InventoryMovement(
            store_id=store.id, item_code=line.item_code, description=(line.description or "")[:255] or None,
            movement_type=MOVEMENT_RECEIPT, quantity=quantity, base_unit=BASE_UNIT_SELLABLE,
            expected_quantity=line.expected_units, source_quantity=line.cases, source_unit=SOURCE_UNIT_CASE,
            units_per_case=line.units_per_case, conversion_path=line.conversion_path,
            conversion_mapping_id=line.conversion_mapping_id, invoice_id=invoice.id,
            invoice_item_id=line.invoice_item_id, receipt_id=receipt.id, business_date=received_on,
            idempotency_key=f"receipt:{receipt.id}:{line.invoice_item_id}", actor_user_id=user.id,
            actor_username=user.username, reason=reason,
        )
        for line, quantity, reason in confirmed
    ])
    await session.flush()
    return receipt


@dataclass
class ReversalResult:
    receipt: InventoryReceipt
    movements: list[InventoryMovement]
    already_reversed: bool = False


async def reverse_receipt(
    session: AsyncSession, invoice_id: uuid.UUID, *, receipt_id: uuid.UUID, reason: str | None, user,
) -> ReversalResult:
    """
    Reverse the invoice's POSTED receipt: one compensating movement per
    receipt movement (negated, same business date, reversal_of_id set), the
    receipt marked REVERSED with who, when and why. Nothing is deleted or
    edited in place. Asking again for a receipt already reversed changes
    nothing and says so. Flushes; the caller commits.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(message="Say why the receipt is being reversed.",
                              detail={"field": "reason", "reason": "required"})
    await lock_invoice(session, invoice_id)
    receipt = (await session.execute(
        select(InventoryReceipt).where(InventoryReceipt.id == receipt_id, InventoryReceipt.invoice_id == invoice_id)
        .with_for_update()
    )).scalars().first()
    if receipt is None:
        raise RecordNotFoundError(message="Receipt not found for this invoice.",
                                  detail={"invoice_id": str(invoice_id), "receipt_id": str(receipt_id)})
    if receipt.status == RECEIPT_REVERSED:
        return ReversalResult(receipt, [], already_reversed=True)

    originals = list((await session.execute(
        select(InventoryMovement).where(InventoryMovement.receipt_id == receipt.id,
                                        InventoryMovement.reversal_of_id.is_(None))
        .order_by(InventoryMovement.item_code)
    )).scalars().all())
    compensating = [
        InventoryMovement(
            store_id=m.store_id, item_code=m.item_code, description=m.description, movement_type=m.movement_type,
            quantity=-m.quantity, base_unit=m.base_unit, expected_quantity=None, source_quantity=m.source_quantity,
            source_unit=m.source_unit, units_per_case=m.units_per_case, conversion_path=m.conversion_path,
            conversion_mapping_id=m.conversion_mapping_id, invoice_id=m.invoice_id,
            invoice_item_id=m.invoice_item_id, receipt_id=receipt.id, business_date=m.business_date,
            idempotency_key=f"reversal:{m.id}", actor_user_id=user.id, actor_username=user.username,
            reason=reason, reversal_of_id=m.id,
        )
        for m in originals
    ]
    session.add_all(compensating)
    receipt.status = RECEIPT_REVERSED
    receipt.reversed_at = datetime.now(UTC)
    receipt.reversed_by_user_id = user.id
    receipt.reversed_by = user.username
    receipt.reversal_reason = reason
    await session.flush()       # the invoice lock serializes reversals; any integrity error is a real fault
    return ReversalResult(receipt, compensating)


@dataclass(frozen=True)
class OpeningCountEntry:
    item_code: str
    quantity: Decimal
    description: str | None = None


async def record_opening_counts(
    session: AsyncSession, store_id: uuid.UUID, *, count_date: date, entries: Sequence[OpeningCountEntry],
    reason: str | None, user,
) -> list[InventoryMovement]:
    """One audited OPENING_COUNT per product per store. All or nothing. Flushes; the caller commits."""
    store = await physical_store(session, store_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(message="Say what the count rests on — e.g. who counted, and how.",
                              detail={"field": "reason", "reason": "required"})
    if not entries:
        raise ValidationError(message="Enter at least one counted product.", detail={"field": "entries"})
    problems: dict[str, str] = {}
    normalized: list[tuple[str, Decimal, str | None]] = []
    for entry in entries:
        code = normalize_item_code(entry.item_code)
        quantity = Decimal(str(entry.quantity))
        if code is None:
            problems[entry.item_code] = "no usable item code"
        elif quantity < 0 or quantity != quantity.to_integral_value():
            problems[entry.item_code] = "count must be a whole number of sellable units, at least 0"
        elif any(code == c for c, _, _ in normalized):
            problems[entry.item_code] = "counted twice in this entry"
        else:
            normalized.append((code, quantity, entry.description))
    existing = set((await session.execute(
        select(InventoryMovement.item_code).where(
            InventoryMovement.store_id == store.id, InventoryMovement.movement_type == MOVEMENT_OPENING_COUNT,
            InventoryMovement.item_code.in_([c for c, _, _ in normalized]),
        )
    )).scalars().all()) if normalized else set()
    for code in existing:
        problems[code] = "already has an opening count; a recount is a later adjustment"
    if problems:
        raise ValidationError(message=f"{len(problems)} count(s) cannot be recorded; nothing was recorded.",
                              detail={"entries": problems})
    movements = [
        InventoryMovement(
            store_id=store.id, item_code=code, description=(description or "")[:255] or None,
            movement_type=MOVEMENT_OPENING_COUNT, quantity=quantity, base_unit=BASE_UNIT_SELLABLE,
            business_date=count_date, idempotency_key=f"opening:{store.id}:{code}", actor_user_id=user.id,
            actor_username=user.username, reason=reason,
        )
        for code, quantity, description in normalized
    ]
    session.add_all(movements)
    try:
        await session.flush()
    except IntegrityError as exc:
        if "uq_inventory_movements_idempotency" not in str(exc.orig):
            raise
        raise ValidationError(message="Another opening count was recorded meanwhile; nothing was recorded.",
                              detail={"reason": "concurrent_opening_count"}) from exc
    return movements


async def store_balances(session: AsyncSession, store_id: uuid.UUID) -> list[ItemBalance]:
    """Every product with a movement at this store, folded into balances."""
    rows = (await session.execute(
        select(InventoryMovement.item_code, InventoryMovement.movement_type, InventoryMovement.business_date,
               func.sum(InventoryMovement.quantity).label("quantity"),
               func.max(InventoryMovement.description).label("description"))
        .where(InventoryMovement.store_id == store_id)
        .group_by(InventoryMovement.item_code, InventoryMovement.movement_type, InventoryMovement.business_date)
    )).mappings().all()
    return fold_balances(rows)


async def item_movements(session: AsyncSession, store_id: uuid.UUID, item_code: str) -> list[InventoryMovement]:
    return list((await session.execute(
        select(InventoryMovement)
        .where(InventoryMovement.store_id == store_id, InventoryMovement.item_code == item_code)
        .order_by(InventoryMovement.business_date, InventoryMovement.created_at)
    )).scalars().all())


async def recent_receipts(session: AsyncSession, store_id: uuid.UUID, limit: int = 5) -> list[InventoryReceipt]:
    return list((await session.execute(
        select(InventoryReceipt).where(InventoryReceipt.store_id == store_id)
        .order_by(InventoryReceipt.received_on.desc(), InventoryReceipt.created_at.desc()).limit(limit)
    )).scalars().all())


async def last_recorded(session: AsyncSession, store_id: uuid.UUID) -> datetime | None:
    return (await session.execute(
        select(func.max(InventoryMovement.created_at)).where(InventoryMovement.store_id == store_id)
    )).scalar()


async def store_invoices(session: AsyncSession, store_id: uuid.UUID, *, page: int, page_size: int):
    """The store's invoices, newest first, with each one's receipt (if any)."""
    total = (await session.execute(
        select(func.count()).select_from(Invoice).where(Invoice.store_id == store_id)
    )).scalar() or 0
    invoices = (await session.execute(
        select(Invoice).options(selectinload(Invoice.items), selectinload(Invoice.vendor),
                                selectinload(Invoice.document))
        .where(Invoice.store_id == store_id)
        .order_by(Invoice.invoice_date.desc().nulls_last(), Invoice.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    receipts: dict[uuid.UUID | None, InventoryReceipt] = {}
    if invoices:
        for r in (await session.execute(
            select(InventoryReceipt).where(InventoryReceipt.invoice_id.in_([i.id for i in invoices]))
            .order_by(InventoryReceipt.created_at)
        )).scalars().all():
            if receipts.get(r.invoice_id) is None or receipts[r.invoice_id].status != RECEIPT_POSTED:
                receipts[r.invoice_id] = r      # the POSTED one, else the latest reversed
    return list(invoices), receipts, total
