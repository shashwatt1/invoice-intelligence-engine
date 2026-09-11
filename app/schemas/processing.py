"""
Processing & Read-Model API Schemas — app/schemas/processing.py

Response payloads for the processing pipeline and the read endpoints
that power the frontend (status polling, invoice details, history,
dashboard summary).

Design decisions:
- Money and confidence are floats here: these are display-layer
  contracts. Precision-critical Decimals live in the database and the
  validation layer; the API serializes for human consumption.
- DocumentStatusData.is_terminal saves every client from re-deriving
  the terminal-status set.
- InvoiceDetailData deliberately includes the developer-panel payloads
  (raw OCR text, raw structured output, LLM/validation metadata) so the
  detail view needs exactly one request.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


class ProcessAccepted(BaseModel):
    """Returned by POST /invoices/process (202)."""

    document_id: uuid.UUID
    filename: str
    status: str = Field(description="Initial document status (UPLOADED).")
    status_url: str = Field(description="Poll this endpoint for live progress.")


class InvoiceDeleteResult(BaseModel):
    """Returned by DELETE /invoices/{id}."""

    invoice_id: uuid.UUID
    document_id: uuid.UUID
    deleted: bool = True


class CaseMappingRow(BaseModel):
    """One line item's units-per-case state, for the review UI."""

    item_code: str | None = Field(
        default=None, description="Normalized UPC; null when the line has no usable code."
    )
    description: str | None = None
    pack_size: str | None = Field(
        default=None, description="Pack descriptor as printed, shown as evidence."
    )
    units_per_case: int | None = Field(
        default=None, description="Confirmed units per case, when a mapping exists."
    )
    suggested_units_per_case: int | None = Field(
        default=None,
        description="Document-derived suggestion; requires confirmation before use.",
    )
    suggestion_source: str | None = Field(
        default=None,
        description=(
            "Where the displayed value came from: 'database' (confirmed "
            "mapping, the only source ever used for an EDI), 'pack_size' (a "
            "dedicated pack column), 'reference' (derived from the store's own per-unit cost), 'description' (N/M notation in the "
            "description), or 'description_ambiguous' (a form such as "
            "'4/6/16OZ' whose leading number does not settle units-per-case). "
            "Null when nothing could be suggested."
        ),
    )
    suggestion_candidates: list[int] = Field(
        default_factory=list,
        description=(
            "Units-per-case readings a structurally ambiguous description could "
            "support, smallest first (e.g. '4/6/16OZ' -> [4, 24]). Empty when the "
            "packaging is unambiguous. The UI offers these instead of prefilling, "
            "so an ambiguous product cannot be confirmed with a single click."
        ),
    )
    reference_description: str | None = Field(
        default=None,
        description=(
            "Product name in the store's own catalogue, when matched by exact "
            "UPC. A hint for the operator; never used to match automatically."
        ),
    )
    reference_avg_cost: float | None = Field(
        default=None,
        description=(
            "The store's per-selling-unit cost, when known. Evidence behind a "
            "reference-derived suggestion; never written to an EDI."
        ),
    )
    mapped: bool = Field(description="False when this product still needs a mapping.")


class CaseMappingConfirmation(BaseModel):
    """One product's units-per-case, as confirmed by a person."""

    item_code: str = Field(min_length=1, description="Normalized UPC / item code.")
    units_per_case: int = Field(ge=1, le=9999, description="Units contained in one case.")
    description: str | None = Field(
        default=None, description="Product description, stored to identify the row later."
    )


class CaseMappingRequest(BaseModel):
    """Body of POST /invoices/{id}/case-mappings — confirm one or many."""

    mappings: list[CaseMappingConfirmation] = Field(
        min_length=1, description="Products to confirm; several can be resolved at once."
    )


class CaseMappingResult(BaseModel):
    """Returned after confirming mappings: the invoice's refreshed state."""

    saved: int
    case_mappings: list[CaseMappingRow]
    pdi_export_allowed: bool
    pdi_export_blocked_reason: str | None = None


class LineItemCorrection(BaseModel):
    """
    Body of PATCH /invoices/{id}/items/{sort_order}.

    Only the transaction values a person may legitimately need to supply
    when extraction could not associate them. Deliberately narrow: this
    is a correction path for a handful of unreadable figures, not an
    invoice editor. Units-per-case is not here — it has its own confirm
    endpoint and its own authority table.

    Every field is optional; at least one must be given. A field left out
    keeps its extracted value.
    """

    # Range is enforced in the endpoint rather than with a Field
    # constraint: a `ge` on a Decimal field puts a Decimal into the
    # validation error context, which the shared exception handler cannot
    # serialize (it raises TypeError and returns 500 instead of 422).
    unit_price: Decimal | None = Field(
        default=None, description="Net cost per unit, as printed. Must not be negative."
    )
    quantity: Decimal | None = Field(
        default=None, description="Quantity delivered, as printed. Must not be negative."
    )
    line_total: Decimal | None = Field(
        default=None,
        description="Extended total for the line, as printed. Must not be negative.",
    )
    unit_deposit: Decimal | None = Field(
        default=None,
        description=(
            "Per-unit container deposit, as printed. NOT part of product cost "
            "and never written to an EDI — it is what lets reconciliation "
            "prove (cost + deposit) x quantity = line total on layouts that "
            "fold the deposit into the extended total."
        ),
    )

    # API field name -> ORM column. The column predates the extraction
    # schema's `unit_deposit` naming; the API uses the clearer name and
    # records that name in corrected_fields, so provenance reads the way
    # the operator saw it.
    COLUMNS: ClassVar[dict[str, str]] = {
        "unit_price": "unit_price",
        "quantity": "quantity",
        "line_total": "line_total",
        "unit_deposit": "deposit",
    }

    def updates(self) -> dict[str, Decimal]:
        return {
            field: value
            for field, value in (
                ("unit_price", self.unit_price),
                ("quantity", self.quantity),
                ("line_total", self.line_total),
                ("unit_deposit", self.unit_deposit),
            )
            if value is not None
        }


