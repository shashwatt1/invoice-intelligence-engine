"""
tests/test_extraction_service.py — Extraction routing and factory tests.

Verifies:
- ExtractionService routes digital PDFs to pdfplumber and everything
  else to the configured OCR provider.
- get_ocr_provider() honors OCR_PROVIDER and fails clearly for
  planned-but-unimplemented or unknown providers.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services.extraction_service import ExtractionService
from app.services.ocr.base import OCRProvider, OCRResult
from app.services.ocr.factory import get_ocr_provider
from app.services.ocr.google_vision import GoogleVisionOCR
from tests.pdf_builder import build_pdf


class FakeOCRProvider(OCRProvider):
    """Records calls and returns a canned OCRResult."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def extract_text(
        self, file_content: bytes, mime_type: str, filename: str = ""
    ) -> OCRResult:
        self.calls.append((mime_type, filename))
        return OCRResult(full_text="ocr text", source_type="ocr", page_count=1)


class TestExtractionRouting:
    async def test_digital_pdf_routes_to_pdfplumber(self):
        fake_ocr = FakeOCRProvider()
        service = ExtractionService(ocr_provider=fake_ocr)

        result = await service.extract_text(
            build_pdf(["Digital invoice text"]), "application/pdf", "digital.pdf"
        )

        assert result.source_type == "digital_pdf"
        assert "Digital invoice text" in result.full_text
        assert fake_ocr.calls == []  # OCR was never touched

    async def test_scanned_pdf_routes_to_ocr(self):
        fake_ocr = FakeOCRProvider()
        service = ExtractionService(ocr_provider=fake_ocr)

        result = await service.extract_text(build_pdf([None]), "application/pdf", "scan.pdf")

        assert result.source_type == "ocr"
        assert fake_ocr.calls == [("application/pdf", "scan.pdf")]

    async def test_image_routes_to_ocr(self):
        fake_ocr = FakeOCRProvider()
        service = ExtractionService(ocr_provider=fake_ocr)

        result = await service.extract_text(b"png-bytes", "image/png", "photo.png")

        assert result.source_type == "ocr"
        assert fake_ocr.calls == [("image/png", "photo.png")]

    async def test_digital_pdf_needs_no_ocr_credentials(self):
        # No provider injected and no API key configured: the digital path
        # must still work because the provider is resolved lazily.
        service = ExtractionService()
        result = await service.extract_text(
            build_pdf(["text layer present"]), "application/pdf", "d.pdf"
        )
        assert result.source_type == "digital_pdf"


class TestOCRFactory:
    def test_returns_google_vision_when_configured(self, monkeypatch):
        monkeypatch.setenv("OCR_PROVIDER", "google_vision")
        monkeypatch.setenv("GOOGLE_VISION_API_KEY", "test-key")
        get_settings.cache_clear()
        try:
            provider = get_ocr_provider()
            assert isinstance(provider, GoogleVisionOCR)
        finally:
            get_settings.cache_clear()

    def test_explicit_argument_overrides_settings(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_VISION_API_KEY", "test-key")
        get_settings.cache_clear()
        try:
            provider = get_ocr_provider("google_vision")
            assert isinstance(provider, GoogleVisionOCR)
        finally:
            get_settings.cache_clear()

    @pytest.mark.parametrize("planned", ["paddleocr", "easyocr"])
    def test_planned_providers_raise_not_implemented(self, planned):
        with pytest.raises(NotImplementedError, match=planned):
            get_ocr_provider(planned)

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown OCR provider"):
            get_ocr_provider("tesseract")
