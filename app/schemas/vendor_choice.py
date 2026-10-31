"""Schemas for choosing a vendor at upload: the picker, direct creation and requests for a vendor that is not listed."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class VendorOption(BaseModel):
    """One entry of the vendor picker (names only: no tax id, address or contact)."""

    id: uuid.UUID
    name: str = Field(description="The confirmed canonical name, else the name first observed.")
    confirmed: bool
    last_used_at: datetime | None = Field(default=None, description="The latest invoice at the given store, if any.")


class VendorAddRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)


class VendorRequestCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255, description="The vendor's name as it should be listed.")


class VendorRequestDecision(BaseModel):
    existing_vendor_id: uuid.UUID | None = Field(
        default=None, description="Approve by pointing the request at a vendor that already exists (it was a duplicate).")
    note: str | None = Field(default=None, max_length=1000)


class VendorRequestOut(BaseModel):
    id: uuid.UUID
    requested_name: str
    status: str
    requested_by: str
    created_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    vendor_id: uuid.UUID | None = None
    waiting_invoices: int = Field(default=0, description="Invoices uploaded against this request that still wait for it.")
