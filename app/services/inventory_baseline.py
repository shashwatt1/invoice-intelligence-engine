"""
Store inventory baselines — app/services/inventory_baseline.py

Records a store's zero start (store_inventory_baselines, 0036). Used only by
scripts/initialize_inventory_baselines.py, run by an administrator; there is
no API route that writes a baseline.

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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import PermissionDeniedError, ValidationError
from app.models.inventory import (
    BASELINE_EARLIEST_RECEIPT,
    BASELINE_INITIALIZATION_DATE,
    RECEIPT_POSTED,
    InventoryReceipt,
    StoreInventoryBaseline,
)
from app.models.store import KIND_PHYSICAL, Store
from app.models.user import User, UserRole
from app.services.inventory_service import baseline_schema_ready

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
