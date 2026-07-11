"""
Validation Report Types — app/services/validation/report.py

The result contracts of the validation engine: individual check results,
the composite confidence breakdown, the processing decision, and the
full validation report.

Design decisions:
- Check failures are data, not exceptions. A bad invoice is a normal
  input whose outcome is REVIEW_REQUIRED; design.md mandates collecting
  all rule results rather than short-circuiting. Domain exceptions are
  reserved for engine misuse.
- ProcessingDecision is a str Enum so it compares/serializes as a plain
  string — it becomes the Document/Invoice status vocabulary used by
  persistence (Milestone D) and the frontend.
- to_dict() emits only JSON-native types so the report can be written
  verbatim into ProcessingLog.payload (JSONB) and returned by APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.schemas.normalized import NormalizedInvoice


class CheckStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"


class ProcessingDecision(StrEnum):
    """Terminal outcome of the validation quality gate."""

    VALIDATED = "VALIDATED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single validation rule."""

    name: str                      # e.g. "LINE_ITEM_MATH", "VENDOR_NAME_PRESENT"
    status: CheckStatus
    field: str | None = None       # e.g. "line_items[2].line_total"
    message: str = ""
    expected: str | None = None
    actual: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "status": self.status.value}
        for key in ("field", "message", "expected", "actual"):
            value = getattr(self, key)
            if value:
                data[key] = value
        return data


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """
    Composite confidence score with its inputs, reusable platform-wide.

    Components whose signal was unavailable carry weight 0 and the
    remaining weights are renormalized (see confidence.py).
    """

    composite: float
    ocr_confidence: float | None
    ai_confidence: float | None
    validation_score: float
    weights: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "composite": round(self.composite, 4),
            "ocr_confidence": self.ocr_confidence,
            "ai_confidence": self.ai_confidence,
            "validation_score": round(self.validation_score, 4),
            "weights": self.weights,
        }


@dataclass
class ValidationReport:
    """Structured result of one validation run."""

    checks: list[CheckResult]
    confidence: ConfidenceBreakdown
    decision: ProcessingDecision
    review_reasons: list[str] = field(default_factory=list)
    validated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = 0

    def _by_status(self, status: CheckStatus) -> list[CheckResult]:
        return [c for c in self.checks if c.status is status]

    @property
    def passed_checks(self) -> list[CheckResult]:
        return self._by_status(CheckStatus.PASSED)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return self._by_status(CheckStatus.FAILED)

    @property
    def warnings(self) -> list[CheckResult]:
        return self._by_status(CheckStatus.WARNING)

    @property
    def is_valid(self) -> bool:
        return self.decision is ProcessingDecision.VALIDATED

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialization for ProcessingLog.payload / API / frontend."""
        return {
            "decision": self.decision.value,
            "confidence": self.confidence.to_dict(),
            "review_reasons": list(self.review_reasons),
            "summary": {
                "passed": len(self.passed_checks),
                "failed": len(self.failed_checks),
                "warnings": len(self.warnings),
                "skipped": len(self._by_status(CheckStatus.SKIPPED)),
            },
            "checks": [c.to_dict() for c in self.checks],
            "validated_at": self.validated_at.isoformat(),
            "duration_ms": self.duration_ms,
        }


@dataclass
class ValidationResult:
    """
    Full output of the validation stage: the canonical invoice plus the
    report explaining how it was judged. Consumed by persistence (D).
    """

    invoice: NormalizedInvoice
    report: ValidationReport
