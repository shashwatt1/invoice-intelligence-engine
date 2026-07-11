"""
OCR Provider Interface — app/services/ocr/base.py

Defines the abstract OCR provider that all implementations must conform to.

Design decisions:
- Follows design.md §OCR Service → Provider Abstraction.
- `extract_text()` is the single method: accepts file bytes + MIME type,
  returns structured OCRResult with full text and optional word-level tokens.
- OCRResult is a dataclass, not a Pydantic model, because it never crosses
  an API boundary — it's an internal service-to-service contract.
- `source_type` indicates whether extraction used OCR or direct PDF parsing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class OCRToken:
    """
    A single recognized word with position and confidence.

    Matches design.md §Document Processing Service → OCRToken.
    """

    text: str
    confidence: float  # 0.0 – 1.0
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    page: int = 0


@dataclass
class OCRResult:
    """
    Structured output from any OCR provider.

    Contains the full extracted text and optionally word-level tokens
    with bounding boxes and confidence scores.
    """

    full_text: str
    source_type: str  # "digital_pdf" or "ocr"
    tokens: list[OCRToken] = field(default_factory=list)
    page_count: int = 0
    mean_confidence: float = 1.0  # 1.0 for digital PDFs (no OCR uncertainty)
    duration_ms: int = 0


class OCRProvider(ABC):
    """
    Abstract base class for all OCR providers.

    Implementing a new provider (PaddleOCR, EasyOCR, etc.) requires
    only implementing `extract_text()`. The calling code never knows
    which provider is active — it calls OCRProvider.extract_text().
    """

    @abstractmethod
    async def extract_text(
        self, file_content: bytes, mime_type: str, filename: str = ""
    ) -> OCRResult:
        """
        Extract text from a document.

        Args:
            file_content: Raw file bytes (PDF, PNG, or JPEG).
            mime_type: MIME type of the file.
            filename: Original filename (used for logging).

        Returns:
            OCRResult with extracted text and metadata.

        Raises:
            OCRExtractionError: If extraction produces no usable text.
        """
