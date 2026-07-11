"""app/services/ocr/__init__.py — OCR provider package."""

from app.services.ocr.base import OCRProvider, OCRResult, OCRToken
from app.services.ocr.factory import get_ocr_provider
from app.services.ocr.google_vision import GoogleVisionOCR
from app.services.ocr.pdf_extractor import PDFTextExtractor, is_digital_pdf

__all__ = [
    "GoogleVisionOCR",
    "OCRProvider",
    "OCRResult",
    "OCRToken",
    "PDFTextExtractor",
    "get_ocr_provider",
    "is_digital_pdf",
]
