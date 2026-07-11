"""
Validation Rules — app/services/validation/checks.py

Individual validation rules over a NormalizedInvoice. Each rule returns
CheckResults; nothing raises on bad data (design.md: collect all results,
never short-circuit).

Rule inventory:
    Presence:  VENDOR_NAME_PRESENT, INVOICE_NUMBER_PRESENT,
               INVOICE_DATE_VALID, GRAND_TOTAL_PRESENT,
               LINE_ITEMS_PRESENT, PURCHASE_ORDER_PRESENT (optional field)
    Math:      LINE_ITEM_MATH (per item), SUBTOTAL_MATCHES_ITEMS,
               GRAND_TOTAL_MATH, TAX_CONSISTENT
    Dates:     DATE_ORDER (due >= invoice)
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.normalized import NormalizedInvoice
from app.services.validation.report import CheckResult, CheckStatus


def _within(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


# ---------------------------------------------------------------------------
# Presence checks
# ---------------------------------------------------------------------------


def check_required_fields(invoice: NormalizedInvoice) -> list[CheckResult]:
    """Presence of the fields an invoice cannot be processed without."""
    checks: list[CheckResult] = []

    required = [
        ("VENDOR_NAME_PRESENT", "vendor_name", invoice.vendor_name, "Vendor name"),
        ("INVOICE_NUMBER_PRESENT", "invoice_number", invoice.invoice_number, "Invoice number"),
        ("GRAND_TOTAL_PRESENT", "grand_total", invoice.grand_total, "Grand total"),
    ]
    for name, field, value, label in required:
        if value is None:
            checks.append(
                CheckResult(
                    name=name,
                    status=CheckStatus.FAILED,
                    field=field,
                    message=f"{label} was not found on the document.",
                )
            )
        else:
            checks.append(CheckResult(name=name, status=CheckStatus.PASSED, field=field))

    # Invoice date presence. Format failures are reported by normalization
    # under the same name, so only report here when nothing was extracted.
    if invoice.invoice_date is not None:
        checks.append(
            CheckResult(name="INVOICE_DATE_VALID", status=CheckStatus.PASSED, field="invoice_date")
        )

    checks.append(
        CheckResult(
            name="LINE_ITEMS_PRESENT",
            status=CheckStatus.PASSED if invoice.line_items else CheckStatus.FAILED,
            field="line_items",
            message="" if invoice.line_items else "No line items were extracted.",
        )
    )

    # Purchase order is validated only when the document carries one.
    checks.append(
        CheckResult(
            name="PURCHASE_ORDER_PRESENT",
            status=CheckStatus.PASSED if invoice.purchase_order else CheckStatus.SKIPPED,
            field="purchase_order",
            message="" if invoice.purchase_order else "No purchase order on document.",
        )
    )
    return checks


def check_missing_invoice_date(
    invoice: NormalizedInvoice, raw_invoice_date: str | None
) -> list[CheckResult]:
    """FAILED when no invoice date was extracted at all (format errors are handled upstream)."""
    if invoice.invoice_date is None and not raw_invoice_date:
        return [
            CheckResult(
                name="INVOICE_DATE_VALID",
                status=CheckStatus.FAILED,
                field="invoice_date",
                message="Invoice date was not found on the document.",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Math checks
# ---------------------------------------------------------------------------


def check_line_item_math(invoice: NormalizedInvoice, tolerance: Decimal) -> list[CheckResult]:
    """
    Verify quantity × unit_price ≈ line_total for every item where all
    three values are known. Derived values were reconciled by construction
    (normalization), so verification here targets printed values.
    """
    checks: list[CheckResult] = []
    for item in invoice.line_items:
        prefix = f"line_items[{item.sort_order}]"
        if None in (item.quantity, item.unit_price, item.line_total):
            checks.append(
                CheckResult(
                    name="LINE_ITEM_MATH",
                    status=CheckStatus.WARNING,
                    field=prefix,
                    message="Item is missing quantity, unit price, or line total; math not verifiable.",
                )
            )
            continue
        expected = (item.quantity * item.unit_price).quantize(Decimal("0.01"))
        if _within(expected, item.line_total, tolerance):
            checks.append(
                CheckResult(name="LINE_ITEM_MATH", status=CheckStatus.PASSED, field=prefix)
            )
        else:
            checks.append(
                CheckResult(
                    name="LINE_ITEM_MATH",
                    status=CheckStatus.FAILED,
                    field=f"{prefix}.line_total",
                    message="quantity × unit_price does not match the printed line total.",
                    expected=str(expected),
                    actual=str(item.line_total),
                )
            )
    return checks


def check_subtotal(invoice: NormalizedInvoice, tolerance: Decimal) -> list[CheckResult]:
    """Subtotal ≈ Σ line totals (skipped when either side is unavailable)."""
    line_totals = [i.line_total for i in invoice.line_items if i.line_total is not None]
    if invoice.subtotal is None or not line_totals:
        return [
            CheckResult(
                name="SUBTOTAL_MATCHES_ITEMS",
                status=CheckStatus.SKIPPED,
                field="subtotal",
                message="Subtotal or line totals unavailable.",
            )
        ]
    computed = sum(line_totals, Decimal("0.00"))
    if _within(computed, invoice.subtotal, tolerance):
        return [CheckResult(name="SUBTOTAL_MATCHES_ITEMS", status=CheckStatus.PASSED, field="subtotal")]
    return [
        CheckResult(
            name="SUBTOTAL_MATCHES_ITEMS",
            status=CheckStatus.FAILED,
            field="subtotal",
            message="Sum of line totals does not match the printed subtotal.",
            expected=str(computed),
            actual=str(invoice.subtotal),
        )
    ]


def check_grand_total_math(invoice: NormalizedInvoice, tolerance: Decimal) -> list[CheckResult]:
    """
    grand_total ≈ subtotal + tax − discount. Falls back to Σ line totals
    when no subtotal is printed. Skipped when components are unavailable.
    """
    if invoice.grand_total is None:
        return [
            CheckResult(
                name="GRAND_TOTAL_MATH",
                status=CheckStatus.SKIPPED,
                field="grand_total",
                message="No grand total to verify.",
            )
        ]
    base = invoice.subtotal
    if base is None:
        line_totals = [i.line_total for i in invoice.line_items if i.line_total is not None]
        base = sum(line_totals, Decimal("0.00")) if line_totals else None
    if base is None:
        return [
            CheckResult(
                name="GRAND_TOTAL_MATH",
                status=CheckStatus.SKIPPED,
                field="grand_total",
                message="No subtotal or line totals available to verify against.",
            )
        ]
    computed = base + (invoice.tax_amount or Decimal("0")) - (invoice.discount_amount or Decimal("0"))
    if _within(computed, invoice.grand_total, tolerance):
        return [CheckResult(name="GRAND_TOTAL_MATH", status=CheckStatus.PASSED, field="grand_total")]
    return [
        CheckResult(
            name="GRAND_TOTAL_MATH",
            status=CheckStatus.FAILED,
            field="grand_total",
            message="subtotal + tax − discount does not match the printed grand total.",
            expected=str(computed),
            actual=str(invoice.grand_total),
        )
    ]


def check_tax_consistency(invoice: NormalizedInvoice, tolerance: Decimal) -> list[CheckResult]:
    """
    When every line item carries the same tax rate, verify
    tax_amount ≈ subtotal × rate / 100 (design.md tax validation).
    Skipped when rates are absent, mixed, or components are missing.
    """
    rates = {i.tax_rate for i in invoice.line_items if i.tax_rate is not None}
    if (
        len(rates) != 1
        or invoice.subtotal is None
        or invoice.tax_amount is None
    ):
        return [
            CheckResult(
                name="TAX_CONSISTENT",
                status=CheckStatus.SKIPPED,
                field="tax_amount",
                message="Uniform tax rate, subtotal, or tax amount unavailable.",
            )
        ]
    (rate,) = rates
    expected = (invoice.subtotal * rate / Decimal("100")).quantize(Decimal("0.01"))
    if _within(expected, invoice.tax_amount, tolerance):
        return [CheckResult(name="TAX_CONSISTENT", status=CheckStatus.PASSED, field="tax_amount")]
    return [
        CheckResult(
            name="TAX_CONSISTENT",
            status=CheckStatus.FAILED,
            field="tax_amount",
            message=f"Tax amount is inconsistent with a uniform {rate}% rate on the subtotal.",
            expected=str(expected),
            actual=str(invoice.tax_amount),
        )
    ]


# ---------------------------------------------------------------------------
# Date checks
# ---------------------------------------------------------------------------


def check_date_order(invoice: NormalizedInvoice) -> list[CheckResult]:
    """Due date must not precede the invoice date."""
    if invoice.invoice_date is None or invoice.due_date is None:
        return []
    if invoice.due_date >= invoice.invoice_date:
        return [CheckResult(name="DATE_ORDER", status=CheckStatus.PASSED, field="due_date")]
    return [
        CheckResult(
            name="DATE_ORDER",
            status=CheckStatus.FAILED,
            field="due_date",
            message="Due date is earlier than the invoice date.",
            expected=f">= {invoice.invoice_date.isoformat()}",
            actual=invoice.due_date.isoformat(),
        )
    ]
