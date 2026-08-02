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
# Reverse-engineered from real PDI import files. Field confidence is NOT
# uniform — see docs/PDI_OPEN_QUESTIONS.md.
#
# Every field is produced by its own small, named encoder below, grouped
# CONFIRMED (verified against real PDI samples) vs. PLACEHOLDER (business
# rule not yet known — see docs/PDI_OPEN_QUESTIONS.md). Confirming a rule
# means changing exactly one function; nothing else in this module, the
# API, or the frontend needs to change.
#
# Detail record layout (70 chars, 0-indexed slices):
#   [0]      "B"                     record type
#   [1:12]   item code               11 digits — CONFIRMED (_pdi_item_code)
#   [12:37]  description             25 chars  — CONFIRMED (_pdi_description)
#   [37:57]  cost block              20 digits — calculation CONFIRMED (unit
#                                                 cost, per business rule),
#                                                 digit-layout UNCONFIRMED
#                                                 (_pdi_cost_block)
#   [57]     sign                    1 char    — CONFIRMED (_pdi_sign)
#   [58:62]  quantity                4 digits  — CONFIRMED (_pdi_quantity)
#   [62:70]  cost block tail         8 digits  — calculation CONFIRMED
#                                                 (extended cost = unit cost
#                                                 × quantity), digit-layout
#                                                 UNCONFIRMED (_pdi_cost_tail)
#
# Header record: "AMOUNT {batch}   {date}{sign}{amount}"
#   batch  — CONFIRMED (_pdi_batch_number: the invoice's own reference number)
#   date   — CONFIRMED format, semantics open (_pdi_date, see open questions)
#   amount — CONFIRMED (_pdi_amount_cents)
#
# Sign — CONFIRMED (_pdi_sign): "+" for a normal invoice, "-" for a return
# (grand_total negative). Applied uniformly to the header and every detail
# line, matching every real sample: a file is either entirely a delivery or
# entirely a return, never mixed.
#
# Trailer records (CFUE/CPPT fee lines) — PLACEHOLDER (_pdi_trailer_lines),
# currently always empty: no source data is captured for fuel surcharge or
# itemized tax. Omitting is safer than fabricating a $0 line that looks
# like real, verified data.
# ---------------------------------------------------------------------------

PDI_ITEM_CODE_WIDTH = 11
PDI_DESCRIPTION_WIDTH = 25
PDI_QUANTITY_WIDTH = 4
PDI_BLOCK_A_WIDTH = 20  # unit cost in cents — digit-layout unconfirmed
PDI_BLOCK_B_TAIL_WIDTH = 8  # extended cost in cents — digit-layout unconfirmed
PDI_BATCH_WIDTH = 7
PDI_DATE_FORMAT = "%m%d%y"
PDI_AMOUNT_WIDTH = 9

_NON_DIGITS = re.compile(r"\D")


# ---------------------------------------------------------------------------
# CONFIRMED field encoders — verified against real PDI samples
# ---------------------------------------------------------------------------


def _pdi_item_code(product_code: str | None) -> str:
    """
    Reduce an extracted product_code to PDI's 11-digit item-code field.

    Rule: a 12-digit code is treated as UPC-A and reduced by dropping its
    trailing check digit (the standard "UPC without check digit" convention
    many POS/back-office systems use) — UNVERIFIED against a matched
    ground-truth PDI file, but a documented, deterministic, industry-standard
    transformation rather than a guess. Shorter codes (e.g. a vendor item
    number) are zero-padded on the left.

    No code on file → "00000" followed by 6 spaces. This exact convention
    (not all-spaces, not all-zeros) is CONFIRMED against a real blank-code
    row in a supplied PDI ground-truth file — see the PDI compatibility
    report, Track A case 3.
    """
    if not product_code:
        return "00000" + " " * (PDI_ITEM_CODE_WIDTH - 5)
    digits = _NON_DIGITS.sub("", product_code)
    if not digits:
        return "00000" + " " * (PDI_ITEM_CODE_WIDTH - 5)
    if len(digits) == 12:
        digits = digits[:-1]
    if len(digits) > PDI_ITEM_CODE_WIDTH:
        digits = digits[:PDI_ITEM_CODE_WIDTH]
    return digits.rjust(PDI_ITEM_CODE_WIDTH, "0")


