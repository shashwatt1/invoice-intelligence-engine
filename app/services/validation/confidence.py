"""
Composite Confidence Scoring — app/services/validation/confidence.py

Combines three signals into the composite confidence stored on
invoices.composite_confidence and used for the review-routing decision:

    composite = 0.30 × OCR confidence          (extraction layer signal)
              + 0.40 × AI confidence           (model's self-assessment)
              + 0.30 × validation pass ratio   (deterministic checks)

Design decisions (deviation from requirements.md §8.3, explained):
- The original formula weighted LLM logprobs at 0.50. Structured Outputs
  does not expose usable per-field logprobs, so the AI signal is the
  model's schema-level self-reported confidence — a softer signal, so it
  is weighted below the original 0.50 and deterministic validation is
  weighted up.
- When a component's signal is unavailable (e.g. the model omitted its
  confidence), its weight is renormalized away instead of substituting a
  magic constant: a missing signal is not evidence of a bad extraction,
  and any invented default would bias every score that lacks it.
- The validation component uses PASSED / (PASSED + FAILED). Warnings and
  skipped checks are excluded: warnings are advisory, and skipped rules
  carry no information about correctness.
"""

from __future__ import annotations

from app.services.validation.report import (
    CheckResult,
    CheckStatus,
    ConfidenceBreakdown,
)

WEIGHT_OCR = 0.30
WEIGHT_AI = 0.40
WEIGHT_VALIDATION = 0.30


def validation_pass_ratio(checks: list[CheckResult]) -> float:
    """Ratio of passed to scoreable (passed+failed) checks. 0.0 if none ran."""
    passed = sum(1 for c in checks if c.status is CheckStatus.PASSED)
    failed = sum(1 for c in checks if c.status is CheckStatus.FAILED)
    scoreable = passed + failed
    return passed / scoreable if scoreable else 0.0


def compute_confidence(
    ocr_confidence: float | None,
    ai_confidence: float | None,
    checks: list[CheckResult],
) -> ConfidenceBreakdown:
    """
    Compute the composite confidence score.

    Args:
        ocr_confidence: OCRResult.mean_confidence (1.0 for digital PDFs);
            None if the extraction layer provided no signal.
        ai_confidence: The model's self-reported overall confidence; falls
            back to the caller's aggregation of line-item confidences.
        checks: All validation check results.

    Returns:
        ConfidenceBreakdown with the composite in [0, 1] and the effective
        (renormalized) weights actually applied.
    """
    validation_score = validation_pass_ratio(checks)

    components: list[tuple[str, float, float | None]] = [
        ("ocr", WEIGHT_OCR, ocr_confidence),
        ("ai", WEIGHT_AI, ai_confidence),
        ("validation", WEIGHT_VALIDATION, validation_score),
    ]
    available = [(name, weight, value) for name, weight, value in components if value is not None]
    total_weight = sum(weight for _, weight, _ in available)

    if total_weight == 0:
        composite = 0.0
        effective_weights = {name: 0.0 for name, _, _ in components}
    else:
        composite = sum(weight * _clamp(value) for _, weight, value in available) / total_weight
        effective_weights = {
            name: round(weight / total_weight, 4) for name, weight, _ in available
        }
        effective_weights.update(
            {name: 0.0 for name, _, value in components if value is None}
        )

    return ConfidenceBreakdown(
        composite=round(_clamp(composite), 4),
        ocr_confidence=ocr_confidence,
        ai_confidence=ai_confidence,
        validation_score=validation_score,
        weights=effective_weights,
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
