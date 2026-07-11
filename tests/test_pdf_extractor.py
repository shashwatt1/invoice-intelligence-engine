"""
tests/test_pdf_extractor.py — Digital PDF text-layer extraction tests.

Covers PDFTextExtractor and the is_digital_pdf() routing check using
synthetic in-memory PDFs (see tests/pdf_builder.py).
"""

from __future__ import annotations

import pytest

from app.core.exceptions import OCRExtractionError
from app.services.ocr.pdf_extractor import PDFTextExtractor, is_digital_pdf
from tests.pdf_builder import build_pdf


class TestPDFTextExtractor:
    async def test_extracts_text_from_digital_pdf(self):
        pdf = build_pdf(["INVOICE #123 Total: 18.90"])
        result = await PDFTextExtractor().extract_text(pdf, "application/pdf", "inv.pdf")

        assert "INVOICE #123" in result.full_text
        assert result.source_type == "digital_pdf"
        assert result.page_count == 1
        assert result.mean_confidence == 1.0
        assert result.duration_ms >= 0

    async def test_joins_multiple_pages(self):
        pdf = build_pdf(["Page one text", "Page two text"])
        result = await PDFTextExtractor().extract_text(pdf, "application/pdf", "multi.pdf")

        assert "Page one text" in result.full_text
        assert "Page two text" in result.full_text
        assert result.page_count == 2

    async def test_raises_on_pdf_without_text_layer(self):
        pdf = build_pdf([None])
        with pytest.raises(OCRExtractionError) as exc_info:
            await PDFTextExtractor().extract_text(pdf, "application/pdf", "scan.pdf")
        assert "scanned" in exc_info.value.message.lower()

    async def test_raises_on_corrupt_pdf(self):
        with pytest.raises(OCRExtractionError):
            await PDFTextExtractor().extract_text(b"not a pdf at all", "application/pdf", "x.pdf")


class TestIsDigitalPdf:
    def test_true_for_pdf_with_text_layer(self):
        assert is_digital_pdf(build_pdf(["Some embedded text"])) is True

    def test_false_for_pdf_without_text_layer(self):
        assert is_digital_pdf(build_pdf([None])) is False

    def test_false_for_corrupt_bytes(self):
        assert is_digital_pdf(b"garbage") is False

    def test_true_when_text_appears_on_a_later_page(self):
        assert is_digital_pdf(build_pdf([None, "text on page two"])) is True
