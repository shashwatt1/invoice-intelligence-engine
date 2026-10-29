"""
Store inventory — app/models/inventory.py

An append-only movement ledger per PHYSICAL store and product. A balance is
never stored: it is the sum of the movements, so it cannot drift from them.

Product key: the 11-digit PDI item code (normalize_item_code) — the code the
invoice carries and the POS sells under. Base unit: the SELLABLE unit that
code denotes. A receipt converts invoiced cases with the approved units per
case (commercial resolution) and records the conversion it used.

A correction is never an edit: a reversing movement points at the original.
A receipt is never posted directly. Marking an invoice Retailed (with
inventory on) creates it SUBMITTED, with no movements; an approver with a
grant for the store approves it (POSTED: movements written once), adjusts
accepted quantities with reasons, or rejects it. A receipt is reversed, not
deleted: its movements are compensated, it keeps its history, and the invoice
may then be corrected and submitted again. At most one receipt per invoice is
SUBMITTED or POSTED at a time.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

BASE_UNIT_SELLABLE = "SELLABLE_UNIT"

MOVEMENT_OPENING_COUNT = "OPENING_COUNT"
MOVEMENT_RECEIPT = "RECEIPT"
MOVEMENT_SALE = "SALE"
MOVEMENT_SALE_RETURN = "SALE_RETURN"
MOVEMENT_RETURN_TO_VENDOR = "RETURN_TO_VENDOR"
MOVEMENT_TRANSFER_IN = "TRANSFER_IN"
MOVEMENT_TRANSFER_OUT = "TRANSFER_OUT"
MOVEMENT_ADJUSTMENT = "ADJUSTMENT"
INBOUND = frozenset({MOVEMENT_OPENING_COUNT, MOVEMENT_RECEIPT, MOVEMENT_SALE_RETURN, MOVEMENT_TRANSFER_IN})
OUTBOUND = frozenset({MOVEMENT_SALE, MOVEMENT_RETURN_TO_VENDOR, MOVEMENT_TRANSFER_OUT})
MOVEMENT_TYPES = INBOUND | OUTBOUND | {MOVEMENT_ADJUSTMENT}

RECEIPT_SUBMITTED = "SUBMITTED"      # proposed by marking the invoice Retailed; no stock effect yet
RECEIPT_POSTED = "POSTED"            # approved: its movements are in the ledger
RECEIPT_REJECTED = "REJECTED"        # refused by an approver, with a reason; terminal
RECEIPT_WITHDRAWN = "WITHDRAWN"      # the Retailed mark was removed, or the invoice deleted; terminal
RECEIPT_SUPERSEDED = "SUPERSEDED"    # replaced by a newer submission (Retailed marked again); terminal
RECEIPT_REVERSED = "REVERSED"        # a POSTED receipt compensated movement by movement; terminal
RECEIPT_STATUSES = frozenset({RECEIPT_SUBMITTED, RECEIPT_POSTED, RECEIPT_REJECTED, RECEIPT_WITHDRAWN,
                              RECEIPT_SUPERSEDED, RECEIPT_REVERSED})
RECEIPT_ACTIVE = frozenset({RECEIPT_SUBMITTED, RECEIPT_POSTED})        # at most one per invoice
SUBMISSION_ONLY = frozenset({RECEIPT_SUBMITTED, RECEIPT_REJECTED, RECEIPT_WITHDRAWN, RECEIPT_SUPERSEDED})
POSTING_STATES = frozenset({RECEIPT_POSTED, RECEIPT_REVERSED})


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


# Per-state rules, enforced by the database (migration 0035 creates exactly these).
_SUBMITTER = ("submitted_at IS NOT NULL AND submitted_by IS NOT NULL AND submitted_role IS NOT NULL "
              "AND attestation_sha256 IS NOT NULL AND submission_snapshot IS NOT NULL")
RECEIPT_CHECKS = {
    "ck_inventory_receipts_status": f"status IN ({_in(RECEIPT_STATUSES)})",
    "ck_inventory_receipts_reversal": (
        f"status <> '{RECEIPT_REVERSED}' OR "
        "(reversed_at IS NOT NULL AND reversed_by IS NOT NULL AND reversal_reason IS NOT NULL)"),
    "ck_inventory_receipts_posting": (
        f"(status NOT IN ({_in(POSTING_STATES)}) OR (received_on IS NOT NULL AND confirmed_by IS NOT NULL "
        "AND confirmed_role IS NOT NULL AND discrepancy_count IS NOT NULL "
        "AND (submitted_at IS NULL OR approved_at IS NOT NULL))) "
        f"AND (status <> '{RECEIPT_POSTED}' OR reversed_at IS NULL)"),
    "ck_inventory_receipts_submission": (
        f"(submitted_at IS NULL OR ({_SUBMITTER})) AND "
        f"(status NOT IN ({_in(SUBMISSION_ONLY)}) OR (submitted_at IS NOT NULL AND received_on IS NULL "
        "AND confirmed_by IS NULL AND confirmed_role IS NULL AND discrepancy_count IS NULL "
        "AND approved_at IS NULL AND reversed_at IS NULL))"),
    "ck_inventory_receipts_rejection": (
        f"(status = '{RECEIPT_REJECTED}' AND rejected_at IS NOT NULL AND rejected_by IS NOT NULL "
        "AND rejected_role IS NOT NULL AND rejection_reason IS NOT NULL AND length(trim(rejection_reason)) > 0) OR "
        f"(status <> '{RECEIPT_REJECTED}' AND rejected_at IS NULL AND rejected_by IS NULL AND rejected_role IS NULL "
        "AND rejection_reason IS NULL AND rejected_by_user_id IS NULL)"),
    "ck_inventory_receipts_withdrawal": (
        f"(status = '{RECEIPT_WITHDRAWN}' AND withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL "
        "AND withdrawn_role IS NOT NULL AND withdrawal_reason IS NOT NULL AND length(trim(withdrawal_reason)) > 0) OR "
        f"(status <> '{RECEIPT_WITHDRAWN}' AND withdrawn_at IS NULL AND withdrawn_by IS NULL AND withdrawn_role IS NULL "
        "AND withdrawal_reason IS NULL AND withdrawn_by_user_id IS NULL)"),
    "ck_inventory_receipts_supersession": (
        f"(status = '{RECEIPT_SUPERSEDED}' AND superseded_at IS NOT NULL) OR "
        f"(status <> '{RECEIPT_SUPERSEDED}' AND superseded_at IS NULL)"),
}
ACTIVE_INDEX_WHERE = f"status IN ({_in(RECEIPT_ACTIVE)})"
PENDING_INDEX_WHERE = f"status = '{RECEIPT_SUBMITTED}'"


class InventoryReceipt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One invoice's goods receipt, from proposal to ledger. Marking the invoice
    Retailed (with inventory on) creates it SUBMITTED, with a snapshot of the
    proposed lines and no movements. A MANAGER/ADMIN with a grant for the
    store approves it (POSTED, movements written once) or rejects it. A row is
    never deleted and changes only along the allowed transitions (guarded
    below and by the per-state CHECKs). At most one SUBMITTED or POSTED per invoice.
    """

    __tablename__ = "inventory_receipts"

    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True,
        doc="Kept as history if the invoice is later deleted.",
    )
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RECEIPT_POSTED)
    # The submission: who proposed it (by marking Retailed), what they attested, and what was proposed.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    submitted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    attestation_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, doc="The Retailed attestation fingerprint the submission was made under.",
    )
    edi_export_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("edi_exports.id", ondelete="SET NULL"), nullable=True,
        doc="The ledger-recorded PDI export the Retailed mark rested on (number and SHA-256 also in the snapshot).",
    )
    submission_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, doc="Proposed lines, blockers, eligibility and conversions at submission.",
    )
    previous_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inventory_receipts.id", ondelete="RESTRICT"), nullable=True,
        doc="The receipt this submission follows (rejected, withdrawn, superseded or reversed).",
    )
    # The posting: the approver (confirmed_*), the business date and what was accepted.
    received_on: Mapped[date | None] = mapped_column(Date, nullable=True, doc="The business date the goods arrived.")
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, doc="The approving account's username.")
    confirmed_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    discrepancy_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Terminal outcomes, each set once.
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    rejected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejected_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawn_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    withdrawn_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    withdrawn_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    withdrawal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reversed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        *(CheckConstraint(sql, name=name) for name, sql in RECEIPT_CHECKS.items()),
        # One SUBMITTED or POSTED receipt per invoice; every other state stays as history.
        Index("uq_inventory_receipts_invoice_active", "invoice_id", unique=True,
              postgresql_where=text(ACTIVE_INDEX_WHERE)),
        Index("idx_inventory_receipts_invoice", "invoice_id"),
        Index("idx_inventory_receipts_store", "store_id", "received_on"),
        Index("idx_inventory_receipts_pending", "store_id", "submitted_at",
              postgresql_where=text(PENDING_INDEX_WHERE)),
    )


