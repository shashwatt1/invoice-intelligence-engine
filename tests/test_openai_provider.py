"""
tests/test_openai_provider.py — OpenAI provider tests.

A mocked httpx transport is injected into a real AsyncOpenAI client, so
these tests exercise the SDK's actual Structured Outputs parse path —
including strict-schema generation from our Pydantic models — without
network access or a real API key.
"""

from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.core.exceptions import (
    AIStructuringError,
    LLMAuthenticationError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
)
from app.schemas.extraction import ExtractedInvoice
from app.services.llm.openai_provider import OpenAIProvider, estimate_cost_usd

# ---------------------------------------------------------------------------
# Canned OpenAI response builders
# ---------------------------------------------------------------------------

VALID_INVOICE_CONTENT = {
    "vendor": {
        "name": "Acme Corp",
        "tax_id": "GB123456",
        "address": "1 Acme Way, London",
        "phone": None,
        "email": None,
    },
    "invoice_number": "INV-001",
    "invoice_date": "2026-01-15",
    "due_date": "2026-02-14",
    "currency": "USD",
    "purchase_order": "PO-777",
    "payment_terms": "Net 30",
    "subtotal": 100.0,
    "tax_amount": 18.0,
    "discount_amount": None,
    "grand_total": 118.0,
    "line_items": [
        {
            "description": "Widget",
            "quantity": 2.0,
            "unit_price": 9.45,
            "line_total": 18.9,
            "tax_rate": 18.0,
            "confidence": 0.95,
        }
    ],
    "confidence": 0.9,
}


def completion_payload(
    content: dict | str | None,
    finish_reason: str = "stop",
    model: str = "gpt-4o-mini",
    refusal: str | None = None,
) -> dict:
    if isinstance(content, dict):
        content = json.dumps(content)
    return {
        "id": "chatcmpl-test-1",
        "object": "chat.completion",
        "created": 1767000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "logprobs": None,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500},
    }


def make_provider(payload: dict | Exception, status_code: int = 200, captured: list | None = None):
    """OpenAIProvider wired to a real SDK client with a mocked transport."""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(status_code, json=payload)

    client = AsyncOpenAI(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    return OpenAIProvider(client=client, model="gpt-4o-mini")


async def call(provider: OpenAIProvider):
    return await provider.generate_structured(
        system_prompt="You extract invoices.",
        user_prompt="<document>INVOICE ...</document>",
        schema=ExtractedInvoice,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestOpenAIProviderSuccess:
    async def test_parses_invoice_and_captures_metadata(self):
        captured: list[httpx.Request] = []
        provider = make_provider(completion_payload(VALID_INVOICE_CONTENT), captured=captured)

        result = await call(provider)

        invoice = result.parsed
        assert isinstance(invoice, ExtractedInvoice)
        assert invoice.vendor.name == "Acme Corp"
        assert invoice.invoice_number == "INV-001"
        assert invoice.line_items[0].unit_price == pytest.approx(9.45)

        meta = result.metadata
        assert meta.provider == "openai"
        assert meta.model == "gpt-4o-mini"
        assert meta.request_id == "chatcmpl-test-1"
        assert meta.finish_reason == "stop"
        assert meta.latency_ms >= 0
        assert (meta.input_tokens, meta.output_tokens, meta.total_tokens) == (1200, 300, 1500)
        assert meta.estimated_cost_usd == pytest.approx((1200 * 0.15 + 300 * 0.60) / 1e6)
        assert json.dumps(meta.to_dict())  # JSONB-safe for ProcessingLog

    async def test_sends_strict_structured_output_request(self):
        captured: list[httpx.Request] = []
        provider = make_provider(completion_payload(VALID_INVOICE_CONTENT), captured=captured)

        await call(provider)

        body = json.loads(captured[0].content)
        assert body["model"] == "gpt-4o-mini"
        assert [m["role"] for m in body["messages"]] == ["system", "user"]
        response_format = body["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        schema_props = response_format["json_schema"]["schema"]["properties"]
        assert "vendor" in schema_props and "line_items" in schema_props

    async def test_raw_response_is_json_safe_and_strips_parsed(self):
        provider = make_provider(completion_payload(VALID_INVOICE_CONTENT))

        result = await call(provider)

        assert json.dumps(result.raw_response)  # serializable for JSONB storage
        message = result.raw_response["choices"][0]["message"]
        assert "parsed" not in message
        assert json.loads(message["content"])["invoice_number"] == "INV-001"


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestOpenAIProviderErrors:
    async def test_401_maps_to_authentication_error(self):
        provider = make_provider({"error": {"message": "bad key"}}, status_code=401)
        with pytest.raises(LLMAuthenticationError):
            await call(provider)

    async def test_429_maps_to_rate_limit_error(self):
        provider = make_provider({"error": {"message": "slow down"}}, status_code=429)
        with pytest.raises(LLMRateLimitError):
            await call(provider)

    async def test_timeout_maps_to_timeout_error(self):
        provider = make_provider(httpx.ReadTimeout("timed out"))
        with pytest.raises(LLMTimeoutError):
            await call(provider)

    async def test_connection_failure_maps_to_connection_error(self):
        provider = make_provider(httpx.ConnectError("refused"))
        with pytest.raises(LLMConnectionError):
            await call(provider)

    async def test_500_maps_to_generic_structuring_error(self):
        provider = make_provider({"error": {"message": "boom"}}, status_code=500)
        with pytest.raises(AIStructuringError) as exc_info:
            await call(provider)
        assert type(exc_info.value) is AIStructuringError
        assert "500" in exc_info.value.message

    async def test_truncated_output_maps_to_response_error(self):
        provider = make_provider(
            completion_payload(VALID_INVOICE_CONTENT, finish_reason="length")
        )
        with pytest.raises(LLMResponseError) as exc_info:
            await call(provider)
        assert exc_info.value.detail == {"finish_reason": "length"}

    async def test_refusal_maps_to_response_error(self):
        provider = make_provider(
            completion_payload(None, refusal="I can't help with that.")
        )
        with pytest.raises(LLMResponseError) as exc_info:
            await call(provider)
        assert "refused" in exc_info.value.message

    async def test_malformed_json_maps_to_response_error(self):
        provider = make_provider(completion_payload("this is not json {{"))
        with pytest.raises(LLMResponseError):
            await call(provider)

    async def test_schema_invalid_json_maps_to_response_error(self):
        bad = dict(VALID_INVOICE_CONTENT, vendor="not-an-object")
        provider = make_provider(completion_payload(bad))
        with pytest.raises(LLMResponseError):
            await call(provider)


# ---------------------------------------------------------------------------
# Construction & cost estimation
# ---------------------------------------------------------------------------


class TestOpenAIProviderConstruction:
    def test_missing_api_key_fails_fast(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "")
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                OpenAIProvider()
        finally:
            get_settings.cache_clear()


class TestCostEstimation:
    def test_known_model(self):
        assert estimate_cost_usd("gpt-4o-mini", 1_000_000, 1_000_000) == pytest.approx(0.75)

    def test_dated_model_snapshot_matches_base_price(self):
        assert estimate_cost_usd("gpt-4o-mini-2024-07-18", 1_000_000, 0) == pytest.approx(0.15)

    def test_unknown_model_returns_none(self):
        assert estimate_cost_usd("some-future-model", 1000, 1000) is None
