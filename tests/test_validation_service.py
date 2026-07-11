"""
tests/test_validation_service.py — Validation engine tests.

Covers normalization, every rule family, the reconciliation business
rule, confidence scoring, and VALIDATED / REVIEW_REQUIRED routing.
"""

from __future__ import annotations

import json
from datetime import UTC, date
from decimal import Decimal

import pytest

from app.schemas.extraction import ExtractedInvoice, ExtractedLineItem, ExtractedVendor
from app.services.validation import (
    CheckStatus,
    ProcessingDecision,
    ValidationService,
    compute_confidence,
)
from app.services.validation.report import CheckResult

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def item(**overrides) -> ExtractedLineItem:
    base = {
        "description": "Blue Widget",
        "quantity": 2.0,
        "unit_price": 9.45,
        "line_total": 18.9,
        "tax_rate": None,
        "confidence": 0.95,
    }
    base.update(overrides)
    return ExtractedLineItem(**base)


def invoice(**overrides) -> ExtractedInvoice:
    base = {
        "vendor": ExtractedVendor(name="Acme Corp", tax_id="GB123"),
        "invoice_number": "INV-001",
        "invoice_date": "2026-01-15",
        "due_date": "2026-02-14",
        "currency": "USD",
        "purchase_order": "PO-777",
        "payment_terms": "Net 30",
        "subtotal": 18.9,
        "tax_amount": None,
        "discount_amount": None,
        "grand_total": 18.9,
        "line_items": [item()],
        "confidence": 0.95,
    }
    base.update(overrides)
    return ExtractedInvoice(**base)


def validate(extracted: ExtractedInvoice, ocr_confidence: float | None = 1.0, **kwargs):
    return ValidationService(**kwargs).validate_invoice(extracted, ocr_confidence)


def checks_named(result, name: str) -> list:
    return [c for c in result.report.checks if c.name == name]


# ---------------------------------------------------------------------------
# Happy path & routing
# ---------------------------------------------------------------------------


class TestRouting:
    def test_clean_invoice_is_validated(self):
        result = validate(invoice())

        assert result.report.decision is ProcessingDecision.VALIDATED
        assert result.report.is_valid
        assert result.report.failed_checks == []
        assert result.report.confidence.composite >= 0.85
        assert result.report.review_reasons == []

    def test_math_failure_routes_to_review(self):
        result = validate(invoice(line_items=[item(unit_price=5.00)]))

        assert result.report.decision is ProcessingDecision.REVIEW_REQUIRED
        assert any("LINE_ITEM_MATH" in r for r in result.report.review_reasons)

    def test_low_ocr_confidence_routes_to_review_even_when_checks_pass(self):
        result = validate(invoice(), ocr_confidence=0.40)

        assert result.report.failed_checks == []
        assert result.report.decision is ProcessingDecision.REVIEW_REQUIRED
        assert any("below" in r for r in result.report.review_reasons)

    def test_warnings_do_not_block_validation(self):
        result = validate(invoice(currency=None))  # currency → WARNING

        assert result.report.decision is ProcessingDecision.VALIDATED
        assert len(result.report.warnings) >= 1


# ---------------------------------------------------------------------------
# The reconciliation business rule
# ---------------------------------------------------------------------------


class TestLineItemReconciliation:
    def test_derives_unit_price_when_missing(self):
        result = validate(invoice(line_items=[item(unit_price=None)]))

        [normalized_item] = result.invoice.line_items
        assert normalized_item.quantity == Decimal("2.0000")
        assert normalized_item.unit_price == Decimal("9.4500")
        assert normalized_item.line_total == Decimal("18.90")
        assert normalized_item.unit_price_derived is True
        assert checks_named(result, "UNIT_PRICE_DERIVED")[0].status is CheckStatus.PASSED
        assert result.report.decision is ProcessingDecision.VALIDATED

    def test_existing_unit_price_is_verified_not_overwritten(self):
        result = validate(invoice(line_items=[item(unit_price=5.00)]))

        [normalized_item] = result.invoice.line_items
        assert normalized_item.unit_price == Decimal("5.0000")  # kept, not "fixed"
        assert normalized_item.unit_price_derived is False
        [math_check] = checks_named(result, "LINE_ITEM_MATH")
        assert math_check.status is CheckStatus.FAILED
        assert math_check.expected == "10.00"
        assert math_check.actual == "18.90"

    def test_derives_line_total_as_warning(self):
        result = validate(
            invoice(line_items=[item(line_total=None)], subtotal=None, grand_total=18.9)
        )

        [normalized_item] = result.invoice.line_items
        assert normalized_item.line_total == Decimal("18.90")
        assert normalized_item.line_total_derived is True
        assert checks_named(result, "LINE_TOTAL_DERIVED")[0].status is CheckStatus.WARNING

    def test_incomplete_item_is_warning_not_failure(self):
        result = validate(invoice(line_items=[item(quantity=None, unit_price=None)]))

        [math_check] = checks_named(result, "LINE_ITEM_MATH")
        assert math_check.status is CheckStatus.WARNING


