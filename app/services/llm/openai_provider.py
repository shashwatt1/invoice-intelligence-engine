"""
OpenAI Provider — app/services/llm/openai_provider.py

Schema-enforced structured extraction via the OpenAI Chat Completions
parse API (native Structured Outputs, strict JSON schema mode).

Design decisions:
- Native Structured Outputs instead of the `instructor` library planned
  in design.md (which predates the feature): the model is constrained to
  emit schema-conformant JSON, so there is no retry-on-malformed-JSON
  loop to pay for. Provider portability lives in our LLMProvider
  interface, not in a third-party shim.
- Retries use the SDK's built-in exponential backoff with jitter, which
  honors Retry-After and retries only recoverable failures (429, 5xx,
  timeouts, connection errors). Configured from the existing
  openai_max_retries / openai_timeout_seconds settings — no second retry
  layer on top.
- Terminal SDK exceptions are mapped to the platform's domain exceptions
  so pipeline code never imports openai types.
- An httpx client can be injected for tests (same pattern as the Google
  Vision provider) — tests exercise the SDK's real parse path offline.
- API key comes from Settings only. Never logged, never echoed in errors.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.exceptions import (
    AIStructuringError,
    LLMAuthenticationError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from app.core.logging import get_logger
from app.services.llm.base import (
    LLMCallMetadata,
    LLMProvider,
    LLMStructuredResponse,
    SchemaT,
)

logger = get_logger(__name__)

PROVIDER_NAME = "openai"

# USD per 1M tokens: (input, output). Estimates for observability only —
# prices drift; update alongside model changes. Unknown models cost None.
MODEL_PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimate call cost from the pricing table. None for unknown models."""
    pricing = MODEL_PRICING_USD_PER_1M.get(model)
    if pricing is None:
        # Handle dated snapshots like "gpt-4o-mini-2024-07-18"
        pricing = next(
            (p for name, p in MODEL_PRICING_USD_PER_1M.items() if model.startswith(name + "-")),
            None,
        )
    if pricing is None:
        return None
    input_price, output_price = pricing
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


class OpenAIProvider(LLMProvider):
    """
    LLM provider backed by OpenAI Chat Completions with Structured Outputs.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        settings = get_settings()
        self._model = model or settings.openai_model

        if client is not None:
            self._client = client
            return

        key = api_key if api_key is not None else settings.openai_api_key
        if not key:
            raise ValueError(
                "OPENAI_API_KEY is not configured. "
                "Set it in the environment to use the openai LLM provider."
            )
        self._client = AsyncOpenAI(
            api_key=key,
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
    ) -> LLMStructuredResponse[SchemaT]:
        start = time.monotonic()
        logger.info(
            "openai_structured_call_started",
            model=self._model,
            schema=schema.__name__,
            user_prompt_chars=len(user_prompt),
        )

        try:
            completion = await self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=schema,
            )
        except openai.AuthenticationError as exc:
            logger.error("openai_auth_failed", status_code=exc.status_code)
            raise LLMAuthenticationError(detail={"status_code": exc.status_code}) from exc
        except openai.PermissionDeniedError as exc:
            logger.error("openai_permission_denied", status_code=exc.status_code)
            raise LLMAuthenticationError(detail={"status_code": exc.status_code}) from exc
        except openai.RateLimitError as exc:
            logger.warning("openai_rate_limited_after_retries")
            raise LLMRateLimitError(detail={"status_code": exc.status_code}) from exc
        except openai.APITimeoutError as exc:
            logger.warning("openai_timeout_after_retries")
            raise LLMTimeoutError() from exc
        except openai.APIConnectionError as exc:
            logger.warning("openai_connection_failed_after_retries", error=str(exc))
            raise LLMConnectionError() from exc
        except openai.LengthFinishReasonError as exc:
            logger.warning("openai_output_truncated", model=self._model)
            raise LLMResponseError(
                message="AI response was truncated before completing the invoice schema.",
                detail={"finish_reason": "length"},
            ) from exc
        except openai.ContentFilterFinishReasonError as exc:
            logger.warning("openai_content_filtered", model=self._model)
            raise LLMResponseError(
                message="AI response was blocked by the provider's content filter.",
                detail={"finish_reason": "content_filter"},
            ) from exc
        except openai.APIStatusError as exc:
            logger.error("openai_api_error", status_code=exc.status_code)
            raise AIStructuringError(
                message=f"OpenAI API returned HTTP {exc.status_code}.",
                detail={"status_code": exc.status_code},
            ) from exc
        except (ValidationError, json.JSONDecodeError) as exc:
            logger.error("openai_response_schema_invalid", error=str(exc))
            raise LLMResponseError(
                message="AI response did not conform to the invoice schema.",
                detail={"validation_error": str(exc)[:500]},
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("openai_transport_error", error=str(exc))
            raise LLMConnectionError() from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        choice = completion.choices[0]
        message = choice.message

        if message.refusal:
            logger.warning("openai_refusal", refusal=message.refusal[:200])
            raise LLMResponseError(
                message="The AI provider refused to process this document.",
                detail={"refusal": message.refusal},
            )
        if message.parsed is None:
            raise LLMResponseError(
                message="AI response contained no parsed content.",
                detail={"finish_reason": choice.finish_reason},
            )

        usage = completion.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        metadata = LLMCallMetadata(
            provider=PROVIDER_NAME,
            model=completion.model or self._model,
            request_id=completion.id,
            finish_reason=choice.finish_reason,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=usage.total_tokens if usage else 0,
            estimated_cost_usd=estimate_cost_usd(
                completion.model or self._model, input_tokens, output_tokens
            ),
        )

        logger.info(
            "openai_structured_call_complete",
            model=metadata.model,
            request_id=metadata.request_id,
            finish_reason=metadata.finish_reason,
            latency_ms=metadata.latency_ms,
            total_tokens=metadata.total_tokens,
            estimated_cost_usd=metadata.estimated_cost_usd,
        )

        return LLMStructuredResponse(
            parsed=message.parsed,
            raw_response=self._raw_response_dict(completion),
            metadata=metadata,
        )

    @staticmethod
    def _raw_response_dict(completion: Any) -> dict[str, Any]:
        """
        Full completion as a JSON-safe dict for storage/reprocessing.

        The parsed model is stripped — it duplicates the message content
        and Pydantic instances are not JSON-serializable in JSONB writes.
        """
        raw = completion.model_dump(mode="json", exclude={"choices"})
        raw["choices"] = [
            choice.model_dump(mode="json", exclude={"message"})
            | {"message": choice.message.model_dump(mode="json", exclude={"parsed"})}
            for choice in completion.choices
        ]
        return raw
