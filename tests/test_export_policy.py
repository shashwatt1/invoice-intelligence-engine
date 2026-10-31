"""
Export policy for incomplete or unreconciled invoices — tests/test_export_policy.py

pdi_export_eligibility used to let a REVIEW_REQUIRED invoice export on a confirmation, whatever had failed; one
invoice with a third of its rows missing was exported that way. The critical checks (completeness and money
reconciliation) now block the export outright while they fail; any other review reason still leaves the
confirmation to a person. The block lifts by itself when the checks pass again (every correction revalidates).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.export_service import persisted_pdi_export_eligibility
from app.services.validation.report import (
    CRITICAL_CHECKS,
    CheckResult,
    CheckStatus,
    ConfidenceBreakdown,
    ProcessingDecision,
    ValidationReport,
)
from app.services.validation.service import ValidationService
from tests import synthetic_invoice as syn
from tests.test_extraction_reliability import extracted


def report(*checks):
    return ValidationReport(
        checks=list(checks), confidence=ConfidenceBreakdown(0.9, 0.9, 0.9, 1.0, {}),
        decision=ProcessingDecision.REVIEW_REQUIRED)


def failed(name):
    return CheckResult(name, CheckStatus.FAILED, message="x")


class TestWhichChecksBlock:
    def test_the_critical_set_is_the_completeness_and_reconciliation_checks(self):
        assert {
            "COMPLETENESS_MISSING_LINES", "COMPLETENESS_PARTIAL_CODES", "COMPLETENESS_DUPLICATE_LINES",
            "COMPLETENESS_SUMMARY_CASES", "COMPLETENESS_SUMMARY_UNITS", "COMPLETENESS_SECTION_TOTALS",
            "SUBTOTAL_MATCHES_ITEMS", "GRAND_TOTAL_MATH"} == CRITICAL_CHECKS

    def test_failed_critical_checks_are_named_sorted_and_deduplicated(self):
        names = report(failed("GRAND_TOTAL_MATH"), failed("COMPLETENESS_MISSING_LINES"),
                       failed("GRAND_TOTAL_MATH")).blocking_check_names()
        assert names == ["COMPLETENESS_MISSING_LINES", "GRAND_TOTAL_MATH"]

    def test_other_failures_and_warnings_do_not_block(self):
        r = report(failed("INVOICE_DATE_VALID"), CheckResult("SUBTOTAL_UNRECONCILED", CheckStatus.WARNING),
                   CheckResult("GRAND_TOTAL_MATH", CheckStatus.PASSED))
        assert r.blocking_check_names() is None

    def test_a_passed_or_skipped_critical_check_does_not_block(self):
        r = report(CheckResult("COMPLETENESS_MISSING_LINES", CheckStatus.PASSED),
                   CheckResult("COMPLETENESS_SUMMARY_UNITS", CheckStatus.SKIPPED))
        assert r.blocking_check_names() is None


class TestTheExportGate:
    def invoice(self, blockers):
        return SimpleNamespace(store_id=uuid.uuid4(), blocking_checks=blockers)

    def test_a_blocked_invoice_cannot_export_and_cannot_be_confirmed_past_it(self):
        gate = persisted_pdi_export_eligibility(self.invoice(["COMPLETENESS_MISSING_LINES"]), {})
        assert gate.allowed is False and gate.requires_confirmation is False
        assert "COMPLETENESS_MISSING_LINES" in gate.blocked_reason and "critical checks" in gate.blocked_reason

    def test_several_blockers_are_all_named(self):
        gate = persisted_pdi_export_eligibility(self.invoice(["COMPLETENESS_SECTION_TOTALS", "GRAND_TOTAL_MATH"]), {})
        assert "COMPLETENESS_SECTION_TOTALS" in gate.blocked_reason and "GRAND_TOTAL_MATH" in gate.blocked_reason

    def test_a_store_pending_invoice_keeps_its_own_reason_first(self):
        invoice = SimpleNamespace(store_id=None, blocking_checks=["GRAND_TOTAL_MATH"])
        assert "critical checks" not in persisted_pdi_export_eligibility(invoice, {}).blocked_reason

    @pytest.mark.parametrize("blockers", [None, []])
    def test_no_blockers_leaves_the_existing_rules_untouched(self, blockers, monkeypatch):
        sentinel = object()
        monkeypatch.setattr("app.services.export_service.pdi_export_eligibility", lambda inv, units: sentinel)
        assert persisted_pdi_export_eligibility(self.invoice(blockers), {}) is sentinel


class TestFromARealValidationRun:
    def run(self, rows):
        return ValidationService(review_threshold=0.80).validate_invoice(
            extracted(rows), ocr_confidence=0.97, ocr_text=syn.ocr_text()).report

    def test_a_complete_extraction_has_nothing_blocking(self):
        assert self.run(syn.benchmark_lines()).blocking_check_names() is None

    def test_an_incomplete_extraction_blocks_the_export(self):
        rows = syn.benchmark_lines()[:40]
        names = self.run(rows).blocking_check_names()
        assert names and "COMPLETENESS_MISSING_LINES" in names
        assert persisted_pdi_export_eligibility(
            SimpleNamespace(store_id=uuid.uuid4(), blocking_checks=names), {}).allowed is False
