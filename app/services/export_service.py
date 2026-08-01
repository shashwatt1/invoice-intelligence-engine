"""
Invoice Export Service — app/services/export_service.py

Builds ERP-consumable representations of a persisted invoice:

    JSON — the final validated invoice object (what validation wrote to
           the database), NOT the raw LLM response
    TXT  — human-readable summary
    CSV  — line items for accounting systems

Design decisions:
- Purely read-side and additive: everything here is a projection of the
  Invoice ORM aggregate loaded by InvoiceRepository.get_detail(). No
  pipeline or validation logic is duplicated — the persisted row IS the
  validated object.
- Money is emitted as JSON numbers (consistent with the existing detail
  API); the database remains the precision source of truth.
- schema_version lets ERP consumers integrate against a stable contract
  that can evolve additively.
"""

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal
from typing import Any

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem

EXPORT_SCHEMA_VERSION = "1.0"

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _sorted_items(invoice: Invoice) -> list[InvoiceItem]:
    return sorted(invoice.items, key=lambda item: item.sort_order)


def export_basename(invoice: Invoice) -> str:
    """Safe filename stem: invoice number if usable, else the invoice id."""
    stem = invoice.invoice_number or str(invoice.id)[:8]
    cleaned = _FILENAME_SAFE.sub("-", stem).strip("-.")
    return f"invoice_{cleaned or str(invoice.id)[:8]}"


# ---------------------------------------------------------------------------
# JSON — the validated structured invoice
# ---------------------------------------------------------------------------


def build_export_payload(invoice: Invoice) -> dict[str, Any]:
    """The final validated invoice object, as persisted by the pipeline."""
    vendor = invoice.vendor
    document = invoice.document
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "invoice_id": str(invoice.id),
        "document_id": str(invoice.document_id),
        "invoice": {
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "currency": invoice.currency,
            "totals": {
                "subtotal": _num(invoice.subtotal),
                "tax": _num(invoice.tax_amount),
                "discount": _num(invoice.discount_amount),
                "grand_total": _num(invoice.grand_total),
            },
        },
        "vendor": {
            "name": vendor.name if vendor else invoice.vendor_name,
            "tax_id": vendor.tax_id if vendor else invoice.vendor_tax_id,
            "address": vendor.address if vendor else invoice.vendor_address,
            "phone": vendor.phone if vendor else None,
            "email": vendor.email if vendor else None,
        },
        "line_items": [
            {
                "position": index + 1,
                "description": item.description,
                "quantity": _num(item.quantity),
                "unit_price": _num(item.unit_price),
                "line_total": _num(item.line_total),
                "tax_rate": _num(item.tax_rate),
                "sku_upc": item.product_sku,
            }
            for index, item in enumerate(_sorted_items(invoice))
        ],
        "validation": {
            "status": invoice.status,
            "review_required": invoice.status == "REVIEW_REQUIRED",
            "composite_confidence": _num(invoice.composite_confidence),
        },
        "processing": {
            "filename": document.filename if document else None,
            "source_type": document.source_type if document else None,
            "extraction_model": invoice.extraction_model,
            "processed_at": invoice.created_at.isoformat() if invoice.created_at else None,
        },
    }


# ---------------------------------------------------------------------------
# TXT — human-readable summary
# ---------------------------------------------------------------------------

