"""
Benchmark acceptance, end to end — tests/test_extraction_reliability.py

A synthetic copy of the benchmark's shape (tests/synthetic_invoice.py) is pushed through the real validation
service with and without rows missing. What must hold:

  * the complete extraction is VALIDATED with completeness COMPLETE and balances: 61 lines, 68 cases, 958 units,
    1,821.18 merchandise + 0.00 tax + 23.30 state/local charges = 1,844.48;
  * the same extraction with rows missing is REVIEW_REQUIRED and INCOMPLETE although every remaining row still
    passes its own arithmetic, and its confidence can never read high;
  * units per case is right for every product (printed units ÷ cases agrees with the pack text).
"""

from __future__ import annotations

import pytest

from app.schemas.extraction import (
    ExtractedCharge,
    ExtractedInvoice,
    ExtractedLineItem,
    ExtractedVendor,
)
from app.services.completeness import (
    INCOMPLETE_CONFIDENCE_CAP,
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_UNVERIFIED,
)
from app.services.export_service import units_evidence
from app.services.validation.report import CheckStatus, ProcessingDecision
from app.services.validation.service import ValidationService
from tests import synthetic_invoice as syn


def extracted(rows, *, charges=True, subtotal=None) -> ExtractedInvoice:
    return ExtractedInvoice(
        vendor=ExtractedVendor(name="SYN BEVERAGE CO"), invoice_number="10000001", invoice_date="2026-10-22",
        currency="USD", subtotal=float(subtotal if subtotal is not None else syn.SUBTOTAL), tax_amount=0.0,
        grand_total=float(syn.AMOUNT_DUE), confidence=0.93,
        other_charges=[ExtractedCharge(kind="state_container_deposit", label="NY St Deposit @ .05",
                                       amount=float(syn.CHARGES))] if charges else [],
        line_items=[ExtractedLineItem(
            description=r.description, product_code=r.product_code, pack_size=r.pack_size, quantity=float(r.quantity),
            unit_price=float(r.unit_price), line_total=float(r.line_total), printed_units=r.printed_units,
            section_label=r.section_label, confidence=0.95, source_pages=[1]) for r in rows])


def validate(rows, ocr_text, **kw):
    return ValidationService(review_threshold=0.80).validate_invoice(
        extracted(rows, **kw), ocr_confidence=0.97, filename="synthetic.jpg", ocr_text=ocr_text)


def statuses(result, name):
    return [c.status for c in result.report.checks if c.name == name]


class TestTheCompleteBenchmark:
    def test_it_validates_and_every_acceptance_figure_holds(self):
        rows = syn.benchmark_lines()
        result = validate(rows, syn.ocr_text())
        invoice = result.invoice
        assert result.report.decision is ProcessingDecision.VALIDATED, result.report.review_reasons
        assert result.report.completeness["status"] == STATUS_COMPLETE
        assert len(invoice.line_items) == 61
        assert sum(int(i.quantity) for i in invoice.line_items) == 68
        assert sum(i.printed_units for i in invoice.line_items) == 958
        assert (invoice.subtotal, invoice.tax_amount, invoice.other_charges_total, invoice.grand_total) == (
            syn.SUBTOTAL, 0, syn.CHARGES, syn.AMOUNT_DUE)
        assert statuses(result, "GRAND_TOTAL_MATH") == [CheckStatus.PASSED]
        assert statuses(result, "SUBTOTAL_MATCHES_ITEMS") == [CheckStatus.PASSED]
        assert not [c for c in result.report.checks if c.status is CheckStatus.FAILED]

    def test_a_bottom_first_upload_that_extracted_everything_is_still_complete(self):
        assert validate(syn.benchmark_lines(), syn.ocr_text([4, 3, 2, 1])).report.completeness["status"] == STATUS_COMPLETE

    def test_without_the_typed_charge_it_cannot_balance(self):
        result = validate(syn.benchmark_lines(), syn.ocr_text(), charges=False)
        assert statuses(result, "GRAND_TOTAL_MATH") == [CheckStatus.FAILED]
        assert result.report.decision is ProcessingDecision.REVIEW_REQUIRED

    def test_units_per_case_is_right_for_every_product(self):
        for row in syn.benchmark_lines():
            per_case = next(n for h, n, _ in syn.SECTIONS if h == row.section_label)
            ev = units_evidence(row.pack_size, row.description, cases=row.quantity, printed_units=row.printed_units)
            assert (ev.units, ev.conflict) == (per_case, False), row.description


class TestAnIncompleteExtraction:
    def forty(self):
        return [r for n, r in enumerate(syn.benchmark_lines()) if n % 3 != 2][:40]

    def test_forty_of_sixty_one_lines_is_review_required_and_incomplete(self):
        rows = self.forty()
        subtotal = sum(r.line_total for r in rows)
        result = validate(rows, syn.ocr_text(), subtotal=subtotal)       # the printed subtotal matches the 40 lines
        assert result.report.decision is ProcessingDecision.REVIEW_REQUIRED
        assert result.report.completeness["status"] == STATUS_INCOMPLETE
        assert len(result.report.completeness["missing_codes"]) == 21

    def test_every_remaining_row_still_passes_its_own_arithmetic(self):
        result = validate(self.forty(), syn.ocr_text())
        assert statuses(result, "LINE_ITEM_MATH") and set(statuses(result, "LINE_ITEM_MATH")) == {CheckStatus.PASSED}
        assert result.report.completeness["status"] == STATUS_INCOMPLETE        # passing rows are not a complete invoice

    def test_a_high_score_never_stands_for_an_incomplete_invoice(self):
        result = validate(self.forty(), syn.ocr_text())
        confidence = result.report.confidence
        assert confidence.composite <= INCOMPLETE_CONFIDENCE_CAP
        assert confidence.composite_uncapped is not None and confidence.composite_uncapped > 0.85
        assert result.report.to_dict()["confidence"]["completeness"] == STATUS_INCOMPLETE
        assert result.report.to_dict()["confidence"]["composite"] <= INCOMPLETE_CONFIDENCE_CAP

    def test_one_missing_row_out_of_sixty_one_is_enough(self):
        rows = syn.benchmark_lines()
        rows.pop(40)
        result = validate(rows, syn.ocr_text())
        assert result.report.decision is ProcessingDecision.REVIEW_REQUIRED
        assert result.report.completeness["status"] == STATUS_INCOMPLETE


class TestNoOcrText:
    def test_without_the_ocr_text_completeness_is_unverified_not_complete(self):
        result = validate(syn.benchmark_lines(), None)
        assert result.report.completeness["status"] == STATUS_UNVERIFIED
        assert result.report.confidence.completeness == STATUS_UNVERIFIED
        assert result.report.confidence.composite_uncapped is None


@pytest.mark.parametrize("pack, units, cases, expected", [
    ("20OZ PL 1/24S", 24, 1, 24), ("28OZ PL 1/15", 30, 2, 15), ("1L PL 1/15S", 15, 1, 15),
    ("2L PL 1/8S", 16, 2, 8), ("16OZCN1/12SLCEL", 12, 1, 12), ("11OZ PL 1/12 TBG", 12, 1, 12),
])
def test_the_acceptance_table_for_units_per_case(pack, units, cases, expected):
    assert units_evidence(pack, None, cases=cases, printed_units=units).units == expected
