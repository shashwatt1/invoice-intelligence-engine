"""
Text Extraction Service — app/services/extraction_service.py

Routes a document to the cheapest viable extraction path and returns a
standardized OCRResult, regardless of which path ran.

Routing (per design.md §Document Processing Service and architecture.md
cost-optimization principle):

    application/pdf with a text layer  →  PDFTextExtractor (pdfplumber, free)
    scanned PDF or PNG/JPEG image      →  active OCR provider (factory)

Design decisions:
- Downstream layers (AI structuring, validation) consume OCRResult only.
  They never know — and must never care — whether text came from a digital
  PDF or from OCR. That contract is what makes OCR providers swappable.
- The OCR provider is resolved lazily: digital-PDF-only workloads never
  construct the provider, so they don't require OCR credentials.
- An OCRProvider can be injected for tests.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.services.ocr.base import OCRProvider, OCRResult
from app.services.ocr.factory import get_ocr_provider
from app.services.ocr.pdf_extractor import PDFTextExtractor, is_digital_pdf

logger = get_logger(__name__)


class ExtractionService:
    """
    Facade over the two extraction paths (digital PDF vs. OCR).

    Usage:
        service = ExtractionService()
        result = await service.extract_text(content, mime_type, filename)
    """

    def __init__(self, ocr_provider: OCRProvider | None = None) -> None:
        self._pdf_extractor = PDFTextExtractor()
        self._ocr_provider = ocr_provider

    @property
    def ocr_provider(self) -> OCRProvider:
        """Lazily resolve the configured OCR provider on first OCR-path use."""
        if self._ocr_provider is None:
            self._ocr_provider = get_ocr_provider()
        return self._ocr_provider

    async def extract_text(
        self, file_content: bytes, mime_type: str, filename: str = ""
    ) -> OCRResult:
        """
        Extract text from an uploaded document.

        Args:
            file_content: Raw file bytes (PDF, PNG, or JPEG).
            mime_type: Validated MIME type from the upload layer.
            filename: Original filename (used for logging).

        Returns:
            OCRResult with source_type 'digital_pdf' or 'ocr'.

        Raises:
            OCRExtractionError: If the chosen path produces no usable text.
        """
        if mime_type == "application/pdf" and is_digital_pdf(file_content):
            logger.info("extraction_route_selected", filename=filename, route="digital_pdf")
            return await self._pdf_extractor.extract_text(file_content, mime_type, filename)

        logger.info("extraction_route_selected", filename=filename, route="ocr")
        return await self.ocr_provider.extract_text(file_content, mime_type, filename)
