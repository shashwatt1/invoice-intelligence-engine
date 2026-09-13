"""
ProductDataProposal Model — app/models/product_data_proposal.py

The gate between "someone thinks this is true" and "the EDI relies on it".

Every reusable product fact — today, units-per-case — enters the system
as a PENDING proposal carrying the evidence it came from, and reaches an
authoritative table only when a reviewer approves it. The row is then
frozen: a later change is a new proposal, never an edit, so the history
of what was claimed, by whom, on what evidence, and what was decided is
complete and cannot be rewritten.

Why this exists
---------------
During Testani testing, values typed into the frontend to unblock a
download were written straight into product_case_mappings and were
afterwards indistinguishable from genuinely verified ones — ten of
thirty-eight had to be found and deleted by hand. This table closes that
path permanently: the frontend can propose, and only a reviewer can
promote.

Design decisions
----------------
- Generic over (entity_type, entity_key, field) so the same table governs
  product_case_mappings today and product identity / pricing later,
  without one review table per fact.
- proposed_value / current_value are JSONB so a proposal is typed and
  lossless whatever the field, and current_value records what was
  authoritative at proposal time — the reviewer sees the delta, and the
  audit shows what a decision replaced.
- `source` says HOW the value was arrived at (the store's cost ratio, an
  explicit workbook cell, a typed number). It is the single most
  important thing a reviewer reads, and it is deliberately not
  VERIFIED_FROM_INVOICE — that label meant nothing.
- Immutable after review, enforced in the repository: any write to a
  non-PENDING row raises. Rejections stay in the table forever.
- The legacy path: mappings confirmed before this table existed are
  grandfathered with source=legacy_migrated and a reviewer of
  "system:legacy-migration". They are not claimed to have been reviewed
  by a person, because they were not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
VALID_STATUSES = frozenset({STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED})

ENTITY_CASE_MAPPING = "case_mapping"
FIELD_UNITS_PER_CASE = "units_per_case"

# How a proposed value was arrived at. A reviewer weighs these very
# differently, so the vocabulary is deliberately specific.
SOURCE_REFERENCE_DERIVED = "reference_derived"        # invoice cost / store avg_cost hit a pack
SOURCE_DOCUMENT_DERIVED = "document_derived"          # pack column or N/M in the description
SOURCE_DOCUMENT_AMBIGUOUS = "document_ambiguous"      # N/M/K form; operator chose between readings
SOURCE_BEER_INVENTORY_EXPLICIT = "beer_inventory_explicit"   # a typed items/case cell
SOURCE_BEER_INVENTORY_PACKAGE = "beer_inventory_package"     # decoded from a package string
SOURCE_OPERATOR_ENTERED = "operator_entered"          # typed with no supporting suggestion
SOURCE_LEGACY_MIGRATED = "legacy_migrated"            # predates this table; grandfathered
VALID_SOURCES = frozenset({
    SOURCE_REFERENCE_DERIVED, SOURCE_DOCUMENT_DERIVED, SOURCE_DOCUMENT_AMBIGUOUS,
    SOURCE_BEER_INVENTORY_EXPLICIT, SOURCE_BEER_INVENTORY_PACKAGE,
    SOURCE_OPERATOR_ENTERED, SOURCE_LEGACY_MIGRATED,
})

LEGACY_REVIEWER = "system:legacy-migration"


class ProductDataProposal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One proposed change to reusable product data, and its review."""

    __tablename__ = "product_data_proposals"

    store_number: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(32), nullable=False, doc="What kind of record this targets, e.g. case_mapping."
    )
    entity_key: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="Normalized item code the proposal is about."
    )
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    current_value: Mapped[Any | None] = mapped_column(
        JSONB, nullable=True, doc="Authoritative value at proposal time; NULL when new."
    )

    source: Mapped[str] = mapped_column(String(48), nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    evidence: Mapped[Any | None] = mapped_column(
        JSONB, nullable=True,
        doc="Whatever supported the value: ratio, avg_cost, package string, candidates.",
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_by: Mapped[str] = mapped_column(String(128), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_PENDING)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_product_data_proposals_status", "status"),
        Index("idx_product_data_proposals_entity", "store_number", "entity_type", "entity_key"),
        Index("idx_product_data_proposals_invoice", "invoice_id"),
    )

    @property
    def is_pending(self) -> bool:
        return self.status == STATUS_PENDING

    def __repr__(self) -> str:
        return (
            f"<ProductDataProposal {self.entity_type}:{self.entity_key}.{self.field} "
            f"= {self.proposed_value!r} [{self.status}] via {self.source}>"
        )
