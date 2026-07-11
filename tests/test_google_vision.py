"""
tests/test_google_vision.py — Google Vision OCR provider tests.

Uses httpx.MockTransport so the full request/parse path is exercised
without network access or a real API key.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.exceptions import OCRExtractionError
from app.services.ocr.google_vision import (
    FILES_ENDPOINT,
    IMAGES_ENDPOINT,
    GoogleVisionOCR,
)

# ---------------------------------------------------------------------------
# Canned Vision response builders
# ---------------------------------------------------------------------------


def vision_word(text: str, confidence: float = 0.95) -> dict:
    """A Vision API word: per-character symbols + confidence + bounding box."""
    return {
        "symbols": [{"text": ch} for ch in text],
        "confidence": confidence,
        "boundingBox": {
            "vertices": [
                {"x": 10, "y": 20},
                {"x": 90, "y": 20},
                {"x": 90, "y": 35},
                {"x": 10, "y": 35},
            ]
        },
    }


def full_text_annotation(text: str, words: list[dict]) -> dict:
    return {
        "text": text,
        "pages": [{"blocks": [{"paragraphs": [{"words": words}]}]}],
    }


def make_transport(payload: dict, status_code: int = 200, captured: list | None = None):
    """MockTransport returning a fixed payload; optionally captures requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Image path (images:annotate)
# ---------------------------------------------------------------------------


class TestGoogleVisionImage:
    async def test_extracts_text_and_tokens_from_image(self):
        payload = {
            "responses": [
                {
                    "fullTextAnnotation": full_text_annotation(
                        "INVOICE 123",
                        [vision_word("INVOICE", 0.98), vision_word("123", 0.90)],
                    )
                }
            ]
        }
        captured: list[httpx.Request] = []
        provider = GoogleVisionOCR(api_key="test-key", transport=make_transport(payload, captured=captured))

        result = await provider.extract_text(b"fake-png-bytes", "image/png", "scan.png")

        assert result.full_text == "INVOICE 123"
        assert result.source_type == "ocr"
        assert [t.text for t in result.tokens] == ["INVOICE", "123"]
        assert result.tokens[0].confidence == pytest.approx(0.98)
        assert result.tokens[0].x0 == 10.0 and result.tokens[0].x1 == 90.0
        assert result.mean_confidence == pytest.approx(0.94)

        # Request shape: images endpoint, API key as query param, correct feature
        request = captured[0]
        assert str(request.url).startswith(IMAGES_ENDPOINT)
        assert request.url.params["key"] == "test-key"
        body = json.loads(request.content)
        assert body["requests"][0]["features"] == [{"type": "DOCUMENT_TEXT_DETECTION"}]
        assert "content" in body["requests"][0]["image"]

    async def test_raises_when_no_text_detected(self):
        payload = {"responses": [{}]}  # No fullTextAnnotation
        provider = GoogleVisionOCR(api_key="test-key", transport=make_transport(payload))

        with pytest.raises(OCRExtractionError):
            await provider.extract_text(b"blank", "image/png", "blank.png")

    async def test_raises_on_http_error(self):
        provider = GoogleVisionOCR(
            api_key="test-key", transport=make_transport({"error": "denied"}, status_code=403)
        )

        with pytest.raises(OCRExtractionError) as exc_info:
            await provider.extract_text(b"img", "image/jpeg", "x.jpg")
        assert "403" in exc_info.value.message

    async def test_raises_on_annotation_error(self):
        payload = {"responses": [{"error": {"code": 3, "message": "Bad image data."}}]}
        provider = GoogleVisionOCR(api_key="test-key", transport=make_transport(payload))

        with pytest.raises(OCRExtractionError) as exc_info:
            await provider.extract_text(b"img", "image/png", "bad.png")
        assert "Bad image data" in exc_info.value.message


# ---------------------------------------------------------------------------
# Scanned PDF path (files:annotate)
# ---------------------------------------------------------------------------


class TestGoogleVisionPdf:
    async def test_scanned_pdf_uses_files_endpoint_and_joins_pages(self):
        payload = {
            "responses": [
                {
                    "responses": [
                        {"fullTextAnnotation": full_text_annotation("Page one", [vision_word("one", 0.9)])},
                        {"fullTextAnnotation": full_text_annotation("Page two", [vision_word("two", 0.8)])},
                    ]
                }
            ]
        }
        captured: list[httpx.Request] = []
        provider = GoogleVisionOCR(api_key="test-key", transport=make_transport(payload, captured=captured))

        result = await provider.extract_text(b"%PDF-fake", "application/pdf", "scan.pdf")

        assert result.full_text == "Page one\n\nPage two"
        assert result.page_count == 2
        assert result.mean_confidence == pytest.approx(0.85)
        # Tokens carry their page index
        assert [t.page for t in result.tokens] == [0, 1]

        request = captured[0]
        assert str(request.url).startswith(FILES_ENDPOINT)
        body = json.loads(request.content)
        assert body["requests"][0]["inputConfig"]["mimeType"] == "application/pdf"
        assert body["requests"][0]["pages"] == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestGoogleVisionConstruction:
    def test_missing_api_key_fails_fast(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_VISION_API_KEY", "")
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            with pytest.raises(ValueError, match="GOOGLE_VISION_API_KEY"):
                GoogleVisionOCR()
        finally:
            get_settings.cache_clear()
