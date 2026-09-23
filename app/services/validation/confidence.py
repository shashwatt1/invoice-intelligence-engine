"""
Extraction Confidence — app/services/validation/confidence.py

How sure we are that OCR and the model read the document correctly:

    composite = OCR confidence (extraction layer signal)
              + AI confidence  (model's self-assessment)
    weighted 0.30 : 0.40 and renormalized over the signals present.

This is extraction confidence and nothing else. Validation is a separate
judgement (deterministic checks, decision, review reasons), review status
is whether a person still has to act, and EDI readiness is whether the
export prerequisites are met. The composite used to fold a 0.30 weight of
"validation pass ratio" in, so an unrelated failed check — a missing
invoice date, a total the model read from the wrong line — mechanically
pulled the extraction score down and read, in the UI, as proof that the
whole extraction was unreliable. It is not: the checks that failed say
exactly which field is wrong, and the review decision already carries
them. The pass ratio is still computed and reported (`validation_score`)
so the breakdown remains informative; it no longer moves the composite.

Design decisions:
- Structured Outputs does not expose usable per-field logprobs, so the AI
  signal is the model's schema-level self-reported confidence (with a
  fallback to the mean of line-item confidences upstream).
- When a signal is unavailable its weight is renormalized away instead of
  substituting a magic constant: a missing signal is not evidence of a
  bad extraction, and an invented default would bias every score.
- A genuinely unreliable extraction still routes to review: the composite
  is compared with the review threshold by the validation service, and a
  low OCR or model signal fails that comparison on its own.
"""

from __future__ import annotations

from app.services.validation.report import (
    CheckResult,
    CheckStatus,
    ConfidenceBreakdown,
)

WEIGHT_OCR = 0.30
WEIGHT_AI = 0.40
# Reported in the breakdown, never weighted into the composite.
WEIGHT_VALIDATION = 0.0


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
    Compute the extraction confidence score.

    Args:
        ocr_confidence: OCRResult.mean_confidence (1.0 for digital PDFs);
            None if the extraction layer provided no signal.
        ai_confidence: The model's self-reported overall confidence; falls
            back to the caller's aggregation of line-item confidences.
        checks: All validation check results — reported as
            `validation_score`, not weighted into the composite.

    Returns:
        ConfidenceBreakdown with the composite in [0, 1] and the effective
        (renormalized) weights actually applied; "validation" is always 0.
    """
    validation_score = validation_pass_ratio(checks)

    components: list[tuple[str, float, float | None]] = [
        ("ocr", WEIGHT_OCR, ocr_confidence),
        ("ai", WEIGHT_AI, ai_confidence),
        ("validation", WEIGHT_VALIDATION, validation_score),
    ]
    available = [
        (name, weight, value) for name, weight, value in components if value is not None and weight > 0
    ]
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
            {name: 0.0 for name, weight, value in components if value is None or weight == 0}
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
