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
    status: str = Field(description="SUBMITTED, POSTED, REJECTED, WITHDRAWN, SUPERSEDED or REVERSED.")
    line_count: int
    created_at: datetime
    # The submission (made by marking the invoice Retailed); absent on receipts posted before approval existed.
    submitted_at: datetime | None = None
    submitted_by: str | None = None
    submitted_role: str | None = None
    previous_receipt_id: uuid.UUID | None = None
    # The posting: the approver and what was accepted.
    received_on: date | None = None
    confirmed_by: str | None = Field(default=None, description="The approving account.")
    confirmed_role: str | None = None
    approved_at: datetime | None = None
    discrepancy_count: int | None = None
    note: str | None = None
    self_approved: bool = Field(default=False, description="The submitter approved it themselves.")
    rejected_at: datetime | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    withdrawn_at: datetime | None = None
    withdrawn_by: str | None = None
    withdrawal_reason: str | None = None
    superseded_at: datetime | None = None
    reversed_at: datetime | None = None
    reversed_by: str | None = None
    reversal_reason: str | None = None


class InventoryBaselineOut(BaseModel):
    """The store's inventory start: products without their own opening count start at zero on baseline_date."""

    baseline_date: date
    basis: str = Field(description="EARLIEST_POSTED_RECEIPT or INITIALIZATION_DATE.")
    initialization_date: date
    initialized_by: str
    reason: str


class InventoryOverview(BaseModel):
    store: StoreRef
    tracked_products: int = Field(description="Products with at least one movement at this store.")
    opening_required: int = Field(description="Tracked products with no opening count yet — no on-hand figure.")
    out_of_stock: int
    in_stock: int
    last_recorded_at: datetime | None = Field(default=None, description="When the latest movement was recorded.")
    recent_receipts: list[ReceiptSummary] = Field(default_factory=list)
    pending_approvals: int = Field(default=0, description="Receipt submissions awaiting approval at this store.")
    baseline: InventoryBaselineOut | None = Field(
        default=None, description="The store's zero start, if an administrator initialized one.")
    opening_state: str = Field(
        default="NOT_SET",
        description=("NOT_SET: no zero start and no counts — the store's stock is unknown, NOT zero. "
                     "ZERO_START: an administrator declared a zero start; not a physical count. "
                     "COUNTED: people entered physical opening counts."))
    opening_counted_products: int = Field(default=0, description="Products with a person-entered opening count.")
    opening_last_counted_on: date | None = Field(default=None, description="Date of the latest opening count.")
    opening_last_counted_by: str | None = Field(default=None, description="Who recorded the latest opening count.")
    on_hand_units: Decimal | None = Field(
        default=None,
        description="Units on hand across products that have an on-hand figure; null when none does (not zero).")


class StoreInventoryEntry(BaseModel):
    """One physical store on the Inventory page. `overview` is null exactly when `access_granted` is false."""

    store: StoreRef
    access_granted: bool = Field(
        description=("The signed-in account may use this store: an administrator for any active physical store, "
                     "a manager through an active grant."))
    overview: InventoryOverview | None = None


class InventoryAllStores(BaseModel):
    stores: list[StoreInventoryEntry]
    total_physical: int = Field(description="Stores listed.")
    not_granted: int = Field(default=0, description="Listed stores the account may not open (none for an administrator).")


class ZeroStartRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500,
                        description="Why the store starts at zero; kept with the baseline for good.")
    initialization_date: date | None = Field(
        default=None, description="The date a store with no receipts starts at zero. Defaults to today; never in the future.")
    confirm: bool = Field(default=False, description="The administrator confirmed what the preview showed.")
    acknowledge_prior_stock: bool = Field(
        default=False,
        description=("Required when the store already has movements or posted receipts: the administrator "
                     "confirms it held no stock of these products before its first receipt."))


class ZeroStartPreviewOut(BaseModel):
    """What starting a store at zero would do — nothing is written to produce it."""

    already_started: bool
    has_history: bool = Field(description="Movements or posted receipts exist, so stock may have existed before them.")
    products: int = Field(description="Distinct products at the store: with a movement, or listed by its catalogue.")
    catalogue_only_products: int = Field(
        default=0, description="Of those, products its catalogue lists that have no movement yet.")
    counted_products: int = Field(description="Products with a person-entered opening count: kept as they are.")
    products_to_initialize: int = Field(description="Products that would start at an explicit zero.")
    posted_receipts: int
    first_receipt_date: date | None = None
    effective_date: date = Field(description="The date products start at zero.")
    basis: str
    opening_required_after: int
    available_units_after: Decimal


class ZeroStartResult(BaseModel):
    created: bool = Field(description="False when the store already had a zero start: nothing was written.")
    baseline: InventoryBaselineOut
    products_initialized: int = Field(default=0, description="Products that start at an explicit zero as a result.")


