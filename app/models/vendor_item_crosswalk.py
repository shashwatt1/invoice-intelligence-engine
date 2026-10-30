"""
Vendor item crosswalk — app/models/vendor_item_crosswalk.py

"Vendor V's item number N is the product whose PDI item code is C", as a
reviewer confirmed it once (migration 0038).

Frito-Lay, Red Bull and other route vendors print their own item numbers and
often no UPC. Extraction keeps that number apart from the barcode
(`invoice_items.supplier_item_id`); a reviewer enters the UPC from the paper
or the shelf. Without this table that work was lost with the invoice and
repeated on the next one. With it, a later invoice from the same vendor
carrying the same item number gets the confirmed UPC written to the line as
a recorded correction (app/services/vendor_item_crosswalk.py).

Design decisions:
- Scoped to one vendor record. Two vendors using the same number are not
  talking about the same product, so the key is (vendor_id, item number).
- Only CONFIRMED links live here, each traceable to the APPROVED proposal
  (product_data_proposals, entity vendor_item) that created it. Suggestions
  and their review history stay in the proposal table, like units-per-case.
- Never edited. A wrong link is RETIRED (who, when, why) and a new one is
  confirmed; at most one CONFIRMED link per key, enforced by a partial
  unique index.
- `item_code` is the 11-digit PDI key; `upc` is the full barcode when the
  reviewer typed one with its check digit. Both are text: leading zeros
  are part of the value.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

CROSSWALK_CONFIRMED = "CONFIRMED"
CROSSWALK_RETIRED = "RETIRED"
VALID_CROSSWALK_STATES = frozenset({CROSSWALK_CONFIRMED, CROSSWALK_RETIRED})


class VendorItemCrosswalk(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "vendor_item_crosswalk"

    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False)
    vendor_item_code: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="The vendor's item number, normalized (gtin.normalize_vendor_item_code).")
    vendor_item_code_raw: Mapped[str] = mapped_column(String(128), nullable=False, doc="As printed.")
    item_code: Mapped[str] = mapped_column(String(32), nullable=False, doc="The 11-digit PDI item code it names.")
    upc: Mapped[str | None] = mapped_column(String(14), nullable=True,
                                            doc="The full barcode as typed, when it carried its check digit.")
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("master_products.id", ondelete="SET NULL"), nullable=True,
        doc="The Product Master product with that item code, when one existed at confirmation.")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=CROSSWALK_CONFIRMED)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_data_proposals.id", ondelete="RESTRICT"), nullable=True,
        doc="The approved proposal that confirmed this link.")
    confirmed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    confirmed_by_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict,
                                                     doc="Invoice, line and description it was confirmed on.")
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        CheckConstraint("state IN ('CONFIRMED', 'RETIRED')", name="ck_vendor_item_crosswalk_state"),
        CheckConstraint(
            "(state = 'CONFIRMED' AND retired_at IS NULL AND retired_by IS NULL AND retire_reason IS NULL) OR "
            "(state = 'RETIRED' AND retired_at IS NOT NULL AND retired_by IS NOT NULL AND retire_reason IS NOT NULL)",
            name="ck_vendor_item_crosswalk_retired"),
        CheckConstraint("length(item_code) = 11", name="ck_vendor_item_crosswalk_item_code"),
        Index("uq_vendor_item_crosswalk_confirmed", "vendor_id", "vendor_item_code", unique=True,
              postgresql_where=text("state = 'CONFIRMED'")),
        Index("idx_vendor_item_crosswalk_item_code", "item_code"),
    )
