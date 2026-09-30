"""
Vendor Master schemas — app/schemas/vendor.py

A vendor's canonical identity (its id, and the name a person confirmed) is
shown beside what invoices actually printed. Who decided is never part of a
request: it is the authenticated session.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.processing import StoreRef


class VendorRow(BaseModel):
    """One vendor in the Vendor Master list."""

    id: uuid.UUID
    label: str = Field(description="The confirmed canonical name, else the name first observed on an invoice.")
    name: str = Field(description="The name first observed on an invoice (what extraction matches on).")
    display_name: str | None = Field(default=None, description="The canonical name a person confirmed.")
    identity_status: str = Field(description="'unresolved' or 'confirmed'.")
    tax_id: str | None = None
    invoices: int = 0
    observed_names: int = Field(default=0, description="Distinct vendor wordings on this vendor's invoices.")
    last_seen_at: datetime | None = None


class ObservedName(BaseModel):
    name: str
    invoices: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None


class ObservedTaxId(BaseModel):
    tax_id: str
    invoices: int


class VendorInvoiceRef(BaseModel):
    invoice_id: uuid.UUID
    document_id: uuid.UUID
    invoice_number: str | None = None
    invoice_date: date | None = None
    observed_vendor_name: str | None = Field(default=None, description="The vendor wording this invoice printed.")
    grand_total: Decimal | None = None
    store: StoreRef | None = None


class VendorIdentityReviewEntry(BaseModel):
    decision: str
    previous_status: str
    new_status: str
    previous_display_name: str | None = None
    new_display_name: str | None = None
    reviewer: str
    reviewer_role: str | None = None
    basis: str
    decided_at: datetime


class VendorDiscrepancy(BaseModel):
    """A reprocessed reading that named another vendor (or none) — the confirmed vendor was kept."""

    invoice_id: uuid.UUID
    invoice_number: str | None = None
    observed_vendor_name: str | None = None
    observed_vendor_tax_id: str | None = None
    recorded_at: datetime


class VendorDetail(VendorRow):
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    observed_name_list: list[ObservedName] = Field(default_factory=list)
    observed_tax_ids: list[ObservedTaxId] = Field(default_factory=list)
    recent_invoices: list[VendorInvoiceRef] = Field(default_factory=list)
    history: list[VendorIdentityReviewEntry] = Field(default_factory=list)
    discrepancies: list[VendorDiscrepancy] = Field(default_factory=list)


class VendorConfirmRequest(BaseModel):
    """Confirm a vendor's identity. The service requires both fields."""

    display_name: str | None = Field(default=None, max_length=255, description="The canonical vendor name.")
    basis: str | None = Field(default=None, max_length=1000, description="What the confirmation rests on.")


class VendorReopenRequest(BaseModel):
    basis: str | None = Field(default=None, max_length=1000, description="Why the vendor is reopened.")


class VendorDecision(BaseModel):
    vendor_id: uuid.UUID
    previous_status: str
    new_status: str
    display_name: str | None = None
