"""
InvoiceItem Model — app/models/invoice_item.py

Represents a single line item row on an invoice.

Design decisions:
- Fields match design.md §Database Design → invoice_items DDL (lines 624-638).
- `quantity` uses NUMERIC(12,4) and `unit_price` uses NUMERIC(14,4) for
  precision with fractional quantities (e.g. 1.5 kg) and small unit prices.
- `line_total` uses NUMERIC(14,2) since it's a monetary value.
- `sort_order` preserves the original document ordering so items
  display in the same order they appear on the invoice.
- `product_sku` is nullable — populated in Phase 3 by the product matching engine.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, UUIDPrimaryKeyMixin


class InvoiceItem(Base, UUIDPrimaryKeyMixin):
    """
    A single line item on an invoice.

    Fields match design.md invoice_items DDL and requirements.md §7.1 #2.
    """

    __tablename__ = "invoice_items"

    # Foreign key to parent invoice
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        doc="Parent invoice that this line item belongs to.",
    )

    # Line item fields
    description: Mapped[str] = mapped_column(
        Text, nullable=False, doc="Item description as printed on the invoice."
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, doc="Quantity (supports fractional values like 1.5 kg)."
    )
    unit_price: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4),
        nullable=True,
        doc=(
            "Net cost per unit. NULL means extraction could not determine it — "
            "semantically distinct from a legitimate 0.00, and never coerced to "
            "zero (see migration 0005). PDI export is blocked while it is NULL, "
            "so a missing price cannot become a 000000 case cost."
        ),
    )
    line_total: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2),
        nullable=True,
        doc=(
            "Total for this line item. NULL means extraction could not determine "
            "it; distinct from a legitimate 0.00."
        ),
    )
    tax_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True, doc="Tax rate as a percentage (e.g. 18.0000 for 18%)."
    )
    discount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True, server_default=text("0"), doc="Discount applied to this item."
    )
    pack_size: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="Units per case as printed (e.g. '24/12OZ'); drives the PDI units-per-case field.",
    )
    deposit: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2),
        nullable=True,
        doc=(
            "Per-unit container/bottle deposit as printed. Real money owed, "
            "separate from the cost of goods; needed to reconcile layouts "
            "whose extended total includes the deposit."
        ),
    )

    # Phase 3 — product matching
    product_sku: Mapped[str | None] = mapped_column(
        String(100), nullable=True, doc="Matched product SKU (populated in Phase 3)."
    )

    # Ordering
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        doc="Display order matching the original document layout.",
    )

    # Relationships
    invoice = relationship("Invoice", back_populates="items")

    __table_args__ = (
        Index("idx_invoice_items_invoice_id", "invoice_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<InvoiceItem id={self.id} desc={self.description[:30]!r} "
            f"qty={self.quantity} total={self.line_total}>"
        )
