"""
Google Vision OCR Provider — app/services/ocr/google_vision.py

Extracts text from scanned PDFs and images using the Google Cloud Vision
REST API (DOCUMENT_TEXT_DETECTION).

Design decisions:
- REST + httpx instead of the google-cloud-vision SDK. The SDK pulls in
  ~10 gRPC dependencies and requires service-account JSON; the REST API
  works with the GOOGLE_VISION_API_KEY already defined in Settings and
  httpx is already a project dependency. One less credential story.
- Images use `images:annotate`; PDFs use `files:annotate`, which accepts
  raw PDF bytes synchronously for up to 5 pages (MAX_SYNC_PDF_PAGES).
  Multi-page scanned invoices beyond 5 pages need the async batch API —
  out of MVP scope, documented here.
- An httpx transport can be injected for tests, so the full request/parse
  path is exercised without network access or a real API key.
- Word-level confidence and bounding boxes are parsed into OCRToken so
  the validation layer (Milestone C) can compute composite confidence.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import OCRExtractionError
from app.core.logging import get_logger
from app.services.ocr.base import OCRProvider, OCRResult, OCRToken

logger = get_logger(__name__)

IMAGES_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
FILES_ENDPOINT = "https://vision.googleapis.com/v1/files:annotate"

# files:annotate processes at most 5 pages synchronously. Larger scanned
# PDFs require the async batch API (gs:// output) — post-MVP.
MAX_SYNC_PDF_PAGES = 5

REQUEST_TIMEOUT_SECONDS = 60.0


class GoogleVisionOCR(OCRProvider):
    """
    OCR provider backed by Google Cloud Vision DOCUMENT_TEXT_DETECTION.

    Returns OCRResult with source_type='ocr', word-level tokens, and the
    mean word confidence reported by Vision.
    """

    def __init__(
        self,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else get_settings().google_vision_api_key
        if not self._api_key:
            raise ValueError(
                "GOOGLE_VISION_API_KEY is not configured. "
                "Set it in the environment to use the google_vision OCR provider."
            )
        self._transport = transport

    async def extract_text(
        self, file_content: bytes, mime_type: str, filename: str = ""
    ) -> OCRResult:
        """
        Extract text from a scanned PDF or image via Google Vision.

        Args:
            file_content: Raw file bytes (PDF, PNG, or JPEG).
            mime_type: MIME type of the file.
            filename: Original filename (used for logging).

        Returns:
            OCRResult with extracted text, tokens, and mean confidence.

        Raises:
            OCRExtractionError: On API errors or when no text is detected.
        """
        start = time.monotonic()
        logger.info("google_vision_ocr_started", filename=filename, mime_type=mime_type)

        if mime_type == "application/pdf":
            page_annotations = await self._annotate_pdf(file_content, filename)
        else:
            page_annotations = [await self._annotate_image(file_content, filename)]

        duration_ms = int((time.monotonic() - start) * 1000)

        texts: list[str] = []
        tokens: list[OCRToken] = []
        for page_index, annotation in enumerate(page_annotations):
            if not annotation:
                continue
            text = annotation.get("text", "")
            if text.strip():
                texts.append(text.strip())
            tokens.extend(self._parse_tokens(annotation, page_index))

        if not texts:
            logger.warning("google_vision_no_text_found", filename=filename)
            raise OCRExtractionError(
                message="Google Vision detected no text in the document.",
                detail={"filename": filename, "mime_type": mime_type},
            )

        full_text = "\n\n".join(texts)
        confidences = [t.confidence for t in tokens]
        mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        logger.info(
            "google_vision_ocr_complete",
            filename=filename,
            page_count=len(page_annotations),
            token_count=len(tokens),
            text_length=len(full_text),
            mean_confidence=round(mean_confidence, 4),
            duration_ms=duration_ms,
        )

        return OCRResult(
            full_text=full_text,
            source_type="ocr",
            tokens=tokens,
            page_count=len(page_annotations),
            mean_confidence=mean_confidence,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Vision API calls
    # ------------------------------------------------------------------

    async def _annotate_image(
        self, file_content: bytes, filename: str
    ) -> dict[str, Any] | None:
        """Call images:annotate for a PNG/JPEG. Returns fullTextAnnotation."""
        body = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(file_content).decode("ascii")},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                }
            ]
        }
        response = await self._post(IMAGES_ENDPOINT, body, filename)
        return response.get("fullTextAnnotation")

    async def _annotate_pdf(
        self, file_content: bytes, filename: str
    ) -> list[dict[str, Any] | None]:
        """Call files:annotate for a scanned PDF. Returns one annotation per page."""
        body = {
            "requests": [
                {
                    "inputConfig": {
                        "content": base64.b64encode(file_content).decode("ascii"),
                        "mimeType": "application/pdf",
                    },
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "pages": list(range(1, MAX_SYNC_PDF_PAGES + 1)),
                }
            ]
        }
        response = await self._post(FILES_ENDPOINT, body, filename)
        page_responses = response.get("responses", [])
        return [page.get("fullTextAnnotation") for page in page_responses]

    async def _post(self, endpoint: str, body: dict[str, Any], filename: str) -> dict[str, Any]:
        """
        POST to a Vision endpoint and return the first per-document response.

        Raises OCRExtractionError on transport failures, non-200 statuses,
        or an error object inside the response payload.
        """
        client_kwargs: dict[str, Any] = {"timeout": REQUEST_TIMEOUT_SECONDS}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                http_response = await client.post(
                    endpoint, params={"key": self._api_key}, json=body
                )
        except httpx.HTTPError as exc:
            logger.error("google_vision_request_failed", filename=filename, error=str(exc))
            raise OCRExtractionError(
                message=f"Google Vision request failed: {exc}",
                detail={"filename": filename},
            ) from exc

        if http_response.status_code != 200:
            logger.error(
                "google_vision_api_error",
                filename=filename,
                status_code=http_response.status_code,
            )
            raise OCRExtractionError(
                message=f"Google Vision API returned HTTP {http_response.status_code}.",
                detail={"filename": filename, "body": http_response.text[:500]},
            )

        payload = http_response.json()
        responses = payload.get("responses", [])
        if not responses:
            raise OCRExtractionError(
                message="Google Vision returned an empty response.",
                detail={"filename": filename},
            )

        first = responses[0]
        if "error" in first:
            error = first["error"]
            logger.error("google_vision_annotation_error", filename=filename, error=error)
            raise OCRExtractionError(
                message=f"Google Vision annotation error: {error.get('message', 'unknown')}",
                detail={"filename": filename, "error": error},
            )
        return first

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_tokens(annotation: dict[str, Any], page_index: int) -> list[OCRToken]:
        """
        Flatten a fullTextAnnotation into word-level OCRTokens.

        Vision nests words as pages → blocks → paragraphs → words → symbols.
        Confidence and bounding boxes are optional in real responses.
        """
        tokens: list[OCRToken] = []
        for page in annotation.get("pages", []):
            for block in page.get("blocks", []):
                for paragraph in block.get("paragraphs", []):
                    for word in paragraph.get("words", []):
                        text = "".join(
                            symbol.get("text", "") for symbol in word.get("symbols", [])
                        )
                        if not text:
                            continue
                        vertices = word.get("boundingBox", {}).get("vertices", [])
                        xs = [v.get("x", 0) for v in vertices] or [0]
                        ys = [v.get("y", 0) for v in vertices] or [0]
                        tokens.append(
                            OCRToken(
                                text=text,
                                confidence=float(word.get("confidence", 0.0)),
                                x0=float(min(xs)),
                                y0=float(min(ys)),
                                x1=float(max(xs)),
                                y1=float(max(ys)),
                                page=page_index,
                            )
                        )
        return tokens
