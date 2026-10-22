"""Store inventory API schemas — app/schemas/inventory.py"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.processing import StoreRef


class ReceiptSummary(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    status: str
    received_on: date
    confirmed_by: str
    confirmed_role: str
    line_count: int
    discrepancy_count: int
    created_at: datetime
    reversed_at: datetime | None = None
    reversed_by: str | None = None
    reversal_reason: str | None = None


class InventoryOverview(BaseModel):
    store: StoreRef
    tracked_products: int = Field(description="Products with at least one movement at this store.")
    opening_required: int = Field(description="Tracked products with no opening count yet — no on-hand figure.")
    out_of_stock: int
    in_stock: int
    last_recorded_at: datetime | None = Field(default=None, description="When the latest movement was recorded.")
    recent_receipts: list[ReceiptSummary] = Field(default_factory=list)


class InventoryItemRow(BaseModel):
    item_code: str = Field(description="11-digit PDI item code.")
    description: str | None = None
    status: str = Field(description="OPENING_STOCK_REQUIRED, OUT_OF_STOCK or IN_STOCK.")
    base_unit: str = "SELLABLE_UNIT"
    opening_date: date | None = None
    opening_quantity: Decimal | None = None
    received: Decimal = Field(description="Receipts after the opening count (all receipts when there is none).")
    sold: Decimal
    other: Decimal
    on_hand: Decimal | None = Field(default=None, description="Only once an opening count exists.")
    last_movement: date | None = None


class MovementRow(BaseModel):
    id: uuid.UUID
    movement_type: str
    quantity: Decimal
    expected_quantity: Decimal | None = None
    source_quantity: Decimal | None = None
    source_unit: str | None = None
    units_per_case: int | None = None
    conversion_path: str | None = None
    conversion_mapping_id: str | None = None
    business_date: date
    recorded_at: datetime
    actor_username: str
    reason: str | None = None
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    receipt_id: uuid.UUID | None = None


class StoreInvoiceRow(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str | None = None
    invoice_date: date | None = None
    vendor: str | None = None
    status: str
    product_lines: int = Field(description="Lines a receipt would cover (no charges, voided or shorted rows).")
    receipt: ReceiptSummary | None = None
    receivable_reasons: list[str] = Field(default_factory=list,
                                          description="Why the invoice cannot be received yet; empty when it can.")


class ReceiptLineOut(BaseModel):
    invoice_item_id: uuid.UUID
    sort_order: int
    description: str | None = None
    item_code: str
    cases: Decimal
    units_per_case: int
    expected_units: Decimal
    conversion_path: str
    conversion_scope: str | None = None


class ReceiptBlockerOut(BaseModel):
    sort_order: int
    description: str | None = None
    item_code: str | None = None
    reason: str


class ReceiptPreviewOut(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str | None = None
    store: StoreRef | None = None
    eligibility_reasons: list[str]
    lines: list[ReceiptLineOut]
    blockers: list[ReceiptBlockerOut]
    receipt: ReceiptSummary | None = Field(default=None, description="The POSTED receipt, if any.")
    history: list[ReceiptSummary] = Field(default_factory=list, description="Every receipt, oldest first.")
    can_post: bool


class ReceiptLineAcceptance(BaseModel):
    invoice_item_id: uuid.UUID
    accepted_units: Decimal = Field(description="Sellable units actually accepted.")
    reason: str | None = Field(default=None, max_length=500, description="Required when it differs from expected.")


class ReceiptPostRequest(BaseModel):
    received_on: date
    note: str | None = Field(default=None, max_length=1000)
    lines: list[ReceiptLineAcceptance] = Field(min_length=1)


class OpeningCountEntryIn(BaseModel):
    item_code: str = Field(min_length=1, max_length=32)
    quantity: Decimal = Field(description="Counted sellable units.")
    description: str | None = Field(default=None, max_length=255)


class OpeningCountRequest(BaseModel):
    count_date: date
    reason: str = Field(min_length=1, max_length=1000, description="Who counted, and how.")
    entries: list[OpeningCountEntryIn] = Field(min_length=1, max_length=2000)


class ReceiptReversalRequest(BaseModel):
    receipt_id: uuid.UUID = Field(description="The POSTED receipt being reversed — guards against reversing a newer one.")
    reason: str = Field(min_length=1, max_length=1000, description="Why the receipt is being reversed.")


class ReceiptReversalOut(BaseModel):
    receipt: ReceiptSummary
    movements_reversed: int
    already_reversed: bool
