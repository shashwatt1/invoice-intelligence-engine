"""
ProductCaseMapping Model — app/models/product_case_mapping.py

Persistent UPC → units-per-case mapping, the source of truth for the
units-per-case field in the PDI EDI (detail bytes [53:57]).

Design decisions:
- Keyed by the normalized item code, not by product name: the same
  product is described differently on different vendors' invoices, but
  the UPC is stable. Normalization matches what the formatter writes to
  the EDI (digits only, UPC-12 check digit dropped), so a mapping saved
  from one invoice is found again from any other invoice carrying the
  same barcode, however it happens to be printed.
- A unique constraint on `item_code` enforces one mapping per product;
  the repository upserts rather than inserting blindly, so a re-confirmed
  product updates in place instead of creating a duplicate.
- `source` records where the number came from, so a value a human
  confirmed is distinguishable from one taken off a document. It is
  deliberately a plain string rather than a history table — this is an
  MVP, and the current value plus its provenance is what EDI generation
  needs.
- No FK to invoices or vendors: the mapping outlives any single invoice
  and must stay usable after that invoice is deleted.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Where a units-per-case value came from.
SOURCE_MANUAL = "MANUAL"                                 # historical: a person typed it
SOURCE_VERIFIED_FROM_INVOICE = "VERIFIED_FROM_INVOICE"   # historical: pre-approval-workflow writes
SOURCE_APPROVED = "APPROVED"                             # promoted from an approved proposal
VALID_SOURCES = frozenset({SOURCE_MANUAL, SOURCE_VERIFIED_FROM_INVOICE, SOURCE_APPROVED})

# A case holds at least one unit; the EDI field is 4 digits.
MIN_UNITS_PER_CASE = 1
MAX_UNITS_PER_CASE = 9999


class ProductCaseMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One product's confirmed units-per-case, reusable across invoices."""

    __tablename__ = "product_case_mappings"

    item_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc=(
            "Normalized UPC / vendor item code — digits only, UPC-12 check "
            "digit dropped, matching what the PDI formatter emits."
        ),
    )
    units_per_case: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Units contained in one case (1-9999)."
    )
    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Product description as last seen, for recognizing the row in the UI.",
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc=(
            "APPROVED for rows promoted through the proposal workflow. MANUAL and "
            "VERIFIED_FROM_INVOICE are historical values from before that workflow "
            "existed and are kept as-is rather than rewritten."
        ),
    )
    approved_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_data_proposals.id", ondelete="SET NULL"),
        nullable=True,
        doc=(
            "The proposal whose approval established this value. Every "
            "authoritative row carries one; legacy rows point at a grandfathered "
            "proposal that records they predate the workflow."
        ),
    )

    __table_args__ = (
        UniqueConstraint("item_code", name="uq_product_case_mapping_item_code"),
        Index("idx_product_case_mappings_item_code", "item_code"),
    )

    def __repr__(self) -> str:
        return (
            f"<ProductCaseMapping item_code={self.item_code!r} "
            f"units_per_case={self.units_per_case} source={self.source!r}>"
        )
