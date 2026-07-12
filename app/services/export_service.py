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

CSV_HEADERS = ["Description", "Quantity", "Unit Price", "Line Total", "Tax Rate (%)", "SKU/UPC"]


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