# The only changes a receipt row may ever take: one status transition, with its own fields.
RECEIPT_TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    (RECEIPT_SUBMITTED, RECEIPT_POSTED): frozenset({
        "received_on", "confirmed_by_user_id", "confirmed_by", "confirmed_role", "approved_at", "note",
        "line_count", "discrepancy_count", "resolution_snapshot"}),
    (RECEIPT_SUBMITTED, RECEIPT_REJECTED): frozenset({
        "rejected_at", "rejected_by_user_id", "rejected_by", "rejected_role", "rejection_reason"}),
    (RECEIPT_SUBMITTED, RECEIPT_WITHDRAWN): frozenset({
        "withdrawn_at", "withdrawn_by_user_id", "withdrawn_by", "withdrawn_role", "withdrawal_reason"}),
    (RECEIPT_SUBMITTED, RECEIPT_SUPERSEDED): frozenset({"superseded_at"}),
    (RECEIPT_POSTED, RECEIPT_REVERSED): frozenset({
        "reversed_at", "reversed_by_user_id", "reversed_by", "reversal_reason"}),
}


class ImmutableReceiptError(RuntimeError):
    """A write that would edit receipt history outside an allowed transition, or delete a receipt."""


@event.listens_for(InventoryReceipt, "before_update")
def _receipts_change_only_by_transition(_mapper, connection, target: InventoryReceipt) -> None:
    """
    Refuse any update that is not exactly one allowed status transition with
    its own fields. The stored status is read from the database row in the
    flush's own transaction, never from the ORM's loaded copy.
    """
    table = InventoryReceipt.__table__
    stored = connection.execute(select(table.c.status).where(table.c.id == target.id)).scalar()
    state = inspect(target)
    changed = {attr.key for attr in state.attrs if attr.history.has_changes()} - {"updated_at"}
    allowed = RECEIPT_TRANSITIONS.get((stored, target.status))
    if allowed is None:
        raise ImmutableReceiptError(f"A {stored} receipt cannot become {target.status}.")
    if changed - allowed - {"status"}:
        raise ImmutableReceiptError(
            f"{stored} -> {target.status} may not change {sorted(changed - allowed - {'status'})}.")


