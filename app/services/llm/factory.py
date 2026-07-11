"""
LLM Provider Factory — app/services/llm/factory.py

Selects the active LLM provider from configuration (LLM_PROVIDER env var).

Design decisions:
- Mirrors app/services/ocr/factory.py: business logic never instantiates
  a concrete provider directly.
- Only OpenAI is implemented in the MVP. Claude, Gemini, and Azure OpenAI
  are on the roadmap; adding one means a new module implementing
  LLMProvider plus a branch here — no business-logic changes.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.services.llm.base import LLMProvider
from app.services.llm.openai_provider import OpenAIProvider


def get_llm_provider(provider: str | None = None) -> LLMProvider:
    """
    Return the configured LLM provider instance.

    Args:
        provider: Optional explicit provider name. Defaults to
            settings.llm_provider (LLM_PROVIDER env var).

    Returns:
        A concrete LLMProvider implementation.

    Raises:
        ValueError: If the provider name is unknown, or required credentials
            (e.g. OPENAI_API_KEY) are missing.
    """
    name = (provider or get_settings().llm_provider).lower()

    if name == "openai":
        return OpenAIProvider()

    raise ValueError(f"Unknown LLM provider '{name}'. Supported: openai.")
