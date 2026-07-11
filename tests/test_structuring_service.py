"""
tests/test_structuring_service.py — Structuring orchestration, prompt
registry, and LLM factory tests. No network access.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.prompts.invoice_extraction import ACTIVE_VERSION, get_prompt
from app.schemas.extraction import ExtractedInvoice, ExtractedVendor
from app.services.llm.base import LLMCallMetadata, LLMProvider, LLMStructuredResponse
from app.services.llm.factory import get_llm_provider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.ocr.base import OCRResult
from app.services.structuring_service import StructuringService


class FakeLLMProvider(LLMProvider):
    """Records prompts and returns a canned structured response."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_structured(self, *, system_prompt, user_prompt, schema):
        self.calls.append({"system": system_prompt, "user": user_prompt, "schema": schema})
        return LLMStructuredResponse(
            parsed=ExtractedInvoice(
                vendor=ExtractedVendor(name="Acme Corp"),
                invoice_number="INV-42",
                line_items=[],
            ),
            raw_response={"id": "chatcmpl-fake"},
            metadata=LLMCallMetadata(provider="fake", model="fake-model", total_tokens=10),
        )


def ocr_result(text: str = "INVOICE INV-42 from Acme Corp") -> OCRResult:
    return OCRResult(full_text=text, source_type="digital_pdf", page_count=1)


class TestStructuringService:
    async def test_composes_result_with_prompt_version_and_metadata(self):
        fake = FakeLLMProvider()
        service = StructuringService(llm_provider=fake)

        result = await service.structure_invoice(ocr_result(), "inv.pdf")

        assert result.invoice.invoice_number == "INV-42"
        assert result.invoice.vendor.name == "Acme Corp"
        assert result.prompt_version == ACTIVE_VERSION
        assert result.metadata.model == "fake-model"
        assert result.raw_response == {"id": "chatcmpl-fake"}
        assert result.ocr_text_truncated is False

    async def test_sends_versioned_prompts_and_schema(self):
        fake = FakeLLMProvider()
        service = StructuringService(llm_provider=fake)

        await service.structure_invoice(ocr_result("Total due: 118.00"), "inv.pdf")

        [record] = fake.calls
        assert record["system"] == get_prompt().system_prompt
        assert "Total due: 118.00" in record["user"]
        assert "digital_pdf" in record["user"]  # source type is surfaced to the model
        assert record["schema"] is ExtractedInvoice

    async def test_truncates_text_over_token_budget(self):
        fake = FakeLLMProvider()
        service = StructuringService(llm_provider=fake)
        budget_chars = get_settings().openai_max_tokens_per_document * 4
        long_text = "x" * (budget_chars * 2)

        result = await service.structure_invoice(ocr_result(long_text), "big.pdf")

        assert result.ocr_text_truncated is True
        [record] = fake.calls
        assert "truncated" in record["user"]
        # The document portion respects the budget (prompt adds small framing)
        assert len(record["user"]) < budget_chars + 500

    async def test_short_text_is_never_truncated(self):
        fake = FakeLLMProvider()
        service = StructuringService(llm_provider=fake)

        result = await service.structure_invoice(ocr_result("short"), "s.pdf")

        assert result.ocr_text_truncated is False
        assert "truncated" not in fake.calls[0]["user"]


class TestPromptRegistry:
    def test_default_is_active_version(self):
        assert get_prompt().version == ACTIVE_VERSION

    def test_explicit_version_lookup(self):
        assert get_prompt("v1").version == "v1"

    def test_unknown_version_raises(self):
        with pytest.raises(KeyError, match="v999"):
            get_prompt("v999")

    def test_system_prompt_encodes_core_rules(self):
        system = get_prompt("v1").system_prompt
        for rule in ("null", "ISO-8601", "ISO 4217", "Never invent"):
            assert rule in system

    def test_user_prompt_wraps_document(self):
        rendered = get_prompt("v1").render_user_prompt("SOME TEXT", "ocr")
        assert "<document>\nSOME TEXT\n</document>" in rendered
        assert "ocr" in rendered


class TestLLMFactory:
    def test_returns_openai_provider_when_configured(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        get_settings.cache_clear()
        try:
            assert isinstance(get_llm_provider(), OpenAIProvider)
        finally:
            get_settings.cache_clear()

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm_provider("claude")
