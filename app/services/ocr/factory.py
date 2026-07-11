"""
OCR Provider Factory — app/services/ocr/factory.py

Selects the active OCR provider from configuration (OCR_PROVIDER env var).

Design decisions:
- Follows design.md §OCR Service → Driver Selection. Business logic never
  instantiates a concrete provider — it always goes through this factory,
  so swapping providers is a one-line env change.
- Only Google Vision is implemented in the MVP. PaddleOCR and EasyOCR are
  declared in Settings (per the roadmap) but raise NotImplementedError
  with a clear message instead of failing deep inside the pipeline.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.services.ocr.base import OCRProvider
from app.services.ocr.google_vision import GoogleVisionOCR

# Providers on the roadmap (design.md) that are not part of the MVP.
_PLANNED_PROVIDERS = {"paddleocr", "easyocr"}


def get_ocr_provider(provider: str | None = None) -> OCRProvider:
    """
    Return the configured OCR provider instance.

    Args:
        provider: Optional explicit provider name. Defaults to
            settings.ocr_provider (OCR_PROVIDER env var).

    Returns:
        A concrete OCRProvider implementation.

    Raises:
        NotImplementedError: If a planned-but-unimplemented provider is selected.
        ValueError: If the provider name is unknown, or required credentials
            (e.g. GOOGLE_VISION_API_KEY) are missing.
    """
    name = (provider or get_settings().ocr_provider).lower()

    if name == "google_vision":
        return GoogleVisionOCR()

    if name in _PLANNED_PROVIDERS:
        raise NotImplementedError(
            f"OCR provider '{name}' is on the roadmap but not implemented in the MVP. "
            "Set OCR_PROVIDER=google_vision."
        )

    raise ValueError(f"Unknown OCR provider '{name}'. Supported: google_vision.")
