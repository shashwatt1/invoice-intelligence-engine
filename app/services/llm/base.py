"""
LLM Provider Interface — app/services/llm/base.py

Defines the abstract LLM provider that all implementations must conform to,
plus the metadata contract captured for every call.

Design decisions:
- Mirrors the OCR layer (app/services/ocr/base.py): one ABC, one factory,
  concrete providers behind it. Business logic only sees LLMProvider.
- The interface is generic over the output schema (generate_structured
  takes any Pydantic model class), so future structuring tasks (vendor
  normalization, clarification prompts) reuse the same providers.
- LLMCallMetadata is a dataclass, not a Pydantic model — it never crosses
  an API boundary directly. to_dict() feeds ProcessingLog.payload (JSONB)
  and the future Developer Panel.
- Estimated cost lives here (provider-agnostic contract); each provider
  fills it from its own pricing table. Prices drift — costs are estimates
  for observability, never for billing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass
class LLMCallMetadata:
    """Observability record for a single LLM API call."""

    provider: str
    model: str
    request_id: str | None = None
    finish_reason: str | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialize for ProcessingLog.payload (JSONB) / Developer Panel."""
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        return data


@dataclass
class LLMStructuredResponse(Generic[SchemaT]):
    """
    Result of a schema-enforced LLM call.

    Attributes:
        parsed: The validated Pydantic model instance.
        raw_response: Full provider response as a JSON-safe dict, stored
            for debugging and reprocessing (invoices.raw_extraction_json).
        metadata: Observability record for this call.
    """

    parsed: SchemaT
    raw_response: dict[str, Any]
    metadata: LLMCallMetadata


class LLMProvider(ABC):
    """
    Abstract base class for all LLM providers.

    Implementing a new provider (Claude, Gemini, Azure OpenAI) requires
    only implementing `generate_structured()`. Callers never know which
    provider is active.
    """

    @abstractmethod
    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
    ) -> LLMStructuredResponse[SchemaT]:
        """
        Run a schema-enforced completion.

        Args:
            system_prompt: Behavioral instructions.
            user_prompt: Task input (document text).
            schema: Pydantic model class the response must conform to.

        Returns:
            LLMStructuredResponse with the parsed model, the raw provider
            response, and call metadata.

        Raises:
            LLMAuthenticationError: Credentials rejected.
            LLMRateLimitError: Rate limit persisted through retries.
            LLMTimeoutError: Request timed out after retries.
            LLMConnectionError: Provider unreachable after retries.
            LLMResponseError: Refusal, truncation, or schema-invalid output.
            AIStructuringError: Any other provider failure.
        """