class CorrectedLineItem(BaseModel):
    """One line item after correction, with its provenance."""

    sort_order: int
    description: str
    quantity: float
    unit_price: float | None = None
    line_total: float | None = None
    unit_deposit: float | None = None
    corrected_fields: list[str] = Field(
        default_factory=list,
        description="Fields on this line replaced by a person, never by extraction.",
    )


class LineItemCorrectionResult(BaseModel):
    """The corrected line plus the invoice's re-judged state."""

    item: CorrectedLineItem
    status: str = Field(description="VALIDATED or REVIEW_REQUIRED after revalidation.")
    composite_confidence: float
    failed_checks: int
    review_reasons: list[str] = Field(default_factory=list)
    pdi_export_allowed: bool
    pdi_export_blocked_reason: str | None = None


class StageEntry(BaseModel):
    """One processing-log entry in the document timeline."""

    stage: str
    status: str
    message: str | None = None
    duration_ms: int | None = None
    created_at: datetime
    payload: dict[str, Any] | None = None


class DocumentStatusData(BaseModel):
    """Live document status — drives the processing timeline."""

    document_id: uuid.UUID
    filename: str
    status: str
    is_terminal: bool
    source_type: str | None = None
    invoice_id: uuid.UUID | None = None
    error: dict[str, Any] | None = Field(
        default=None, description="Failure log payload when status is FAILED."
    )
    stages: list[StageEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Invoice details
# ---------------------------------------------------------------------------


class LineItemData(BaseModel):
    description: str
    quantity: float
    # Null when extraction could not read the figure. Distinct from 0.00,
    # and never coerced — a line with an unknown cost blocks PDI export
    # rather than being exported as free goods (see migration 0005).
    unit_price: float | None = None
    line_total: float | None = None
    tax_rate: float | None = None
    unit_deposit: float | None = Field(
        default=None,
        description=(
            "Per-unit container deposit as extracted. Null when it could not "
            "be read — which prevents reconciliation from explaining a line "
            "whose total includes it."
        ),
    )
    sort_order: int = 0
    corrected_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Transaction fields on this line replaced by a person. Empty means "
            "every value came from extraction."
        ),
    )


class VendorData(BaseModel):
    id: uuid.UUID
    name: str
    tax_id: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None


class DatabaseConfirmation(BaseModel):
    """Persistence proof for the demo's database panel."""

    vendor_saved: bool
    invoice_saved: bool
    items_saved: int
    logs_saved: int
    duplicate_check_passed: bool
    processing_duration_ms: int


class InvoiceDetailData(BaseModel):
    """Everything the invoice detail view needs in one request."""

    invoice_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    document_status: str
    source_type: str | None = None

    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str
    subtotal: float | None = None
    tax_amount: float | None = None
    discount_amount: float | None = None
    grand_total: float | None = None

    status: str = Field(description="Invoice decision status: VALIDATED or REVIEW_REQUIRED.")
    composite_confidence: float | None = None
    extraction_model: str | None = None
    created_at: datetime

    pdi_export_allowed: bool = Field(
        description="Whether GET .../export?format=pdi will succeed for this invoice."
    )
    pdi_export_requires_confirmation: bool = Field(
        description=(
            "True for REVIEW_REQUIRED invoices: the export is allowed, but the "
            "client should confirm with the user before downloading, since the "
            "underlying data may contain extraction inaccuracies."
        )
    )
    pdi_export_blocked_reason: str | None = Field(
        default=None, description="Why the export is blocked, when pdi_export_allowed is false."
    )
    case_mappings: list[CaseMappingRow] = Field(
        default_factory=list,
        description=(
            "Per-line units-per-case state. Any row with mapped=false must be "
            "confirmed before the PDI export is allowed."
        ),
    )

    vendor: VendorData | None = None
    line_items: list[LineItemData] = Field(default_factory=list)

    validation_report: dict[str, Any] | None = None
    llm_metadata: dict[str, Any] | None = None
    database: DatabaseConfirmation

    # Developer panel
    ocr_text: str | None = None
    raw_extraction: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# History & dashboard
# ---------------------------------------------------------------------------


class HistoryRow(BaseModel):
    """One row in processing history / recent activity."""

    document_id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    filename: str
    status: str = Field(description="Document lifecycle status.")
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    grand_total: float | None = None
    currency: str | None = None
    composite_confidence: float | None = None
    source_type: str | None = None
    created_at: datetime


class DashboardData(BaseModel):
    """Executive summary powering the dashboard."""

    total_documents: int
    completed: int
    review_required: int
    failed: int
    in_progress: int
    success_rate: float | None = Field(
        default=None, description="COMPLETED / terminal documents; null before any finish."
    )
    average_confidence: float | None = None
    average_processing_ms: float | None = None
    total_tokens: int = 0
    total_estimated_cost_usd: float = 0.0
    status_breakdown: dict[str, int] = Field(default_factory=dict)
    recent: list[HistoryRow] = Field(default_factory=list)
