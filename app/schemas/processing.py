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
from typing import Any

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
    unit_price: float
    line_total: float
    tax_rate: float | None = None
    sort_order: int = 0


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