@event.listens_for(InventoryReceipt, "before_delete")
def _receipts_are_never_deleted(_mapper, _connection, _target: InventoryReceipt) -> None:
    raise ImmutableReceiptError("Inventory receipts are history and are never deleted.")


class InventoryMovement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One signed quantity of one product, in sellable units, at one physical store."""

    __tablename__ = "inventory_movements"

    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    item_code: Mapped[str] = mapped_column(String(32), nullable=False, doc="11-digit PDI item code.")
    description: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="As the source named it.")
    movement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False, doc="Signed, in the base unit.")
    base_unit: Mapped[str] = mapped_column(String(16), nullable=False, default=BASE_UNIT_SELLABLE)
    expected_quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4), nullable=True, doc="A receipt's expected quantity (invoice x units per case).",
    )
    # How invoiced cases became sellable units — a snapshot, so later mapping
    # changes never rewrite what this movement meant.
    source_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    source_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    units_per_case: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversion_path: Mapped[str | None] = mapped_column(String(40), nullable=True)
    conversion_mapping_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"),
                                                         nullable=True)
    invoice_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoice_items.id", ondelete="SET NULL"), nullable=True,
    )
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inventory_receipts.id", ondelete="RESTRICT"), nullable=True,
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False, doc="The same source event can never post twice.",
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"),
                                                            nullable=True)
    actor_username: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inventory_movements.id", ondelete="RESTRICT"), nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_inventory_movements_idempotency"),
        CheckConstraint(f"movement_type IN ({_in(MOVEMENT_TYPES)})", name="ck_inventory_movements_type"),
        CheckConstraint(
            f"(movement_type NOT IN ({_in(INBOUND)}) OR quantity >= 0 OR reversal_of_id IS NOT NULL) AND "
            f"(movement_type NOT IN ({_in(OUTBOUND)}) OR quantity <= 0 OR reversal_of_id IS NOT NULL)",
            name="ck_inventory_movements_sign",
        ),
        Index("idx_inventory_movements_store_item", "store_id", "item_code", "business_date"),
        Index("idx_inventory_movements_invoice", "invoice_id"),
    )


# ---- store inventory baseline (0036) --------------------------------------------------------

BASELINE_EARLIEST_RECEIPT = "EARLIEST_POSTED_RECEIPT"   # the store already had posted receipts
BASELINE_INITIALIZATION_DATE = "INITIALIZATION_DATE"     # no posted receipt yet: the day it was initialized
BASELINE_BASES = frozenset({BASELINE_EARLIEST_RECEIPT, BASELINE_INITIALIZATION_DATE})
BASELINE_CHECKS = {
    "ck_store_inventory_baselines_basis": f"basis IN ({_in(BASELINE_BASES)})",
    # The earliest receipt is the evidence for its basis, and only for it.
    "ck_store_inventory_baselines_evidence": (
        f"(basis = '{BASELINE_EARLIEST_RECEIPT}') = (earliest_receipt_id IS NOT NULL)"),
    "ck_store_inventory_baselines_dates": "baseline_date <= initialization_date",
    "ck_store_inventory_baselines_reason": "length(trim(reason)) > 0",
}


class StoreInventoryBaseline(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A store's inventory start: every product at this store that has no
    product-level opening count starts at zero on `baseline_date`.

    It is not a count. A product-level OPENING_COUNT always takes precedence
    for its product. Against a baseline, every RECEIPT movement counts —
    including one approved later with an earlier arrival date, which a zero
    start cannot already contain — and other movements count from
    `baseline_date` (see app.services.inventory_service.fold_balances).

    At most one per store; never edited and never deleted. Recorded only by an
    administrator through scripts/initialize_inventory_baselines.py.
    """

    __tablename__ = "store_inventory_baselines"

    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    baseline_date: Mapped[date] = mapped_column(Date, nullable=False)
    basis: Mapped[str] = mapped_column(String(32), nullable=False)
    earliest_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("inventory_receipts.id", ondelete="RESTRICT"), nullable=True,
        doc="The posted receipt whose arrival date set baseline_date (EARLIEST_POSTED_RECEIPT only).")
    initialization_date: Mapped[date] = mapped_column(Date, nullable=False, doc="The day the run recorded it.")
    initialized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    initialized_by: Mapped[str] = mapped_column(String(64), nullable=False)
    initialized_by_role: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        *(CheckConstraint(sql, name=name) for name, sql in BASELINE_CHECKS.items()),
        UniqueConstraint("store_id", name="uq_store_inventory_baselines_store"),
    )


class ImmutableBaselineError(RuntimeError):
    """A write that would edit or delete a store inventory baseline."""


@event.listens_for(StoreInventoryBaseline, "before_update")
def _baselines_are_never_edited(_mapper, _connection, _target: StoreInventoryBaseline) -> None:
    raise ImmutableBaselineError("A store inventory baseline is never changed once recorded.")


@event.listens_for(StoreInventoryBaseline, "before_delete")
def _baselines_are_never_deleted(_mapper, _connection, _target: StoreInventoryBaseline) -> None:
    raise ImmutableBaselineError("Store inventory baselines are history and are never deleted.")