# ---------------------------------------------------------------------------
# Math checks & tolerance
# ---------------------------------------------------------------------------


class TestMathChecks:
    @pytest.mark.parametrize("line_total,expected_status", [
        (18.90, CheckStatus.PASSED),   # exact
        (18.92, CheckStatus.PASSED),   # within ±0.02
        (18.93, CheckStatus.FAILED),   # beyond tolerance
    ])
    def test_rounding_tolerance(self, line_total, expected_status):
        result = validate(
            invoice(line_items=[item(line_total=line_total)], subtotal=line_total,
                    grand_total=line_total)
        )
        [math_check] = checks_named(result, "LINE_ITEM_MATH")
        assert math_check.status is expected_status

    def test_tolerance_is_configurable(self):
        result = validate(
            invoice(line_items=[item(line_total=18.99)], subtotal=18.99, grand_total=18.99),
            rounding_tolerance=0.10,
        )
        assert checks_named(result, "LINE_ITEM_MATH")[0].status is CheckStatus.PASSED

    def test_subtotal_mismatch_fails(self):
        result = validate(invoice(subtotal=99.00, grand_total=99.00))
        [check] = checks_named(result, "SUBTOTAL_MATCHES_ITEMS")
        assert check.status is CheckStatus.FAILED
        assert check.expected == "18.90"

    def test_grand_total_math_with_tax_and_discount(self):
        result = validate(
            invoice(subtotal=18.9, tax_amount=3.78, discount_amount=1.00, grand_total=21.68)
        )
        assert checks_named(result, "GRAND_TOTAL_MATH")[0].status is CheckStatus.PASSED

    def test_grand_total_mismatch_fails(self):
        result = validate(invoice(subtotal=18.9, tax_amount=3.78, grand_total=99.99))
        [check] = checks_named(result, "GRAND_TOTAL_MATH")
        assert check.status is CheckStatus.FAILED
        assert check.expected == "22.68"

    def test_tax_consistency_uniform_rate(self):
        good = validate(
            invoice(line_items=[item(tax_rate=18.0)], subtotal=18.9,
                    tax_amount=3.40, grand_total=22.30)
        )
        assert checks_named(good, "TAX_CONSISTENT")[0].status is CheckStatus.PASSED

        bad = validate(
            invoice(line_items=[item(tax_rate=18.0)], subtotal=18.9,
                    tax_amount=9.99, grand_total=28.89)
        )
        assert checks_named(bad, "TAX_CONSISTENT")[0].status is CheckStatus.FAILED

    def test_tax_check_skipped_without_rates(self):
        result = validate(invoice(tax_amount=3.78, grand_total=22.68))
        assert checks_named(result, "TAX_CONSISTENT")[0].status is CheckStatus.SKIPPED


# ---------------------------------------------------------------------------
# Presence & date checks
# ---------------------------------------------------------------------------


