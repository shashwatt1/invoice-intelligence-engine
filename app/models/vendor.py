"""
Vendor Model — app/models/vendor.py

Represents a supplier / vendor extracted from invoice data.

Design decisions:
- Vendors are upserted from extracted invoice data, not pre-created.
  The MVP has no vendor management UI, so vendors are created on-the-fly
  when a new vendor name or tax_id is encountered during processing.
- `tax_id` is the primary business key for matching (per requirements.md §7.1).
  If two invoices share the same tax_id, they map to the same vendor.
- The unique constraint on `(name, tax_id)` prevents duplicate vendors
  while allowing different vendors with the same name (different tax IDs).
"""

from __future__ import annotations

from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Vendor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Supplier profile, upserted from AI-extracted invoice data.

    Fields match requirements.md §7.1 Entity Specification #3 (Vendors).
    """

    __tablename__ = "vendors"

    name: Mapped[str] = mapped_column(
        String(255), nullable=False, doc="Vendor / supplier name."
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

    # Relationship to invoices
    invoices = relationship("Invoice", back_populates="vendor")

    __table_args__ = (
        # Allow same name with different tax_id, but not exact duplicates
        UniqueConstraint("name", "tax_id", name="uq_vendor_name_tax_id"),
        Index("idx_vendors_name", "name"),
        Index("idx_vendors_tax_id", "tax_id"),
    )

    def __repr__(self) -> str:
        return f"<Vendor id={self.id} name={self.name!r} tax_id={self.tax_id!r}>"
