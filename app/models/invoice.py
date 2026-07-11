"""
Invoice Model — app/models/invoice.py

Represents a successfully extracted and validated invoice.

Design decisions:
- An Invoice is created only after the AI structuring layer produces
  a valid InvoiceSchema. Documents that fail extraction never get an
  invoice record — they remain as Documents with FAILED_* status.
- Financial fields use NUMERIC(14,2) to match the DDL in design.md (line 601-604).
- `composite_confidence` is NUMERIC(5,4) for four decimal places (0.0000–1.0000).
- `document_id` FK links back to the uploaded file for traceability.
- `vendor_id` FK is nullable: it's set after vendor upsert, which may
  fail if no vendor info was extracted.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Invoice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Extracted and validated invoice header data.

    Fields match design.md §Database Design → invoices DDL (lines 592-621)
    and requirements.md §7.1 Entity Specification #1.
    """

    __tablename__ = "invoices"

    # Foreign keys
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        doc="Reference to the source document.",
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
        doc="Reference to the extracted vendor.",
    )

    # Invoice header fields
    invoice_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True, doc="Invoice number as printed on the document."
    )
    invoice_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, doc="Invoice issue date."
    )
    due_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, doc="Payment due date."
    )

    # Financial totals — NUMERIC(14,2) per design.md
    subtotal: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True, doc="Sum of line item totals before tax."
    )
    tax_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True, doc="Total tax amount."
    )
    discount_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True, server_default=text("0"), doc="Total discount applied."
    )
    grand_total: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True, doc="Final invoice total."
    )
    currency: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'USD'"), doc="ISO 4217 currency code."
    )

    # Vendor info as extracted (denormalized for display before vendor upsert)
    vendor_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, doc="Vendor name as extracted from the document."
    )
    vendor_tax_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, doc="Vendor tax ID as extracted from the document."
    )
    vendor_address: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Vendor address as extracted from the document."
    )

    # Processing metadata
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'EXTRACTED'"),
        doc="Invoice status: EXTRACTED, VALIDATED, REVIEW, COMPLETE.",
    )
    composite_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4), nullable=True, doc="Composite confidence score (0.0000–1.0000)."
    )
    extraction_model: Mapped[str | None] = mapped_column(
        String(100), nullable=True, doc="AI model used for extraction (e.g. gpt-4o-mini)."
    )

    # Raw structured JSON from OpenAI (for debugging / reprocessing)
    raw_extraction_json: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, doc="Raw JSON output from the AI structuring layer."
    )

    # Relationships
    document = relationship("Document", back_populates="invoice")
    vendor = relationship("Vendor", back_populates="invoices")
    items = relationship(
        "InvoiceItem", back_populates="invoice", cascade="all, delete-orphan",
        order_by="InvoiceItem.sort_order",
    )

    __table_args__ = (
        Index("idx_invoices_document_id", "document_id"),
        Index("idx_invoices_vendor_id", "vendor_id"),
        Index("idx_invoices_status", "status"),
        Index("idx_invoices_invoice_number", "invoice_number"),
    )

    def __repr__(self) -> str:
        return (
            f"<Invoice id={self.id} number={self.invoice_number!r} "
            f"total={self.grand_total} status={self.status!r}>"
        )
