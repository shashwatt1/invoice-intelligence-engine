"""
Invoice Normalization — app/services/validation/normalization.py

Pure functions converting the LLM-boundary ExtractedInvoice (floats, ISO
strings) into the canonical NormalizedInvoice (quantized Decimals, dates,
ISO 4217 currency), collecting normalization findings as CheckResults.

Design decisions:
- Decimal conversion goes through str() to avoid binary-float artifacts
  (Decimal(18.9) != Decimal("18.9")).
- Date parsing is strict ISO-8601. The extraction prompt mandates ISO
  output, so a non-ISO date is an extraction-quality signal that must
  surface as a failed check — not be guess-parsed into the wrong day.
- Currency accepts ISO codes case-insensitively and maps unambiguous
  symbols. Unknown values are kept (upper-cased) with a WARNING rather
  than discarded — reviewers see what the document actually said.
- Line-item reconciliation implements the core business rule:
    * qty, unit price, and total all present  → verify, never overwrite
    * unit price missing, qty & total present → derive total/qty (flagged)
    * total missing, qty & price present      → derive qty*price (flagged,
      WARNING — deriving a printed total is riskier than deriving a rate)
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.schemas.extraction import ExtractedInvoice, ExtractedLineItem
from app.schemas.normalized import NormalizedInvoice, NormalizedLineItem
from app.services.validation.report import CheckResult, CheckStatus

MONEY_EXP = Decimal("0.01")      # 2 dp — NUMERIC(14,2) columns
RATE_EXP = Decimal("0.0001")     # 4 dp — quantity / unit price columns

# Curated ISO 4217 codes. Enough for the MVP demo; extend as needed.
KNOWN_CURRENCIES = {
    "USD", "EUR", "GBP", "INR", "JPY", "CNY", "AUD", "CAD", "CHF", "SEK",
    "NOK", "DKK", "NZD", "SGD", "HKD", "AED", "SAR", "ZAR", "BRL", "MXN",
    "PLN", "CZK", "HUF", "TRY", "THB", "IDR", "MYR", "PHP", "VND", "KRW",
}

CURRENCY_SYMBOLS = {
    "$": "USD", "US$": "USD", "€": "EUR", "£": "GBP",
    "₹": "INR", "¥": "JPY", "A$": "AUD", "C$": "CAD",
}


def to_decimal(value: float | None, exp: Decimal = MONEY_EXP) -> Decimal | None:
    """Convert an extracted float to a quantized Decimal (None passes through)."""
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(exp, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def clean_text(value: str | None) -> str | None:
    """Strip whitespace; collapse empty strings to None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def parse_iso_date(value: str | None) -> date | None:
    """Parse a strict ISO-8601 (YYYY-MM-DD) date. None/invalid → None."""
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def normalize_currency(value: str | None) -> tuple[str | None, CheckResult]:
    """
    Normalize a currency to an upper-case ISO 4217 code.

    Returns (normalized_value, check). Missing or unrecognized currencies
    are WARNINGs, not failures — many documents omit an explicit code and
    persistence applies the platform default.
    """
    cleaned = clean_text(value)
    if cleaned is None:
        return None, CheckResult(
            name="CURRENCY_VALID",
            status=CheckStatus.WARNING,
            field="currency",
            message="No currency detected; the platform default will apply.",
        )

    code = CURRENCY_SYMBOLS.get(cleaned, cleaned.upper())
    if code in KNOWN_CURRENCIES:
        return code, CheckResult(
            name="CURRENCY_VALID", status=CheckStatus.PASSED, field="currency", actual=code
        )
    return code, CheckResult(
        name="CURRENCY_VALID",
        status=CheckStatus.WARNING,
        field="currency",
        message=f"Unrecognized currency '{cleaned}'; kept as-is for review.",
        actual=code,
    )


