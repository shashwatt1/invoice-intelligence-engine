"""
Store inventory baselines — app/services/inventory_baseline.py

Records a store's zero start (store_inventory_baselines, 0036). Written by an
administrator through POST /inventory/stores/{id}/zero-start or
scripts/initialize_inventory_baselines.py. `zero_start_preview` says, before
anything is written, exactly what a zero start would do to this store.

For one physical store:
  * already has a baseline  → SKIPPED (nothing is ever overwritten);
  * a source identity       → SKIPPED (not a physical store);
  * has a POSTED receipt    → baseline_date = the arrival date of its earliest
                              POSTED receipt (basis EARLIEST_POSTED_RECEIPT,
                              that receipt kept as evidence);
  * no POSTED receipt       → baseline_date = the initialization date (basis
                              INITIALIZATION_DATE);
  * a baseline date after the initialization date (a receipt dated in the
    future) → FAILED, nothing written.
Product-level opening counts and every movement are left exactly as they are;
see app.services.inventory_service.fold_balances for how balances use it.

Each store is its own transaction, so one failure never blocks another, and a
repeated run skips every store already initialized. A dry run goes through
the same steps (constraints included) and rolls back.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import PermissionDeniedError, ValidationError
from app.models.inventory import (
    BASELINE_EARLIEST_RECEIPT,
    BASELINE_INITIALIZATION_DATE,
    MOVEMENT_OPENING_COUNT,
    RECEIPT_POSTED,
    InventoryReceipt,
    StoreInventoryBaseline,
)
from app.models.store import KIND_PHYSICAL, Store
from app.models.user import User, UserRole
from app.services.inventory_service import (
    baseline_schema_ready,
    fold_balances,
    movement_rows,
    on_hand_units,
    store_catalogue,
)

INITIALIZED = "INITIALIZED"
SKIPPED = "SKIPPED"
FAILED = "FAILED"
REASON_MAX_LENGTH = 500


@dataclass(frozen=True)
class StoreResult:
    store_id: uuid.UUID
    store_label: str
    outcome: str                       # INITIALIZED (or would be, in a dry run), SKIPPED, FAILED
    detail: str
    baseline_date: date | None = None
    basis: str | None = None
    earliest_receipt_id: uuid.UUID | None = None


def require_admin_actor(actor: User | None) -> User:
    if actor is None or not actor.is_active or actor.role != UserRole.ADMIN.value:
        raise PermissionDeniedError(message="Only an active administrator can initialize inventory baselines.",
                                    detail={"required_role": UserRole.ADMIN.value})
    return actor


def require_reason(reason: str | None) -> str:
    why = (reason or "").strip()
    if not why:
        raise ValidationError(message="Give a reason; it is kept with every baseline.",
                              detail={"field": "reason", "reason": "required"})
    if len(why) > REASON_MAX_LENGTH:
        raise ValidationError(message=f"The reason is limited to {REASON_MAX_LENGTH} characters.",
                              detail={"field": "reason", "reason": "too_long"})
    return why


async def earliest_posted_receipt(session: AsyncSession, store_id: uuid.UUID) -> InventoryReceipt | None:
    """The store's POSTED receipt with the earliest arrival date (ties: the first recorded)."""
    return (await session.execute(
        select(InventoryReceipt)
        .where(InventoryReceipt.store_id == store_id, InventoryReceipt.status == RECEIPT_POSTED,
               InventoryReceipt.received_on.is_not(None))
        .order_by(InventoryReceipt.received_on, InventoryReceipt.created_at, InventoryReceipt.id)
        .limit(1)
    )).scalars().first()


@dataclass(frozen=True)
class ZeroStartPreview:
    """What a zero start would do to one store, computed from the ledger without writing anything."""
    already_started: bool
    has_history: bool                       # any movement or POSTED receipt: stock may exist before the first receipt
    products: int                           # distinct products: with a movement, or listed by the store's catalogue
    catalogue_only_products: int            # of those, listed by the catalogue and not yet moved
    counted_products: int                   # of those, with a person-entered opening count (kept as they are)
    products_to_initialize: int             # products with no count of their own: they start at zero
    posted_receipts: int
    first_receipt_date: date | None
    effective_date: date                    # the date products start at zero
    basis: str                              # EARLIEST_POSTED_RECEIPT or INITIALIZATION_DATE
    opening_required_after: int
    available_units_after: object           # Decimal; the store's on-hand total with the start applied


