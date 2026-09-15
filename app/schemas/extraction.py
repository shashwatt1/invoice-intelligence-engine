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

from typing import Literal

from pydantic import BaseModel, Field


class ColumnMapping(BaseModel):
    """
    The model's explicit reading of the line-item table's columns.

    Declared FIRST on ExtractedInvoice deliberately: with Structured
    Outputs the model fills fields in declaration order, so forcing it to
    name the columns and justify the unit-cost choice *before* it reads a
    single line item makes the column decision explicit and reviewable
    instead of an implicit guess repeated per row.

    Motivated by an observed production failure: on a 7-line invoice the
    model took unit_price from the gross pre-discount column on every row
    while taking line_total from the net column, overstating cost by
    9.3% (exactly the invoice's printed total discount).
    """

    column_headers_found: list[str] = Field(
        default_factory=list,
        description=(
            "Every column header printed above the line-item table, left to "
            "right, exactly as written (e.g. ['ITEM#','QTY','DESCRIPTION',"
            "'UPC','U.PRICE','DISC','D.PRICE','DEP','EXT']). Empty list if "
            "the table has no printed headers."
        ),
    )
    unit_cost_column: str | None = Field(
        default=None,
        description=(
            "Header of the column you chose as the wholesale unit cost of the "
            "GOODS: what the store pays per unit after any per-line discount, "
            "EXCLUDING any container deposit. A column headed NET is not always "
            "that: if NET equals PRICE + DEP on the rows, choose PRICE. Null if "
            "the table has no headers."
        ),
    )
    unit_cost_reasoning: str | None = Field(
        default=None,
        description=(
            "One sentence: why that column is the net unit cost and not a "
            "gross/list price, a retail price, or an extended total."
        ),
    )
    extended_total_column: str | None = Field(
        default=None, description="Header of the per-line extended total column, if present."
    )
    discount_column: str | None = Field(
        default=None, description="Header of the per-line discount column, if present."
    )
    deposit_column: str | None = Field(
        default=None, description="Header of the per-line container-deposit column, if present."
    )


class FieldConcern(BaseModel):
    """One field the model is not confident about, and why."""

    field_path: str = Field(
        description="Dotted path, e.g. 'line_items[3].unit_price' or 'grand_total'."
    )
    reason: str = Field(
        description=(
            "Short reason code plus detail. Use one of: blurry_ocr, "
            "ambiguous_column, missing_value, conflicting_totals, "
            "illegible_digit."
        )
    )


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


LINE_TYPE_PRODUCT = "product"
LINE_TYPE_CHARGE = "charge"


class ExtractedLineItem(BaseModel):
    """A single line item row as printed on the invoice."""

    line_type: Literal["product", "charge"] = Field(
        default="product",
        description=(
            "'product' for goods delivered (or shorted: a product row with quantity 0 "
            "is still a product row). 'charge' for a non-product amount printed as a "
            "row in the item table — a delivery charge, fuel/service fee, "
            "miscellaneous charge. A charge has no UPC: if the row prints a placeholder "
            "such as 000000000000, product_code is null."
        ),
    )
    description: str | None = Field(
        default=None, description="Item description exactly as printed."
    )
    product_code: str | None = Field(
        default=None,
        description=(
            "The UPC or vendor item/SKU number printed on this line, exactly as "
            "printed (keep dashes, leading zeros, and letters if present). If both "
            "a UPC/barcode number and a separate vendor item number are printed, "
            "prefer the UPC. If neither is printed, use null. Never infer, look up, "
            "or construct a code that is not directly printed on the line."
        ),
    )
    pack_size: str | None = Field(
        default=None,
        description=(
            "Units per case / pack configuration exactly as printed, if shown "
            "as its own column or embedded in the description (e.g. '24/12OZ', "
            "'12 CT'). Null if not printed. Never infer it from the product name."
        ),
    )
    quantity: float | None = Field(
        default=None,
        description=(
            "Number of cases/units delivered for this line — the QTY column. "
            "This is NOT the pack size. On a line reading '1 RB COCONUT "
            "24/12OZ', quantity is 1 and pack_size is '24/12OZ'. A row printed "
            "with quantity 0 (often annotated 'SHORT ON TRUCK', 'Out of Stock', "
            "'-1') is quantity 0 with line_total 0 — keep the row, never drop it "
            "and never read the annotation's number as the quantity."
        ),
    )
    unit_price: float | None = Field(
        default=None,
        description=(
            "Wholesale cost of the GOODS per unit — what the store pays after "
            "any per-line discount, EXCLUDING any container deposit (the deposit "
            "goes in unit_deposit). If the table shows both a gross/list price "
            "and a discounted price, this is the DISCOUNTED one. If a column is "
            "PRICE + DEP, this is PRICE, not that column. This is never a "
            "retail/shelf price. If not printed but quantity and line total are "
            "both present, derive it as line_total / quantity."
        ),
    )
    unit_discount: float | None = Field(
        default=None,
        description=(
            "Per-unit discount printed on this line (the DISC column), as a "
            "positive number. Null if no per-line discount column exists."
        ),
    )
    unit_deposit: float | None = Field(
        default=None,
        description=(
            "Per-unit container/bottle deposit printed on this line (the DEP "
            "column), as a positive number. This is real money owed and is "
            "separate from the cost of goods. Null if not printed."
        ),
    )
    line_total: float | None = Field(
        default=None,
        description=(
            "Extended total for this line as printed. Must be consistent with "
            "quantity x unit_price (some layouts additionally include the "
            "deposit in this figure — if so, still report it as printed and "
            "flag the discrepancy in concerns)."
        ),
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

    column_mapping: ColumnMapping = Field(
        default_factory=ColumnMapping,
        description=(
            "Resolve the line-item table's columns HERE, before extracting any "
            "line item. This field is answered first on purpose."
        ),
    )
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
    deposit_total: float | None = Field(
        default=None,
        description=(
            "Invoice-level container/bottle deposit total, if printed in the "
            "totals block (labels such as 'Total Deposit', 'Dep$', 'Container "
            "Deposit', 'NY CONTAINER DEPOSIT'). Positive number. NEVER a delivery, "
            "fuel, service or miscellaneous charge — those are not deposits. Null "
            "if no deposit total is printed."
        ),
    )
    fuel_surcharge: float | None = Field(
        default=None,
        description=(
            "Fuel surcharge / delivery fee / service charge printed in the TOTALS "
            "block as its own line (not inside the item table). Positive number. "
            "If the same charge is printed as a row of the item table, report it "
            "there as a line item with line_type 'charge' as well; the two are "
            "reconciled deterministically downstream."
        ),
    )
    grand_total: float | None = Field(
        default=None, description="Final amount payable as printed."
    )
    line_items: list[ExtractedLineItem] = Field(
        description="All line items in the order they appear on the document."
    )
    concerns: list[FieldConcern] = Field(
        default_factory=list,
        description=(
            "Every field you were not confident about, with a reason. Empty "
            "list if none. Prefer listing a concern over silently guessing."
        ),
    )
    confidence: float | None = Field(
        default=None,
        description="Your overall confidence in this extraction, 0.0 to 1.0.",
    )
