"""
Vendor Model — app/models/vendor.py

Represents a supplier / vendor extracted from invoice data.

Design decisions:
- Vendors are upserted from extracted invoice data, not pre-created.
  Vendors are created on-the-fly when a new vendor name or tax_id is
  encountered during processing.
- `tax_id` is the primary business key for matching (per requirements.md §7.1).
  If two invoices share the same tax_id, they map to the same vendor.
- The unique constraint on `(name, tax_id)` prevents duplicate vendors
  while allowing different vendors with the same name (different tax IDs).

Vendor Master (governed identity):
- `id` is the canonical, application-owned vendor identity. It never
  changes; invoice wording, tax ids, addresses and emails are evidence about
  it, not what it is.
- `name` is the name first observed on an invoice — what extraction matches
  on and what exports print. It is never rewritten by a confirmation. Each
  invoice keeps its own raw wording (invoices.vendor_name) as provenance.
- `identity_status` is 'unresolved' until a MANAGER or ADMIN confirms the
  vendor's identity; `display_name` is the canonical name they confirmed.
  Nothing confirms a vendor automatically — not a repeated name, a shared
  address or email, or a model's opinion.
- Every confirmation or reopening is recorded in VendorIdentityReview.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

VENDOR_UNRESOLVED = "unresolved"   # observed on invoices; identity not established by a person
VENDOR_CONFIRMED = "confirmed"     # a MANAGER/ADMIN established the canonical identity
VENDOR_IDENTITY_STATUSES = frozenset({VENDOR_UNRESOLVED, VENDOR_CONFIRMED})

DECISION_CONFIRM = "CONFIRM"
DECISION_REOPEN = "REOPEN"


class Vendor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Supplier profile, upserted from AI-extracted invoice data.

    Fields match requirements.md §7.1 Entity Specification #3 (Vendors).
    """

    __tablename__ = "vendors"

    name: Mapped[str] = mapped_column(
        String(255), nullable=False, doc="Vendor / supplier name, as first observed on an invoice."
    )
    tax_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, doc="VAT / GST / Tax registration ID."
    )
    address: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Full vendor address."
    )
    phone: Mapped[str | None] = mapped_column(
        String(50), nullable=True, doc="Vendor phone number."
    )
    email: Mapped[str | None] = mapped_column(
        String(255), nullable=True, doc="Vendor email address."
    )
    display_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, doc="The canonical vendor name a person confirmed."
    )
    identity_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VENDOR_UNRESOLVED, server_default=VENDOR_UNRESOLVED,
        doc="'unresolved' until a MANAGER/ADMIN confirms the vendor's identity.",
    )

    # Relationship to invoices
    invoices = relationship("Invoice", back_populates="vendor")

    __table_args__ = (
        # Allow same name with different tax_id, but not exact duplicates
        UniqueConstraint("name", "tax_id", name="uq_vendor_name_tax_id"),
        CheckConstraint("identity_status IN ('unresolved', 'confirmed')", name="ck_vendors_identity_status"),
        Index("idx_vendors_name", "name"),
        Index("idx_vendors_tax_id", "tax_id"),
        Index("idx_vendors_identity_status", "identity_status"),
    )

    @property
    def label(self) -> str:
        """What a person sees: the confirmed canonical name, else the observed name."""
        if self.identity_status == VENDOR_CONFIRMED and self.display_name:
            return self.display_name
        return self.name

    def __repr__(self) -> str:
        return f"<Vendor id={self.id} name={self.name!r} tax_id={self.tax_id!r}>"


class VendorIdentityReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One governance decision about one vendor's identity, append-only.

    Never updated or deleted. The vendor row carries its current state; this
    carries how it got there — who (from the authenticated session), what
    changed, when, why, and the evidence as it stood.
    """

    __tablename__ = "vendor_identity_reviews"

    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, doc="CONFIRM or REOPEN.")
    previous_status: Mapped[str] = mapped_column(String(16), nullable=False)
    new_status: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewer: Mapped[str] = mapped_column(String(64), nullable=False, doc="The username that decided.")
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reviewer_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    basis: Mapped[str] = mapped_column(String(1000), nullable=False, doc="What the decision rests on.")
    evidence_considered: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint("decision IN ('CONFIRM', 'REOPEN')", name="ck_vendor_identity_reviews_decision"),
        Index("idx_vendor_identity_reviews_vendor", "vendor_id"),
        Index("idx_vendor_identity_reviews_created", "created_at"),
    )
