"""
Invoice Structuring Service — app/services/structuring_service.py

Orchestrates the AI structuring stage: takes the OCRResult produced by
the extraction layer and returns a validated ExtractedInvoice plus full
call observability.

    OCRResult → prompt (versioned) → LLMProvider → InvoiceStructuringResult

Design decisions:
- Mirrors ExtractionService: a thin facade with a lazily resolved
  provider, so constructing the service never requires LLM credentials
  and tests can inject a fake provider.
- Prompt selection is pinned per service instance and stamped into the
  result, so every stored extraction is traceable to the exact prompt
  version and model that produced it.
- OCR text is truncated to a character budget derived from
  openai_max_tokens_per_document (architecture.md cost-awareness
  principle). Invoices carry their key fields early (header + line
  items); trailing boilerplate is the safest content to drop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.prompts.invoice_extraction import PromptTemplate, get_prompt
from app.schemas.extraction import ExtractedInvoice
from app.services.llm.base import LLMCallMetadata, LLMProvider
from app.services.llm.factory import get_llm_provider
from app.services.ocr.base import OCRResult

logger = get_logger(__name__)

# Rough chars-per-token ratio used to convert the configured token budget
# into a character budget without a tokenizer dependency.
_CHARS_PER_TOKEN_ESTIMATE = 4

_TRUNCATION_MARKER = "\n\n[... document text truncated ...]"


@dataclass
class InvoiceStructuringResult:
    """
    Output of the AI structuring stage — consumed by the validation
    engine (Milestone C) and persistence layer (Milestone D).

    Attributes:
        invoice: The validated structured invoice.
        raw_response: Full provider response (→ invoices.raw_extraction_json).
        prompt_version: Prompt template version used for this extraction.
        metadata: LLM call observability record (→ ProcessingLog.payload).
        ocr_text_truncated: True if the document text was cut to fit the
            token budget — a signal for reviewers that data may be missing.
    """

    invoice: ExtractedInvoice
    raw_response: dict[str, Any]
    prompt_version: str
    metadata: LLMCallMetadata
    ocr_text_truncated: bool = False


class StructuringService:
    """
    Facade over the AI structuring stage.

    Usage:
        service = StructuringService()
        result = await service.structure_invoice(ocr_result, filename)
    """

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._prompt: PromptTemplate = get_prompt(prompt_version)

    @property
    def llm_provider(self) -> LLMProvider:
        """Lazily resolve the configured LLM provider on first use."""
        if self._llm_provider is None:
            self._llm_provider = get_llm_provider()
        return self._llm_provider

    async def structure_invoice(
        self, ocr_result: OCRResult, filename: str = ""
    ) -> InvoiceStructuringResult:
        """
        Convert extracted document text into a structured invoice.

        Args:
            ocr_result: Output of ExtractionService (digital PDF or OCR).
            filename: Original filename (used for logging).

        Returns:
            InvoiceStructuringResult with the parsed invoice and metadata.

        Raises:
            AIStructuringError (or a subclass): On any provider failure.
        """
        text, truncated = self._fit_to_budget(ocr_result.full_text)
        if truncated:
            logger.warning(
                "ocr_text_truncated_for_llm",
                filename=filename,
                original_chars=len(ocr_result.full_text),
                sent_chars=len(text),
            )

        logger.info(
            "invoice_structuring_started",
            filename=filename,
            prompt_version=self._prompt.version,
            source_type=ocr_result.source_type,
        )

        response = await self.llm_provider.generate_structured(
            system_prompt=self._prompt.system_prompt,
            user_prompt=self._prompt.render_user_prompt(text, ocr_result.source_type),
            schema=ExtractedInvoice,
        )

        logger.info(
            "invoice_structuring_complete",
            filename=filename,
            prompt_version=self._prompt.version,
            invoice_number=response.parsed.invoice_number,
            line_item_count=len(response.parsed.line_items),
            model=response.metadata.model,
            total_tokens=response.metadata.total_tokens,
        )

        return InvoiceStructuringResult(
            invoice=response.parsed,
            raw_response=response.raw_response,
            prompt_version=self._prompt.version,
            metadata=response.metadata,
            ocr_text_truncated=truncated,
        )

    @staticmethod
    def _fit_to_budget(text: str) -> tuple[str, bool]:
        """Truncate document text to the configured token budget (as chars)."""
        budget_chars = (
            get_settings().openai_max_tokens_per_document * _CHARS_PER_TOKEN_ESTIMATE
        )
        if len(text) <= budget_chars:
            return text, False
        keep = max(budget_chars - len(_TRUNCATION_MARKER), 0)
        return text[:keep] + _TRUNCATION_MARKER, True
