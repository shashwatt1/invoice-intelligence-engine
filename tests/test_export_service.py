"""
tests/test_export_service.py — Export formatter unit tests (no DB).

Builds detached ORM instances and checks the JSON payload shape, TXT
readability, CSV structure, and filename sanitization.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.document import Document
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.vendor import Vendor
from app.services.export_service import (
    CSV_HEADERS,
    build_export_payload,
    build_items_csv,
    build_txt,
    export_basename,
)


def make_invoice(**overrides) -> Invoice:
    invoice = Invoice(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        invoice_number="INV-2026-0042",
        invoice_date=date(2026, 3, 31),
        due_date=date(2026, 4, 30),
        currency="USD",
        subtotal=Decimal("38.90"),
        tax_amount=Decimal("7.78"),
        discount_amount=Decimal("0.00"),
        grand_total=Decimal("46.68"),
        vendor_name="Apple Food & Grocery Inc",
        status="VALIDATED",
        composite_confidence=Decimal("0.9840"),
        extraction_model="gpt-4o-mini",
    )
    invoice.created_at = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)
    invoice.vendor = Vendor(
        id=uuid.uuid4(), name="Apple Food & Grocery Inc", tax_id="US-998877",
        address="1 Grocer Way", email="ap@apple-food.test",
    )
    invoice.document = Document(
        id=invoice.document_id, filename="apple-food.pdf", mime_type="application/pdf",
        file_size_bytes=2048, file_path="/uploads/x.pdf", file_hash="a" * 64,
        source_type="digital_pdf",
    )
    invoice.items = [
        InvoiceItem(
            invoice_id=invoice.id, description="COORS LIGHT 2/12/12 CAN",
            quantity=Decimal("3.0000"), unit_price=Decimal("21.9500"),
            line_total=Decimal("65.85"), tax_rate=Decimal("6.0000"),
            sort_order=0, product_sku="0071990030",
        ),
        InvoiceItem(
            invoice_id=invoice.id, description="MODELO ESPECIAL 12PK",
            quantity=Decimal("2.0000"), unit_price=Decimal("18.5000"),
            line_total=Decimal("37.00"), tax_rate=None, sort_order=1,
        ),
    ]
    for key, value in overrides.items():
        setattr(invoice, key, value)
    return invoice


class TestJsonPayload:
    def test_payload_shape_and_values(self):
        payload = build_export_payload(make_invoice())

        assert payload["schema_version"] == "1.0"
        assert payload["invoice"]["invoice_number"] == "INV-2026-0042"
        assert payload["invoice"]["invoice_date"] == "2026-03-31"
        assert payload["invoice"]["totals"] == {
            "subtotal": 38.9, "tax": 7.78, "discount": 0.0, "grand_total": 46.68,
        }
        assert payload["vendor"]["name"] == "Apple Food & Grocery Inc"
        assert payload["vendor"]["tax_id"] == "US-998877"
        assert [item["position"] for item in payload["line_items"]] == [1, 2]
        assert payload["line_items"][0]["sku_upc"] == "0071990030"
        assert payload["line_items"][1]["tax_rate"] is None
        assert payload["validation"] == {
            "status": "VALIDATED", "review_required": False, "composite_confidence": 0.984,
        }
        assert payload["processing"]["source_type"] == "digital_pdf"

    def test_review_required_flag(self):
        payload = build_export_payload(make_invoice(status="REVIEW_REQUIRED"))
        assert payload["validation"]["review_required"] is True

    def test_vendor_falls_back_to_denormalized_fields(self):
        invoice = make_invoice()
        invoice.vendor = None
        payload = build_export_payload(invoice)
        assert payload["vendor"]["name"] == "Apple Food & Grocery Inc"
        assert payload["vendor"]["email"] is None


class TestTxt:
    def test_txt_is_human_readable(self):
        text = build_txt(make_invoice())

        for expected in [
            "Vendor\n------\nApple Food & Grocery Inc",
            "Invoice Number:\nINV-2026-0042",
            "Items",
            "1.\nDescription:\nCOORS LIGHT 2/12/12 CAN",
            "Quantity:\n3.00",
            "Unit Price:\n21.95",
            "Grand Total:\n46.68 USD",
            "Validation Status:\nVALIDATED",
            "Confidence:\n98.4%",
            "Review Required:\nNo",
        ]:
            assert expected in text, f"missing block: {expected!r}"

    def test_txt_handles_missing_values(self):
        invoice = make_invoice(due_date=None, tax_amount=None, composite_confidence=None)
        text = build_txt(invoice)
        assert "Due Date:\n—" in text
        assert "Confidence:\n—" in text


class TestCsv:
    def test_csv_headers_and_rows(self):
        content = build_items_csv(make_invoice())
        rows = list(csv.reader(io.StringIO(content)))

        assert rows[0] == CSV_HEADERS
        assert rows[1] == ["COORS LIGHT 2/12/12 CAN", "3.0", "21.95", "65.85", "6.0", "0071990030"]
        assert rows[2] == ["MODELO ESPECIAL 12PK", "2.0", "18.5", "37.0", "", ""]

    def test_csv_quotes_commas_in_descriptions(self):
        invoice = make_invoice()
        invoice.items[0].description = 'Widget, "deluxe", 12ct'
        rows = list(csv.reader(io.StringIO(build_items_csv(invoice))))
        assert rows[1][0] == 'Widget, "deluxe", 12ct'


class TestFilenames:
    def test_basename_uses_invoice_number(self):
        assert export_basename(make_invoice()) == "invoice_INV-2026-0042"

    def test_basename_sanitizes_unsafe_characters(self):
        invoice = make_invoice(invoice_number="INV/2026 #42*")
        assert export_basename(invoice) == "invoice_INV-2026-42"

    def test_basename_falls_back_to_id_when_number_missing(self):
        invoice = make_invoice(invoice_number=None)
        assert export_basename(invoice) == f"invoice_{str(invoice.id)[:8]}"
