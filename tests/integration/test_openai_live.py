"""
tests/integration/test_openai_live.py — Optional live OpenAI smoke test.

Skipped by default so the automated suite never needs network access or
spends tokens. To run manually against the configured OPENAI_API_KEY:

    RUN_LIVE_LLM_TESTS=1 .venv/bin/python -m pytest tests/integration -q --no-cov
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_LLM_TESTS") != "1",
    reason="Live LLM test — set RUN_LIVE_LLM_TESTS=1 to run (spends OpenAI tokens).",
)

SAMPLE_INVOICE_TEXT = """\
ACME SUPPLIES LTD
1 Acme Way, London, UK
VAT: GB123456789

INVOICE
Invoice Number: INV-2026-0042
Invoice Date: 2026-01-15
Due Date: 2026-02-14
Payment Terms: Net 30
PO Number: PO-777

Description           Qty    Unit Price    Amount
Blue Widgets            2                   18.90
Steel Brackets          5         4.00      20.00

Subtotal:   38.90
VAT (20%):   7.78
TOTAL:     46.68 USD
"""


async def test_live_invoice_structuring():
    from app.services.ocr.base import OCRResult
    from app.services.structuring_service import StructuringService

    result = await StructuringService().structure_invoice(
        OCRResult(full_text=SAMPLE_INVOICE_TEXT, source_type="digital_pdf", page_count=1),
        "live-sample.txt",
    )

    invoice = result.invoice
    assert invoice.invoice_number == "INV-2026-0042"
    assert invoice.vendor.name and "ACME" in invoice.vendor.name.upper()
    assert invoice.grand_total == pytest.approx(46.68)
    assert len(invoice.line_items) == 2
    # The qty=2 / total=18.90 business rule: unit price must be derived, not lost
    widgets = invoice.line_items[0]
    assert widgets.quantity == pytest.approx(2)
    assert widgets.line_total == pytest.approx(18.90)
    assert widgets.unit_price == pytest.approx(9.45)

    meta = result.metadata
    assert meta.total_tokens > 0
    assert meta.request_id
    print(
        f"\nLive call OK — model={meta.model} tokens={meta.total_tokens} "
        f"cost=${meta.estimated_cost_usd} latency={meta.latency_ms}ms"
    )