def normalize_line_item(
    item: ExtractedLineItem, index: int, checks: list[CheckResult]
) -> NormalizedLineItem:
    """Normalize one line item, applying the unit-price/line-total reconciliation rule."""
    quantity = to_decimal(item.quantity, RATE_EXP)
    unit_price = to_decimal(item.unit_price, RATE_EXP)
    line_total = to_decimal(item.line_total, MONEY_EXP)
    unit_price_derived = False
    line_total_derived = False
    prefix = f"line_items[{index}]"

    if unit_price is None and quantity not in (None, Decimal("0")) and line_total is not None:
        unit_price = (line_total / quantity).quantize(RATE_EXP, rounding=ROUND_HALF_UP)
        unit_price_derived = True
        checks.append(
            CheckResult(
                name="UNIT_PRICE_DERIVED",
                status=CheckStatus.PASSED,
                field=f"{prefix}.unit_price",
                message=(
                    f"Unit price not printed; derived as line_total / quantity = {unit_price}."
                ),
                actual=str(unit_price),
            )
        )
    elif line_total is None and quantity is not None and unit_price is not None:
        line_total = (quantity * unit_price).quantize(MONEY_EXP, rounding=ROUND_HALF_UP)
        line_total_derived = True
        checks.append(
            CheckResult(
                name="LINE_TOTAL_DERIVED",
                status=CheckStatus.WARNING,
                field=f"{prefix}.line_total",
                message=(
                    f"Line total not printed; derived as quantity × unit_price = {line_total}. "
                    "Derived totals should be verified in review."
                ),
                actual=str(line_total),
            )
        )

    return NormalizedLineItem(
        description=clean_text(item.description),
        quantity=quantity,
        unit_price=unit_price,
        line_total=line_total,
        tax_rate=to_decimal(item.tax_rate, RATE_EXP),
        confidence=item.confidence,
        sort_order=index,
        unit_price_derived=unit_price_derived,
        line_total_derived=line_total_derived,
    )


def normalize_invoice(
    extracted: ExtractedInvoice,
) -> tuple[NormalizedInvoice, list[CheckResult]]:
    """
    Normalize a full extracted invoice.

    Returns the canonical invoice plus the checks produced during
    normalization (date validity, currency, derivations).
    """
    checks: list[CheckResult] = []

    invoice_date = parse_iso_date(extracted.invoice_date)
    if extracted.invoice_date and invoice_date is None:
        checks.append(
            CheckResult(
                name="INVOICE_DATE_VALID",
                status=CheckStatus.FAILED,
                field="invoice_date",
                message="Invoice date is not a valid ISO-8601 date.",
                actual=extracted.invoice_date,
            )
        )

    due_date = parse_iso_date(extracted.due_date)
    if extracted.due_date and due_date is None:
        checks.append(
            CheckResult(
                name="DUE_DATE_VALID",
                status=CheckStatus.WARNING,
                field="due_date",
                message="Due date is not a valid ISO-8601 date; ignored.",
                actual=extracted.due_date,
            )
        )

    currency, currency_check = normalize_currency(extracted.currency)
    checks.append(currency_check)

    line_items = tuple(
        normalize_line_item(item, index, checks)
        for index, item in enumerate(extracted.line_items)
    )

    normalized = NormalizedInvoice(
        vendor_name=clean_text(extracted.vendor.name),
        vendor_tax_id=clean_text(extracted.vendor.tax_id),
        vendor_address=clean_text(extracted.vendor.address),
        vendor_phone=clean_text(extracted.vendor.phone),
        vendor_email=clean_text(extracted.vendor.email),
        invoice_number=clean_text(extracted.invoice_number),
        invoice_date=invoice_date,
        due_date=due_date,
        currency=currency,
        purchase_order=clean_text(extracted.purchase_order),
        payment_terms=clean_text(extracted.payment_terms),
        subtotal=to_decimal(extracted.subtotal),
        tax_amount=to_decimal(extracted.tax_amount),
        discount_amount=to_decimal(extracted.discount_amount),
        grand_total=to_decimal(extracted.grand_total),
        line_items=line_items,
        ai_confidence=extracted.confidence,
    )
    return normalized, checks
