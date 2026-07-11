"""
PDF Text Extractor — app/services/ocr/pdf_extractor.py

Extracts text from digital-native PDFs using pdfplumber.
This is NOT OCR — it reads the embedded text layer directly.

Design decisions:
- Digital PDFs bypass OCR entirely, per architecture.md cost optimization.
  This saves $0 per document vs. ~$0.0015/page for Cloud Vision.
- pdfplumber is chosen because it handles complex table layouts better
  than pypdf/PyPDF2 (per the early prototype analysis in architecture.md).
- If a PDF has no extractable text on any page, it's classified as
  a scanned document and should be routed to the OCR provider instead.
- Confidence is 1.0 for all digital text (no OCR uncertainty).
"""

from __future__ import annotations

import io
import time

from app.core.exceptions import OCRExtractionError
from app.core.logging import get_logger
from app.services.ocr.base import OCRProvider, OCRResult

logger = get_logger(__name__)


class PDFTextExtractor(OCRProvider):
    """
    Extracts text from digital-native PDFs using pdfplumber.

    Returns OCRResult with source_type='digital_pdf' and confidence=1.0.
    Raises OCRExtractionError if no text is found (indicating a scanned PDF).
    """

    async def extract_text(
        self, file_content: bytes, mime_type: str, filename: str = ""
    ) -> OCRResult:
        """
        Extract text from a digital PDF.

        Args:
            file_content: Raw PDF bytes.
            mime_type: Must be 'application/pdf'.
            filename: Original filename for logging.

        Returns:
            OCRResult with full extracted text.

        Raises:
            OCRExtractionError: If the PDF contains no extractable text.
        """
        import pdfplumber

        start = time.monotonic()
        logger.info("pdf_text_extraction_started", filename=filename)

        try:
            pages_text = []
            with pdfplumber.open(io.BytesIO(file_content)) as pdf:
                page_count = len(pdf.pages)
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages_text.append(text.strip())

            duration_ms = int((time.monotonic() - start) * 1000)

            if not pages_text:
                logger.warning("pdf_no_text_found", filename=filename)
                raise OCRExtractionError(
                    message=(
                        "No extractable text found in PDF. "
                        "This appears to be a scanned document."
                    ),
                    detail={"filename": filename, "page_count": page_count},
                )

            full_text = "\n\n".join(pages_text)

            logger.info(
                "pdf_text_extraction_complete",
                filename=filename,
                page_count=page_count,
                text_length=len(full_text),
                duration_ms=duration_ms,
            )

            return OCRResult(
                full_text=full_text,
                source_type="digital_pdf",
                tokens=[],  # No word-level tokens for digital extraction
                page_count=page_count,
                mean_confidence=1.0,  # Digital text has no uncertainty
                duration_ms=duration_ms,
            )

        except OCRExtractionError:
            raise
        except Exception as exc:
            logger.error("pdf_text_extraction_failed", filename=filename, error=str(exc))
            raise OCRExtractionError(
                message=f"Failed to extract text from PDF: {exc}",
                detail={"filename": filename, "error": str(exc)},
            ) from exc


def is_digital_pdf(file_content: bytes) -> bool:
    """
    Quick check: does this PDF contain any extractable text?

    Used by the pipeline to decide between digital PDF extraction and OCR.
    Returns True if at least one page has embedded text.
    """
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(file_content)) as pdf:
            for page in pdf.pages[:3]:  # Check first 3 pages only for speed
                if page.extract_text():
                    return True
        return False
    except Exception:
        return False