async def zero_start_preview(session: AsyncSession, store_id: uuid.UUID, *, today: date,
                             initialization_date: date | None = None) -> ZeroStartPreview:
    """
    The effect of starting `store_id` at zero, for the confirmation dialog. For a store with a POSTED receipt
    the start date is that receipt's arrival date (so every receipt counts exactly once and none is skipped);
    otherwise it is `initialization_date` (default today). A store that already has a baseline reports it.
    """
    rows = await movement_rows(session, store_id)
    catalogue = await store_catalogue(session, store_id)
    existing = (await session.execute(
        select(StoreInventoryBaseline).where(StoreInventoryBaseline.store_id == store_id))).scalars().first()
    receipt = await earliest_posted_receipt(session, store_id)
    posted = (await session.execute(
        select(func.count()).select_from(InventoryReceipt)
        .where(InventoryReceipt.store_id == store_id, InventoryReceipt.status == RECEIPT_POSTED))).scalar() or 0
    if existing is not None:
        effective, basis = existing.baseline_date, existing.basis
    elif receipt is not None:
        effective, basis = receipt.received_on, BASELINE_EARLIEST_RECEIPT
    else:
        effective, basis = initialization_date or today, BASELINE_INITIALIZATION_DATE
    counted = {r["item_code"] for r in rows if r["movement_type"] == MOVEMENT_OPENING_COUNT}
    moved = {r["item_code"] for r in rows}
    products = moved | set(catalogue)
    after = fold_balances(rows, effective, catalogue)     # the same population the overview folds
    return ZeroStartPreview(
        already_started=existing is not None, has_history=bool(rows) or posted > 0,
        products=len(products), catalogue_only_products=len(set(catalogue) - moved),
        counted_products=len(counted), products_to_initialize=len(products - counted),
        posted_receipts=int(posted), first_receipt_date=receipt.received_on if receipt else None,
        effective_date=effective, basis=basis,
        opening_required_after=sum(1 for b in after if b.on_hand is None),
        available_units_after=on_hand_units(after, True),
    )


async def initialize_store(
    session: AsyncSession, store_id: uuid.UUID, *, actor: User, reason: str, initialization_date: date,
) -> StoreResult:
    """
    Record one store's baseline, or say why not. Locks the store row first, so
    two runs for the same store cannot interleave; the unique store_id is the
    backstop. Flushes only; the caller commits (or rolls back a dry run).
    """
    store = (await session.execute(
        select(Store).where(Store.id == store_id).with_for_update())).scalars().first()
    if store is None:
        return StoreResult(store_id, str(store_id), FAILED, "store not found")
    label = store.label
    if store.kind != KIND_PHYSICAL:
        return StoreResult(store.id, label, SKIPPED, "source identity, not a physical store")
    existing = (await session.execute(
        select(StoreInventoryBaseline).where(StoreInventoryBaseline.store_id == store.id))).scalars().first()
    if existing is not None:
        return StoreResult(store.id, label, SKIPPED, f"already has a baseline ({existing.baseline_date})",
                           existing.baseline_date, existing.basis, existing.earliest_receipt_id)

    receipt = await earliest_posted_receipt(session, store.id)
    if receipt is not None:
        baseline_date, basis, evidence = receipt.received_on, BASELINE_EARLIEST_RECEIPT, receipt.id
    else:
        baseline_date, basis, evidence = initialization_date, BASELINE_INITIALIZATION_DATE, None
    if baseline_date > initialization_date:
        return StoreResult(store.id, label, FAILED,
                           f"earliest posted receipt is dated {baseline_date}, after the initialization date",
                           baseline_date, basis, evidence)

    baseline = StoreInventoryBaseline(
        store_id=store.id, baseline_date=baseline_date, basis=basis, earliest_receipt_id=evidence,
        initialization_date=initialization_date, initialized_by_user_id=actor.id, initialized_by=actor.username,
        initialized_by_role=actor.role, reason=reason,
    )
    try:
        async with session.begin_nested():
            session.add(baseline)
            await session.flush()
    except IntegrityError as exc:
        if "uq_store_inventory_baselines_store" not in str(exc.orig):
            raise
        return StoreResult(store.id, label, SKIPPED, "a baseline was recorded concurrently")
    detail = (f"from earliest posted receipt ({baseline_date})" if evidence
              else f"no posted receipts; starts on the initialization date ({baseline_date})")
    return StoreResult(store.id, label, INITIALIZED, detail, baseline_date, basis, evidence)


async def run_initialization(
    factory: async_sessionmaker[AsyncSession], *, actor_username: str, reason: str | None,
    initialization_date: date, store_ids: Sequence[uuid.UUID] | None = None, apply: bool = False,
) -> list[StoreResult]:
    """
    Initialize every physical store (or only `store_ids`), each in its own
    transaction. With apply=False (the default) every store is rolled back:
    the report says what would happen and nothing is written.
    """
    why = require_reason(reason)
    async with factory() as session:
        if not await baseline_schema_ready(session):
            raise ValidationError(message="Migration 0036 (store_inventory_baselines) is not applied.",
                                  detail={"reason": "schema_missing"})
        actor = require_admin_actor((await session.execute(
            select(User).where(User.username == actor_username))).scalars().first())
        actor_id = actor.id
        if store_ids:
            targets = list(dict.fromkeys(store_ids))
        else:
            stores = (await session.execute(select(Store))).scalars().all()
            targets = [s.id for s in sorted(stores, key=lambda s: (s.label.lower(), str(s.id)))]

    results: list[StoreResult] = []
    for store_id in targets:
        async with factory() as session:
            actor = await session.get(User, actor_id)
            try:
                result = await initialize_store(session, store_id, actor=require_admin_actor(actor), reason=why,
                                                initialization_date=initialization_date)
            except Exception as exc:  # noqa: BLE001 — one store's fault is reported, never blocks the rest
                await session.rollback()
                results.append(StoreResult(store_id, str(store_id), FAILED, f"{type(exc).__name__}: {exc}"[:300]))
                continue
            if apply and result.outcome == INITIALIZED:
                await session.commit()
            else:
                await session.rollback()
            results.append(result)
    return results