class ResolvedValueOut(BaseModel):
    """A reference value with where it came from. `value` is null unless status is RESOLVED."""

    value: int | str | None = None
    status: str = Field(description="RESOLVED, MISSING (no reviewed source says anything) or HELD (needs a person).")
    source: str | None = Field(default=None, description="The resolution path or description source.")
    scope: str | None = Field(default=None, description="Where it was approved: a store, all stores, the master.")
    approval: str | None = Field(default=None, description="APPROVED, STORE_CONFIRMED or CONFIRMED.")
    mapping_id: str | None = None
    notes: list[str] = Field(default_factory=list)


class ItemReferenceOut(BaseModel):
    units_per_case: ResolvedValueOut
    description: ResolvedValueOut


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
    on_hand: Decimal | None = Field(default=None,
                                    description="Only once an opening count or a store baseline exists.")
    last_movement: date | None = None
    opening_source: str | None = Field(
        default=None, description="OPENING_COUNT (the product's own count), STORE_BASELINE (the store's zero "
                                  "start), or null when there is no start yet.")
    description_from_reference: bool = Field(
        default=False, description="The description is the reviewed commercial one because the store's own was missing.")
    reference: ItemReferenceOut | None = Field(
        default=None, description="Reviewed commercial reference values; informational, never part of a balance.")


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


class SupplyRow(StoreInvoiceRow):
    """One recorded supply: an invoice at a store, with its delivery date if a receipt was posted."""
    store: StoreRef
    vendor_id: uuid.UUID | None = None
    delivered_on: date | None = Field(
        default=None, description="The posted receipt's received_on. None until goods are received; "
                                  "never the invoice date or processing time.")


class SupplyVendorOption(BaseModel):
    id: uuid.UUID
    label: str
    store_ids: list[uuid.UUID] = Field(description="Granted stores this vendor has invoices at.")


class SupplyOptions(BaseModel):
    stores: list[StoreRef]
    vendors: list[SupplyVendorOption]


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


class LineChangeOut(BaseModel):
    invoice_item_id: str
    item_code: str | None = None
    description: str | None = None
    change: str = Field(description="added, removed or changed since the submission.")
    proposed_units: Decimal | None = None
    current_units: Decimal | None = None
    proposed_units_per_case: int | None = None
    current_units_per_case: int | None = None


class SubmissionOut(BaseModel):
    """A pending receipt submission: what was proposed, and whether it still matches the invoice."""

    receipt: ReceiptSummary
    proposed_lines: list[ReceiptLineOut] = Field(description="The lines as submitted (Retailed snapshot).")
    proposed_blockers: list[ReceiptBlockerOut] = Field(default_factory=list)
    export_number: int | None = Field(default=None, description="The ledger-recorded PDI export Retailed rested on.")
    stale_reasons: list[str] = Field(default_factory=list,
                                     description="Why it can no longer be approved as it stands; empty when current.")
    line_changes: list[LineChangeOut] = Field(default_factory=list)


class ReceiptPreviewOut(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str | None = None
    declared_delivery_date: date | None = Field(
        default=None, description="The day the goods arrived, declared at upload or on the invoice; pre-fills the receipt date.")
    store: StoreRef | None = None
    eligibility_reasons: list[str]
    lines: list[ReceiptLineOut] = Field(description="The lines a receipt would cover now.")
    blockers: list[ReceiptBlockerOut]
    receipt: ReceiptSummary | None = Field(default=None, description="The POSTED receipt, if any.")
    submission: SubmissionOut | None = Field(default=None, description="The pending submission, if any.")
    history: list[ReceiptSummary] = Field(default_factory=list, description="Every receipt, oldest first.")
    can_approve: bool = Field(description="A pending submission exists, still matches the invoice, and can post.")


class ReceiptLineAcceptance(BaseModel):
    invoice_item_id: uuid.UUID
    accepted_units: Decimal = Field(description="Sellable units actually accepted.")
    reason: str | None = Field(default=None, max_length=500, description="Required when it differs from proposed.")


class ReceiptApproveRequest(BaseModel):
    receipt_id: uuid.UUID = Field(description="The pending submission being approved.")
    received_on: date = Field(description="The business date the goods arrived.")
    note: str | None = Field(default=None, max_length=1000)
    lines: list[ReceiptLineAcceptance] = Field(min_length=1)


class ReceiptRejectRequest(BaseModel):
    receipt_id: uuid.UUID = Field(description="The pending submission being rejected.")
    reason: str = Field(min_length=1, max_length=1000, description="Why it is rejected; kept with the record.")


class PendingApprovalRow(BaseModel):
    receipt: ReceiptSummary
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    vendor: str | None = None
    stale_reasons: list[str] = Field(default_factory=list)
    blockers: int = Field(default=0, description="Product lines without an approved conversion now.")


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