def _pdi_description(description: str) -> str:
    """CONFIRMED: 25 chars, left-justified, space-padded/truncated."""
    return description[:PDI_DESCRIPTION_WIDTH].ljust(PDI_DESCRIPTION_WIDTH)


def _pdi_quantity(quantity: Decimal | None) -> str:
    """
    CONFIRMED: 4-digit zero-padded quantity. A quantity of 10000+ (never
    seen in any real sample) would overflow the field rather than being
    silently truncated to a wrong-but-plausible-looking number.
    """
    value = int(quantity) if quantity is not None else 0
    return str(max(value, 0)).rjust(PDI_QUANTITY_WIDTH, "0")


def _pdi_is_return(invoice: Invoice) -> bool:
    """A return/credit invoice is one whose grand total was extracted as
    negative — no new data is required to detect this, extraction and
    validation already preserve the printed sign on grand_total."""
    return invoice.grand_total is not None and invoice.grand_total < 0


def _pdi_sign(invoice: Invoice) -> str:
    """
    CONFIRMED: "+" for a normal invoice, "-" for a return/credit invoice.
    Applied uniformly to the header and every detail line — every real
    sample file was either entirely a delivery or entirely a return, never
    mixed line-by-line, so the sign is computed once per invoice.
    """
    return "-" if _pdi_is_return(invoice) else "+"


def _pdi_date(invoice: Invoice) -> str:
    """CONFIRMED format (MMDDYY). Semantics (invoice date vs. some other
    date PDI expects) unconfirmed but low-risk — see open questions."""
    return invoice.invoice_date.strftime(PDI_DATE_FORMAT) if invoice.invoice_date else "0" * 6


def _pdi_amount_cents(invoice: Invoice) -> str:
    """
    CONFIRMED: integer cents, zero-padded, magnitude only — cross-checked
    against fee amounts in supplied PDI ground-truth files. The sign
    character (_pdi_sign) carries direction; this field is always a plain
    positive digit string, for a return invoice exactly as for a normal
    one — matching every real sample, where the amount digits never
    contained a minus sign, only the dedicated sign character did.
    """
    cents = round(float(abs(invoice.grand_total)) * 100) if invoice.grand_total is not None else 0
    return f"{cents:0{PDI_AMOUNT_WIDTH}d}"


# ---------------------------------------------------------------------------
# Cost calculation — CONFIRMED business rule, isolated from its encoding
#
# Business rule: the cost section is derived from the line's unit cost and
# quantity. This is now a real calculation, not a placeholder. What is
# still unconfirmed is the fixed-width DIGIT LAYOUT the calculated values
# are packed into — see docs/PDI_OPEN_QUESTIONS.md Q1. Keeping calculation
# and encoding as separate functions means a future correction to the
# digit layout touches only the two _pdi_cost_* encoders below, never this
# calculation.
# ---------------------------------------------------------------------------


def _pdi_unit_cost_cents(item: InvoiceItem) -> int:
    """Unit cost in cents, magnitude only. CONFIRMED calculation."""
    return round(float(abs(item.unit_price)) * 100)


def _pdi_extended_cost_cents(item: InvoiceItem) -> int:
    """Extended cost (unit cost x quantity) in cents, magnitude only.
    CONFIRMED calculation."""
    return round(float(abs(item.unit_price)) * float(abs(item.quantity)) * 100)


