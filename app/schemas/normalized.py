"""
Normalized Invoice Schemas — app/schemas/normalized.py

The canonical, strongly typed invoice produced by the validation engine.
This is what the persistence layer writes and what APIs return.

Design decisions:
- ExtractedInvoice (app/schemas/extraction.py) is the LLM-boundary model:
  floats and ISO strings, because strict Structured Outputs cannot emit
  Decimals or dates. NormalizedInvoice is the platform-boundary model:
  quantized Decimal money and real `date` objects, matching the NUMERIC
  and DATE columns in the database exactly.
- Derivation flags (`unit_price_derived`, `line_total_derived`) record
  which values were computed by the validation engine rather than read
  from the document — the original extraction is always preserved in
  invoices.raw_extraction_json, so nothing is ever lost.
- Money is quantized to 2 dp; quantity and unit price to 4 dp (fractional
  quantities like 1.5 kg, sub-cent unit prices), matching the DDL.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class NormalizedLineItem(BaseModel):
    """A line item with canonical numeric types, ready for persistence."""

    model_config = ConfigDict(frozen=True)

    description: str | None = None
    quantity: Decimal | None = None          # 4 dp
    unit_price: Decimal | None = None        # 4 dp
    line_total: Decimal | None = None        # 2 dp
    tax_rate: Decimal | None = None          # percentage, e.g. 18.00
    confidence: float | None = None
    sort_order: int = 0
    unit_price_derived: bool = False
    line_total_derived: bool = False


class NormalizedInvoice(BaseModel):
    """The canonical invoice consumed by persistence and the API layer."""

    model_config = ConfigDict(frozen=True)

    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    vendor_address: str | None = None
    vendor_phone: str | None = None
    vendor_email: str | None = None

    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None              # ISO 4217, upper-case
    purchase_order: str | None = None
    payment_terms: str | None = None

    subtotal: Decimal | None = None          # 2 dp
    tax_amount: Decimal | None = None        # 2 dp
    discount_amount: Decimal | None = None   # 2 dp
    grand_total: Decimal | None = None       # 2 dp

    line_items: tuple[NormalizedLineItem, ...] = ()
    ai_confidence: float | None = None       # model's self-reported confidence
