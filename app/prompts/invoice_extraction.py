"""
Invoice Extraction Prompts — app/prompts/invoice_extraction.py

Versioned prompt templates for the AI structuring layer.

Design decisions:
- Prompts are versioned production assets. Each version is an immutable
  PromptTemplate registered in _REGISTRY; the active version is stamped
  into every structuring result so any stored extraction can be traced
  back to the exact prompt that produced it.
- Evolving a prompt means adding a new version (and optionally moving
  ACTIVE_VERSION), never mutating an existing one — old ProcessingLog
  entries must stay interpretable.
- Field-level schema guidance lives in app/schemas/extraction.py Field
  descriptions (sent to the model as JSON schema). The system prompt
  covers behavior: grounding, no fabrication, formats, edge cases.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

_SYSTEM_PROMPT_V1 = """\
You are an expert invoice data extraction engine for an enterprise \
accounts-payable platform.

You receive raw text extracted from an invoice (via digital PDF parsing or \
OCR) and must populate the provided JSON schema.

Rules:
1. Extract ONLY what is present in the text. Never invent, guess, or fill \
in plausible values. If a field is not present or not legible, use null.
2. OCR text may contain noise, broken lines, or merged columns. Reconstruct \
line items carefully using numeric alignment and context.
3. Dates: output ISO-8601 (YYYY-MM-DD). Resolve ambiguous formats using \
context (e.g. a day greater than 12); if still ambiguous, prefer the \
vendor's locale if evident, otherwise use null.
4. Amounts: output plain numbers without currency symbols or thousands \
separators. Do not round printed values.
5. Currency: output the ISO 4217 code. Infer from symbols only when \
unambiguous (e.g. "€" → EUR); "$" alone is USD unless context says otherwise.
6. Line items: preserve document order. If a unit price is missing but \
quantity and line total are printed, derive unit_price = line_total / \
quantity. Never derive or alter a printed value.
7. Do not confuse the bill-to / ship-to party with the vendor. The vendor \
is the party issuing the invoice.
8. Subtotal, tax, and grand total must be the printed values, even if the \
math looks inconsistent — validation happens downstream.
9. Report honest confidence scores. Use lower confidence for noisy OCR \
regions rather than omitting data you can partially read.
"""


def _build_user_prompt_v1(ocr_text: str, source_type: str = "") -> str:
    source_note = f" (extraction method: {source_type})" if source_type else ""
    return (
        f"Extract structured invoice data from the following document text{source_note}.\n\n"
        f"<document>\n{ocr_text}\n</document>"
    )


@dataclass(frozen=True)
class PromptTemplate:
    """An immutable, versioned prompt pair for one structuring task."""

    version: str
    system_prompt: str
    build_user_prompt: Callable[[str, str], str] = field(repr=False)

    def render_user_prompt(self, ocr_text: str, source_type: str = "") -> str:
        return self.build_user_prompt(ocr_text, source_type)


_REGISTRY: dict[str, PromptTemplate] = {
    "v1": PromptTemplate(
        version="v1",
        system_prompt=_SYSTEM_PROMPT_V1,
        build_user_prompt=_build_user_prompt_v1,
    ),
}

ACTIVE_VERSION = "v1"


def get_prompt(version: str | None = None) -> PromptTemplate:
    """
    Return a prompt template by version (default: ACTIVE_VERSION).

    Raises:
        KeyError: If the requested version is not registered.
    """
    key = version or ACTIVE_VERSION
    if key not in _REGISTRY:
        raise KeyError(
            f"Unknown prompt version '{key}'. Registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[key]