# ---------------------------------------------------------------------------
# Field encoders — calculation confirmed, exact digit-layout unconfirmed
#
# Placement rationale, not a guess: cross-file comparison of the supplied
# PDI ground-truth files showed the 20-digit block is IDENTICAL for the
# same item across invoices with different delivered quantities — the
# signature of a per-product constant (unit cost), not a per-delivery
# total. The 8-digit tail sits beside the quantity field and was observed
# to vary per delivery, consistent with an extended (quantity-dependent)
# cost. See docs/PDI_OPEN_QUESTIONS.md Q1 for what remains open: whether
# cents-scaled, right-justified magnitude is the correct layout within
# each block, or whether either block holds additional sub-fields.
# ---------------------------------------------------------------------------


def _pdi_cost_block(item: InvoiceItem) -> str:
    """Unit cost in cents, zero-padded to 20 digits. Not truncated on
    overflow (practically impossible at this width, but consistent with
    _pdi_quantity: never silently corrupt a real value into a
    wrong-but-plausible one). See module note above and
    docs/PDI_OPEN_QUESTIONS.md Q1."""
    return str(_pdi_unit_cost_cents(item)).rjust(PDI_BLOCK_A_WIDTH, "0")


def _pdi_cost_tail(item: InvoiceItem) -> str:
    """Extended cost (unit cost x quantity) in cents, zero-padded to 8
    digits. Not truncated on overflow, for the same reason as
    _pdi_cost_block. See module note above and
    docs/PDI_OPEN_QUESTIONS.md Q1."""
    return str(_pdi_extended_cost_cents(item)).rjust(PDI_BLOCK_B_TAIL_WIDTH, "0")


def _pdi_batch_number(invoice: Invoice) -> str:
    """
    CONFIRMED: the invoice's own reference number (invoice_number), as
    extracted from the document. Non-digit characters are stripped and the
    result is fit to the fixed 7-digit field — trailing digits are kept
    (right-aligned) when longer, zero-padded on the left when shorter.
    """
    batch = _NON_DIGITS.sub("", invoice.invoice_number or "")
    return (batch[-PDI_BATCH_WIDTH:] if batch else "").rjust(PDI_BATCH_WIDTH, "0")


# ---------------------------------------------------------------------------
# Structural placeholder — no source data, not an encoding question
# ---------------------------------------------------------------------------


def _pdi_trailer_lines(invoice: Invoice) -> list[str]:
    """PLACEHOLDER — fuel surcharge / prepaid tax trailer records
    (CFUE/CPPT). See docs/PDI_OPEN_QUESTIONS.md Q4. No source data is
    captured for these today, so nothing is emitted. This is the function
    to implement once that capture is confirmed as in scope."""
    return []


# ---------------------------------------------------------------------------
# Record composition
# ---------------------------------------------------------------------------


def _pdi_detail_line(item: InvoiceItem, *, invoice: Invoice) -> str:
    return (
        "B"
        + _pdi_item_code(item.product_sku)
        + _pdi_description(item.description)
        + _pdi_cost_block(item)
        + _pdi_sign(invoice)
        + _pdi_quantity(item.quantity)
        + _pdi_cost_tail(item)
    )


def _pdi_header_line(invoice: Invoice) -> str:
    return (
        f"AMOUNT {_pdi_batch_number(invoice)}   {_pdi_date(invoice)}"
        f"{_pdi_sign(invoice)}{_pdi_amount_cents(invoice)}"
    )


def build_pdi_export(invoice: Invoice) -> str:
    """
    Deterministic PDI-compatible fixed-width export.

    Confirmed fields (verified against real PDI samples, or a confirmed
    business rule): record structure, item code, description, quantity,
    sign (return vs. normal invoice), batch number, unit/extended cost
    calculation. Still-open fields (exact cost digit-layout, trailer
    records): emitted as documented placeholders, never fabricated to
    look more certain than they are. See docs/PDI_OPEN_QUESTIONS.md for
    the full breakdown before relying on this for a live import.
    """
    lines = [_pdi_header_line(invoice)]
    lines.extend(_pdi_detail_line(item, invoice=invoice) for item in _sorted_items(invoice))
    lines.extend(_pdi_trailer_lines(invoice))
    return "\n".join(lines) + "\n"