_RULE = "-" * 48


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def build_txt(invoice: Invoice) -> str:
    payload = build_export_payload(invoice)
    header = payload["invoice"]
    totals = header["totals"]
    vendor = payload["vendor"]
    validation = payload["validation"]

    lines: list[str] = [
        "Vendor",
        "------",
        _fmt(vendor["name"]),
        "",
        "Invoice Number:",
        _fmt(header["invoice_number"]),
        "",
        "Invoice Date:",
        _fmt(header["invoice_date"]),
        "",
        "Due Date:",
        _fmt(header["due_date"]),
        "",
        "Currency:",
        _fmt(header["currency"]),
        "",
        _RULE,
        "",
        "Items",
        "",
    ]

    for item in payload["line_items"]:
        lines += [
            f"{item['position']}.",
            "Description:",
            _fmt(item["description"]),
            "",
            "Quantity:",
            _fmt(item["quantity"]),
            "",
            "Unit Price:",
            _fmt(item["unit_price"]),
            "",
            "Line Total:",
            _fmt(item["line_total"]),
            "",
        ]

    lines += [
        _RULE,
        "",
        "Totals",
        "",
        "Subtotal:",
        _fmt(totals["subtotal"]),
        "",
        "Tax:",
        _fmt(totals["tax"]),
        "",
        "Discount:",
        _fmt(totals["discount"]),
        "",
        "Grand Total:",
        f"{_fmt(totals['grand_total'])} {header['currency'] or ''}".strip(),
        "",
        _RULE,
        "",
        "Validation Status:",
        _fmt(validation["status"]),
        "",
        "Confidence:",
        f"{validation['composite_confidence'] * 100:.1f}%"
        if validation["composite_confidence"] is not None
        else "—",
        "",
        "Review Required:",
        "Yes" if validation["review_required"] else "No",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV — line items for accounting systems
# ---------------------------------------------------------------------------

CSV_HEADERS = ["Description", "Quantity", "Unit Price", "Line Total", "Tax Rate (%)", "UPC"]


def build_items_csv(invoice: Invoice) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for item in _sorted_items(invoice):
        writer.writerow(
            [
                item.description,
                _num(item.quantity),
                _num(item.unit_price),
                _num(item.line_total),
                _num(item.tax_rate) if item.tax_rate is not None else "",
                item.product_sku or "",
            ]
        )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDI — fixed-width positional import format
#
# Reverse-engineered from real PDI import files (see field mapping report,
# "PDI Export — UPC Capture + Formatter" milestone). Field confidence is
# NOT uniform — see the module-level note below before trusting this output
# for a live PDI import without a human review pass.
#
# Detail record layout (70 chars, 0-indexed slices):
#   [0]      "B"                     record type
#   [1:12]   item code               11 digits, zero-padded, right-justified
#   [12:37]  description             25 chars, left-justified, space-padded
#   [37:57]  "block A"               20 digits — cost/price encoding, UNVERIFIED
#   [57]     sign                    "+" (no credit/return concept exists yet)
#   [58:62]  quantity                4 digits, zero-padded
#   [62:70]  "block B tail"          8 digits — cost/UOM encoding, UNVERIFIED
#
# Header record: "AMOUNT {batch:>7}   {date:6}{sign}{amount_cents:09d}"
# Trailer records (CFUE/CPPT fee lines) are intentionally NOT emitted: we
# have no extracted data for fuel surcharge or itemized tax this milestone
# (see field mapping report) — omitting is safer than fabricating a $0 line
# that looks like real, verified data.
# ---------------------------------------------------------------------------

PDI_ITEM_CODE_WIDTH = 11
PDI_DESCRIPTION_WIDTH = 25
PDI_QUANTITY_WIDTH = 4
PDI_BLOCK_A_WIDTH = 20  # cost/price — UNVERIFIED, always emitted as zeros
PDI_BLOCK_B_TAIL_WIDTH = 8  # cost/UOM — UNVERIFIED, always emitted as zeros
PDI_BATCH_WIDTH = 7
PDI_DATE_FORMAT = "%m%d%y"
PDI_AMOUNT_WIDTH = 9

_NON_DIGITS = re.compile(r"\D")


def _pdi_item_code(product_code: str | None) -> str:
    """
    Reduce an extracted product_code to PDI's 11-digit item-code field.

    Rule: a 12-digit code is treated as UPC-A and reduced by dropping its
    trailing check digit (the standard "UPC without check digit" convention
    many POS/back-office systems use) — UNVERIFIED against a matched
    ground-truth PDI file, but a documented, deterministic, industry-standard
    transformation rather than a guess. Shorter codes (e.g. a vendor item
    number) are zero-padded on the left. No code on file → 11 spaces,
    matching the blank-code rows observed in real PDI samples.
    """
    if not product_code:
        return " " * PDI_ITEM_CODE_WIDTH
    digits = _NON_DIGITS.sub("", product_code)
    if not digits:
        return " " * PDI_ITEM_CODE_WIDTH
    if len(digits) == 12:
        digits = digits[:-1]
    if len(digits) > PDI_ITEM_CODE_WIDTH:
        digits = digits[:PDI_ITEM_CODE_WIDTH]
    return digits.rjust(PDI_ITEM_CODE_WIDTH, "0")


def _pdi_description(description: str) -> str:
    return description[:PDI_DESCRIPTION_WIDTH].ljust(PDI_DESCRIPTION_WIDTH)


def _pdi_quantity(quantity: Decimal | None) -> str:
    """
    4-digit zero-padded quantity. A quantity of 10000+ (never seen in any
    real sample) would overflow the field rather than being silently
    truncated to a wrong-but-plausible-looking number.
    """
    value = int(quantity) if quantity is not None else 0
    return str(max(value, 0)).rjust(PDI_QUANTITY_WIDTH, "0")


def _pdi_detail_line(item: InvoiceItem) -> str:
    return (
        "B"
        + _pdi_item_code(item.product_sku)
        + _pdi_description(item.description)
        + "0" * PDI_BLOCK_A_WIDTH
        + "+"
        + _pdi_quantity(item.quantity)
        + "0" * PDI_BLOCK_B_TAIL_WIDTH
    )


def _pdi_header_line(invoice: Invoice) -> str:
    batch = _NON_DIGITS.sub("", invoice.invoice_number or "")
    batch = (batch[-PDI_BATCH_WIDTH:] if batch else "").rjust(PDI_BATCH_WIDTH, "0")
    date_str = invoice.invoice_date.strftime(PDI_DATE_FORMAT) if invoice.invoice_date else "0" * 6
    cents = round(float(invoice.grand_total) * 100) if invoice.grand_total is not None else 0
    return f"AMOUNT {batch}   {date_str}+{cents:0{PDI_AMOUNT_WIDTH}d}"


def build_pdi_export(invoice: Invoice) -> str:
    """
    Deterministic PDI-compatible fixed-width export.

    High-confidence fields (verified against real PDI samples): record
    structure, item code, description, quantity. Low-confidence fields
    (cost/price encoding, batch-number semantics): emitted as documented
    placeholders (zeros / best-effort invoice number), never fabricated to
    look more certain than they are. See the field mapping report for the
    full confidence breakdown before relying on this for a live import.
    """
    lines = [_pdi_header_line(invoice)]
    lines.extend(_pdi_detail_line(item) for item in _sorted_items(invoice))
    return "\n".join(lines) + "\n"
