"""
tests/integration/fakes.py — Deterministic stage fakes shared by the
pipeline and API integration suites. The database is the real dependency
under test; OCR and LLM stages are replaced with these offline fakes.
"""

from __future__ import annotations

from app.schemas.extraction import ExtractedInvoice, ExtractedLineItem, ExtractedVendor
from app.services.llm.base import LLMCallMetadata
from app.services.ocr.base import OCRResult
from app.services.structuring_service import InvoiceStructuringResult


class FakeExtraction:
    def __init__(self, result: OCRResult | None = None, error: Exception | None = None):
        self._result = result or OCRResult(
            full_text="INVOICE INV-001 Acme Corp total 18.90",
            source_type="digital_pdf",
            page_count=1,
            mean_confidence=1.0,
            duration_ms=12,
        )
        self._error = error

    async def extract_text(self, file_content, mime_type, filename=""):
        if self._error:
            raise self._error
        return self._result


def extracted_invoice(**overrides) -> ExtractedInvoice:
    base = {
        "vendor": ExtractedVendor(name="Acme Corp", tax_id="GB123", email="ap@acme.test"),
        "invoice_number": "INV-001",
        "invoice_date": "2026-01-15",
        "due_date": "2026-02-14",
        "currency": "USD",
        "purchase_order": "PO-777",
        "payment_terms": "Net 30",
        "subtotal": 18.9,
        "grand_total": 18.9,
        "line_items": [
            ExtractedLineItem(
                description="Blue Widget", quantity=2.0, unit_price=None,
                line_total=18.9, confidence=0.95,
            )
        ],
        "confidence": 0.95,
    }
    base.update(overrides)
    return ExtractedInvoice(**base)


class FakeStructuring:
    def __init__(self, invoice: ExtractedInvoice | None = None, error: Exception | None = None):
        self._invoice = invoice or extracted_invoice()
        self._error = error

    async def structure_invoice(self, ocr_result, filename=""):
        if self._error:
            raise self._error
        return InvoiceStructuringResult(
            invoice=self._invoice,
            raw_response={"id": "chatcmpl-fake", "model": "gpt-4o-mini"},
            prompt_version="v1",
            metadata=LLMCallMetadata(
                provider="openai", model="gpt-4o-mini", request_id="chatcmpl-fake",
                finish_reason="stop", latency_ms=350, input_tokens=1200,
                output_tokens=250, total_tokens=1450, estimated_cost_usd=0.00033,
            ),
        )
