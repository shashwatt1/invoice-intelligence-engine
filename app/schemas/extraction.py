"""
Invoice Extraction Schemas — app/schemas/extraction.py

The canonical structured-invoice contract produced by the AI structuring
layer. Everything downstream (validation engine, persistence, frontend)
consumes these models.

Design decisions:
- These are LLM-boundary models used with OpenAI Structured Outputs
  (strict JSON schema mode). Strict mode does not support `format: date`
  or arbitrary-precision decimals, so:
    * dates are ISO-8601 strings (parsed to `date` by the validation engine)
    * money is float (quantized to Decimal at the persistence boundary,
      where the DB columns are NUMERIC)
- Every field is nullable. Strict mode requires every key to be present,
  so "missing on the document" is expressed as null — never as an absent
  key and never as a fabricated value.
- Field descriptions are injected into the JSON schema sent to the model;
  they are part of the prompt surface, keep them precise.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedVendor(BaseModel):
    """Vendor identity as printed on the invoice."""

    name: str | None = Field(
        default=None, description="Vendor / supplier business name exactly as printed."
    )
    tax_id: str | None = Field(
        default=None, description="Vendor VAT / GST / tax registration number, if printed."
    )
    address: str | None = Field(
        default=None, description="Full vendor address as a single string."
    )
    phone: str | None = Field(default=None, description="Vendor phone number, if printed.")
    email: str | None = Field(default=None, description="Vendor email address, if printed.")


class ExtractedLineItem(BaseModel):
    """A single line item row as printed on the invoice."""

    description: str | None = Field(
        default=None, description="Item description exactly as printed."
    )
    quantity: float | None = Field(
        default=None, description="Quantity ordered. May be fractional (e.g. 1.5)."
    )
    unit_price: float | None = Field(
        default=None,
        description=(
            "Price per unit. If not printed but quantity and line total are both "
            "present, derive it as line_total / quantity."
        ),
    )
    line_total: float | None = Field(
        default=None, description="Total for this line as printed (after item discount)."
    )
    tax_rate: float | None = Field(
        default=None, description="Tax rate for this line as a percentage (e.g. 18.0 for 18%)."
    )
    confidence: float | None = Field(
        default=None,
        description="Your confidence that this row was read correctly, 0.0 to 1.0.",
    )


class ExtractedInvoice(BaseModel):
    """
    Structured invoice header + line items extracted from document text.

    This is the canonical output of the AI structuring layer.
    """

    vendor: ExtractedVendor = Field(description="Vendor identity block.")
    invoice_number: str | None = Field(
        default=None, description="Invoice number / ID exactly as printed."
    )
    invoice_date: str | None = Field(
        default=None, description="Invoice issue date in ISO-8601 format (YYYY-MM-DD)."
    )
    due_date: str | None = Field(
        default=None, description="Payment due date in ISO-8601 format (YYYY-MM-DD)."
    )
    currency: str | None = Field(
        default=None, description="ISO 4217 currency code (e.g. USD, EUR, INR)."
    )
    purchase_order: str | None = Field(
        default=None, description="Purchase order (PO) number referenced on the invoice."
    )
    payment_terms: str | None = Field(
        default=None, description="Payment terms as printed (e.g. 'Net 30')."
    )
    subtotal: float | None = Field(
        default=None, description="Sum of line totals before tax and invoice-level discount."
    )
    tax_amount: float | None = Field(
        default=None, description="Total tax amount in currency units (not a percentage)."
    )
    discount_amount: float | None = Field(
        default=None, description="Invoice-level discount amount, if any."
    )
    grand_total: float | None = Field(
        default=None, description="Final amount payable as printed."
    )
    line_items: list[ExtractedLineItem] = Field(
        description="All line items in the order they appear on the document."
    )
    confidence: float | None = Field(
        default=None,
        description="Your overall confidence in this extraction, 0.0 to 1.0.",
    )