class TestPresenceAndDates:
    @pytest.mark.parametrize("missing_field,check_name", [
        ({"vendor": ExtractedVendor(name=None)}, "VENDOR_NAME_PRESENT"),
        ({"invoice_number": None}, "INVOICE_NUMBER_PRESENT"),
        ({"grand_total": None}, "GRAND_TOTAL_PRESENT"),
        ({"invoice_date": None}, "INVOICE_DATE_VALID"),
        ({"line_items": []}, "LINE_ITEMS_PRESENT"),
    ])
    def test_missing_required_field_fails_and_routes_to_review(self, missing_field, check_name):
        result = validate(invoice(**missing_field))

        failed_names = {c.name for c in result.report.failed_checks}
        assert check_name in failed_names
        assert result.report.decision is ProcessingDecision.REVIEW_REQUIRED

    def test_purchase_order_checked_only_when_available(self):
        with_po = validate(invoice())
        assert checks_named(with_po, "PURCHASE_ORDER_PRESENT")[0].status is CheckStatus.PASSED

        without_po = validate(invoice(purchase_order=None))
        assert checks_named(without_po, "PURCHASE_ORDER_PRESENT")[0].status is CheckStatus.SKIPPED
        assert without_po.report.decision is ProcessingDecision.VALIDATED

    def test_invalid_date_format_fails(self):
        result = validate(invoice(invoice_date="15/01/2026"))

        [check] = checks_named(result, "INVOICE_DATE_VALID")
        assert check.status is CheckStatus.FAILED
        assert check.actual == "15/01/2026"
        assert result.invoice.invoice_date is None

    def test_due_date_before_invoice_date_fails(self):
        result = validate(invoice(due_date="2026-01-01"))
        assert checks_named(result, "DATE_ORDER")[0].status is CheckStatus.FAILED

    def test_dates_normalized_to_date_objects(self):
        result = validate(invoice())
        assert result.invoice.invoice_date == date(2026, 1, 15)
        assert result.invoice.due_date == date(2026, 2, 14)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_money_quantized_to_decimals(self):
        result = validate(invoice(subtotal=18.9, grand_total=18.9))
        assert result.invoice.subtotal == Decimal("18.90")
        assert str(result.invoice.subtotal) == "18.90"  # 2 dp, not 18.9

    @pytest.mark.parametrize("raw,expected", [
        ("usd", "USD"),
        (" EUR ", "EUR"),
        ("€", "EUR"),
        ("$", "USD"),
        ("₹", "INR"),
    ])
    def test_currency_normalization(self, raw, expected):
        result = validate(invoice(currency=raw))
        assert result.invoice.currency == expected
        assert checks_named(result, "CURRENCY_VALID")[0].status is CheckStatus.PASSED

    def test_unknown_currency_kept_with_warning(self):
        result = validate(invoice(currency="ZZZ"))
        assert result.invoice.currency == "ZZZ"
        assert checks_named(result, "CURRENCY_VALID")[0].status is CheckStatus.WARNING

    def test_whitespace_fields_collapse_to_none(self):
        result = validate(invoice(invoice_number="   ", purchase_order="  "))
        assert result.invoice.invoice_number is None
        assert "INVOICE_NUMBER_PRESENT" in {c.name for c in result.report.failed_checks}


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def _checks(passed: int, failed: int) -> list[CheckResult]:
    return [CheckResult(name=f"P{i}", status=CheckStatus.PASSED) for i in range(passed)] + [
        CheckResult(name=f"F{i}", status=CheckStatus.FAILED) for i in range(failed)
    ]


class TestConfidenceScoring:
    def test_weighted_formula(self):
        breakdown = compute_confidence(0.9, 0.8, _checks(passed=3, failed=1))
        expected = 0.30 * 0.9 + 0.40 * 0.8 + 0.30 * 0.75
        assert breakdown.composite == pytest.approx(expected, abs=1e-4)
        assert breakdown.weights == {"ocr": 0.3, "ai": 0.4, "validation": 0.3}

    def test_missing_ai_signal_renormalizes_weights(self):
        breakdown = compute_confidence(1.0, None, _checks(passed=4, failed=0))
        # (0.30×1.0 + 0.30×1.0) / 0.60 — a perfect invoice isn't penalized
        assert breakdown.composite == pytest.approx(1.0)
        assert breakdown.weights["ai"] == 0.0
        assert breakdown.weights["ocr"] == pytest.approx(0.5)

    def test_failed_checks_drag_score_down(self):
        clean = compute_confidence(1.0, 0.95, _checks(passed=8, failed=0))
        dirty = compute_confidence(1.0, 0.95, _checks(passed=4, failed=4))
        assert dirty.composite < clean.composite
        assert dirty.validation_score == pytest.approx(0.5)

    def test_no_signals_scores_zero(self):
        assert compute_confidence(None, None, []).composite == 0.0

    def test_ai_confidence_falls_back_to_item_mean(self):
        result = validate(
            invoice(confidence=None,
                    line_items=[item(confidence=0.6), item(description="B", quantity=1.0,
                                                           unit_price=5.0, line_total=5.0,
                                                           confidence=0.8)],
                    subtotal=23.9, grand_total=23.9)
        )
        assert result.report.confidence.ai_confidence == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------


class TestReport:
    def test_report_serializes_for_processing_log(self):
        report = validate(invoice(line_items=[item(unit_price=5.0)])).report

        payload = report.to_dict()
        assert json.dumps(payload)  # JSONB-safe
        assert payload["decision"] == "REVIEW_REQUIRED"
        assert payload["summary"]["failed"] >= 1
        assert payload["confidence"]["composite"] == report.confidence.composite
        assert any(c["name"] == "LINE_ITEM_MATH" for c in payload["checks"])

    def test_report_has_timestamp_and_duration(self):
        report = validate(invoice()).report
        assert report.validated_at.tzinfo is UTC
        assert report.duration_ms >= 0

    def test_decision_values_are_plain_strings(self):
        assert ProcessingDecision.VALIDATED == "VALIDATED"
        assert ProcessingDecision.REVIEW_REQUIRED == "REVIEW_REQUIRED"
