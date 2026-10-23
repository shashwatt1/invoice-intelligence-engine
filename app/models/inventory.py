"""
Store inventory — app/models/inventory.py

An append-only movement ledger per PHYSICAL store and product. A balance is
never stored: it is the sum of the movements, so it cannot drift from them.

Product key: the 11-digit PDI item code (normalize_item_code) — the code the
invoice carries and the POS sells under. Base unit: the SELLABLE unit that
code denotes. A receipt converts invoiced cases with the approved units per
case (commercial resolution) and records the conversion it used.

A correction is never an edit: a reversing movement points at the original.
A receipt is reversed, not deleted: its movements are compensated, it keeps
its history, and the invoice may then be corrected and received again. At
most one receipt per invoice is POSTED at a time.
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

RECEIPT_POSTED = "POSTED"
RECEIPT_REVERSED = "REVERSED"


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


class InventoryReceipt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A person's confirmation that an invoice's goods were received — at most one POSTED per invoice."""

    __tablename__ = "inventory_receipts"

    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True,
        doc="Kept as history if the invoice is later deleted.",
    )
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RECEIPT_POSTED)
    received_on: Mapped[date] = mapped_column(Date, nullable=False, doc="The business date the goods arrived.")
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    confirmed_by: Mapped[str] = mapped_column(String(64), nullable=False, doc="The signed-in account's username.")
    confirmed_role: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    discrepancy_count: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Set once, when the receipt is reversed; the receipt row itself is kept.
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reversed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_in({RECEIPT_POSTED, RECEIPT_REVERSED})})",
                        name="ck_inventory_receipts_status"),
        CheckConstraint(
            f"status <> '{RECEIPT_REVERSED}' OR "
            "(reversed_at IS NOT NULL AND reversed_by IS NOT NULL AND reversal_reason IS NOT NULL)",
            name="ck_inventory_receipts_reversal",
        ),
        # One POSTED receipt per invoice; reversed ones stay as history.
        Index("uq_inventory_receipts_invoice_posted", "invoice_id", unique=True,
              postgresql_where=text(f"status = '{RECEIPT_POSTED}'")),
        Index("idx_inventory_receipts_invoice", "invoice_id"),
        Index("idx_inventory_receipts_store", "store_id", "received_on"),
    )


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
