"""
StoreProductReference Model — app/models/store_product_reference.py

Per-store product facts imported from the store's own Item Sales Summary
export. A REFERENCE layer: it never writes an EDI byte and never
overrides an invoice. It exists to identify a product, to sanity-check
the cost an invoice claims, and to propose a units-per-case value for a
human to confirm.

Design decisions:
- Keyed by (store_number, item_code) where item_code comes from the same
  normalize_item_code() the formatter uses for EDI bytes [1:12] and
  product_case_mappings uses for its key. 99.8% of the store's scan
  codes are already in that 11-digit form, so the join is exact rather
  than fuzzy.
- store_number is in the key from the start. Costs are a property of one
  store's purchasing, and retrofitting the column onto live data later
  would be a migration against rows that were never store-scoped.
- `avg_cost` is the per-selling-unit cost. The export also carries a
  `Cost` column, which is a PERIOD AGGREGATE: Cost = # Sold x Avg Cost
  held on 6,138 of 6,138 rows across the three files. Storing that as a
  product cost would be wrong by orders of magnitude, so it is not
  stored at all and the importer refuses to read it.
- `avg_cost` is NULL when the store has no cost on file, never 0. Two
  thirds of the catalogue is uncosted, and a stored 0 would be
  indistinguishable from a genuinely free product — the same defect
  migration 0005 removed from invoice_items.
- NO units_per_case column. Units-per-case is confirmed by a human and
  lives in product_case_mappings, which stays the sole authority for
  what reaches the EDI. A reference-derived value is a suggestion
  computed at review time, never a stored fact.
- source_file/imported_at are the provenance: which export a row came
  from and when, so a stale figure can be traced and re-imported.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StoreProductReference(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One product as the store's own POS knows it."""

    __tablename__ = "store_product_references"

    store_number: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="Store this catalogue belongs to, e.g. '47708760'."
    )
    item_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc=(
            "Normalized scan code — the same key product_case_mappings uses "
            "and the formatter writes to EDI bytes [1:12]."
        ),
    )
    scan_code_raw: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="Scan code exactly as exported, kept for audit."
    )
    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc=(
            "Product name in the store's POS. Shown to a human as a hint; never "
            "used to match a product automatically — 666 descriptions in this "
            "store's own export map to more than one scan code."
        ),
    )
    avg_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4),
        nullable=True,
        doc=(
            "Per-selling-unit cost, period-weighted. NULL when the store has no "
            "cost on file — never 0, which would mean 'free'."
        ),
    )
    avg_price: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 4), nullable=True, doc="Per-unit retail actually achieved. NULL when absent."
    )
    source_file: Mapped[str] = mapped_column(
        String(255), nullable=False, doc="Export file this row was imported from."
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, doc="When this row was last imported."
    )

    __table_args__ = (
        UniqueConstraint(
            "store_number", "item_code", name="uq_store_product_reference_store_item"
        ),
        Index("idx_store_product_references_item_code", "item_code"),
        Index("idx_store_product_references_store", "store_number"),
    )

    def __repr__(self) -> str:
        return (
            f"<StoreProductReference store={self.store_number!r} "
            f"item_code={self.item_code!r} avg_cost={self.avg_cost}>"
        )
