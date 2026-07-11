"""
Validation Service — app/services/validation/service.py

The quality gate between AI extraction and database persistence:

    ExtractedInvoice (+ OCR confidence)
        → normalize (canonical Decimals / dates / currency)
        → run validation rules
        → composite confidence score
        → VALIDATED / REVIEW_REQUIRED decision
        → ValidationResult { NormalizedInvoice, ValidationReport }

Design decisions:
- Synchronous: validation is pure CPU work with no I/O; a fake-async
  facade would only obscure that.
- Failed checks are report entries, never exceptions — a bad invoice is
  a normal input whose outcome is REVIEW_REQUIRED. The ValidationError
  domain exception is reserved for engine misuse.
- Decision rule (design.md manual-review triggers): VALIDATED requires
  zero FAILED checks AND composite confidence ≥ the configured threshold.
  Warnings never block. Missing grand total / zero line items are FAILED
  presence checks, so they route to review through the same rule.
- Tolerance and threshold come from Settings (overridable per instance
  for tests and future per-tenant configuration).
"""

from __future__ import annotations

import time
from decimal import Decimal

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.extraction import ExtractedInvoice
from app.services.validation.checks import (
    check_date_order,
    check_grand_total_math,
    check_line_item_math,
    check_missing_invoice_date,
    check_required_fields,
    check_subtotal,
    check_tax_consistency,
)
from app.services.validation.confidence import compute_confidence
from app.services.validation.normalization import normalize_invoice
from app.services.validation.report import (
    CheckStatus,
    ProcessingDecision,
    ValidationReport,
    ValidationResult,
)

logger = get_logger(__name__)


class ValidationService:
    """
    Facade over the validation stage.

    Usage:
        service = ValidationService()
        result = service.validate_invoice(extracted, ocr_confidence=0.93)
    """

    def __init__(
        self,
        rounding_tolerance: float | None = None,
        review_threshold: float | None = None,
    ) -> None:
        settings = get_settings()
        self._tolerance = Decimal(
            str(
                rounding_tolerance
                if rounding_tolerance is not None
                else settings.validation_rounding_tolerance
            )
        )
        self._threshold = (
            review_threshold
            if review_threshold is not None
            else settings.review_confidence_threshold
        )

    def validate_invoice(
        self,
        extracted: ExtractedInvoice,
        ocr_confidence: float | None = None,
        filename: str = "",
    ) -> ValidationResult:
        """
        Validate an extracted invoice and decide its processing route.

        Args:
            extracted: The structured invoice from the AI layer.
            ocr_confidence: OCRResult.mean_confidence from the extraction
                stage (1.0 for digital PDFs); None if unavailable.
            filename: Original filename (used for logging).

        Returns:
            ValidationResult with the canonical NormalizedInvoice and the
            full ValidationReport.
        """
        start = time.monotonic()

        normalized, checks = normalize_invoice(extracted)
        checks += check_required_fields(normalized)
        checks += check_missing_invoice_date(normalized, extracted.invoice_date)
        checks += check_line_item_math(normalized, self._tolerance)
        checks += check_subtotal(normalized, self._tolerance)
        checks += check_grand_total_math(normalized, self._tolerance)
        checks += check_tax_consistency(normalized, self._tolerance)
        checks += check_date_order(normalized)

        confidence = compute_confidence(
            ocr_confidence, self._aggregate_ai_confidence(extracted), checks
        )

        failed = [c for c in checks if c.status is CheckStatus.FAILED]
        review_reasons = [
            f"{c.name}: {c.message or 'check failed'}" for c in failed
        ]
        if confidence.composite < self._threshold:
            review_reasons.append(
                f"Composite confidence {confidence.composite:.4f} is below "
                f"the {self._threshold} review threshold."
            )

        decision = (
            ProcessingDecision.VALIDATED
            if not review_reasons
            else ProcessingDecision.REVIEW_REQUIRED
        )

        report = ValidationReport(
            checks=checks,
            confidence=confidence,
            decision=decision,
            review_reasons=review_reasons,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

        logger.info(
            "invoice_validation_complete",
            filename=filename,
            decision=decision.value,
            composite_confidence=confidence.composite,
            checks_passed=len(report.passed_checks),
            checks_failed=len(failed),
            warnings=len(report.warnings),
            duration_ms=report.duration_ms,
        )

        return ValidationResult(invoice=normalized, report=report)

    @staticmethod
    def _aggregate_ai_confidence(extracted: ExtractedInvoice) -> float | None:
        """
        The model's overall self-reported confidence; falls back to the
        mean of line-item confidences. None when the model reported nothing
        (the scorer then renormalizes the weights).
        """
        if extracted.confidence is not None:
            return extracted.confidence
        item_scores = [
            item.confidence for item in extracted.line_items if item.confidence is not None
        ]
        if item_scores:
            return sum(item_scores) / len(item_scores)
        return None
