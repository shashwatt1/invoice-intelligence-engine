"""
tests/integration/test_export_api.py — Export endpoint against real Postgres.

Processes an invoice through the real pipeline (fake LLM), then exercises
GET /invoices/{id}/export in every format: bodies, MIME types, filenames,
large invoices, and error paths.
"""

from __future__ import annotations

import csv
import io
import uuid

from app.schemas.extraction import ExtractedLineItem
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db


async def processed_invoice_id(api_client) -> str:  # noqa: F811
    accepted = await process_file(api_client)
    status = (await api_client.get(accepted["status_url"])).json()["data"]
    return status["invoice_id"]


class TestExportFormats:
    async def test_json_export_returns_validated_object(self, api_client):  # noqa: F811
        invoice_id = await processed_invoice_id(api_client)

        response = await api_client.get(f"/api/v1/invoices/{invoice_id}/export")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert (
            response.headers["content-disposition"]
            == 'attachment; filename="invoice_INV-001.json"'
        )
        payload = response.json()
        assert payload["schema_version"] == "1.0"
        assert payload["invoice"]["invoice_number"] == "INV-001"
        assert payload["invoice"]["totals"]["grand_total"] == 18.9
        # The validated object, not the raw LLM response: derived unit price present
        assert payload["line_items"][0]["unit_price"] == 9.45
        assert payload["validation"]["status"] == "VALIDATED"
        assert payload["validation"]["review_required"] is False

    async def test_txt_export(self, api_client):  # noqa: F811
        invoice_id = await processed_invoice_id(api_client)

        response = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "txt"}
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert (
            response.headers["content-disposition"]
            == 'attachment; filename="invoice_INV-001.txt"'
        )
        body = response.text
        assert "Vendor\n------\nAcme Corp" in body
        assert "Invoice Number:\nINV-001" in body
        assert "Grand Total:\n18.90 USD" in body
        assert "Review Required:\nNo" in body

    async def test_csv_export(self, api_client):  # noqa: F811
        invoice_id = await processed_invoice_id(api_client)

        response = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "csv"}
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert (
            response.headers["content-disposition"]
            == 'attachment; filename="invoice_INV-001_items.csv"'
        )
        rows = list(csv.reader(io.StringIO(response.text)))
        assert rows[0] == ["Description", "Quantity", "Unit Price", "Line Total",
                           "Tax Rate (%)", "UPC"]
        assert rows[1][0] == "Blue Widget"
        assert rows[1][2] == "9.45"  # derived unit price

    async def test_large_invoice_exports(self, api_client, app):  # noqa: F811
        from app.api.v1.invoices import get_pipeline

        big = extracted_invoice(
            line_items=[
                ExtractedLineItem(description=f"Item {i:03d}", quantity=1.0,
                                  unit_price=2.5, line_total=2.5)
                for i in range(120)
            ],
            subtotal=300.0, grand_total=300.0,
        )
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(big)
        )
        accepted = await process_file(
            api_client, content=build_pdf(["big invoice " + "pad " * 300]), filename="big.pdf"
        )
        status = (await api_client.get(accepted["status_url"])).json()["data"]

        json_body = (
            await api_client.get(f"/api/v1/invoices/{status['invoice_id']}/export")
        ).json()
        assert len(json_body["line_items"]) == 120
        assert json_body["line_items"][119]["position"] == 120

        csv_rows = list(csv.reader(io.StringIO(
            (await api_client.get(
                f"/api/v1/invoices/{status['invoice_id']}/export", params={"format": "csv"}
            )).text
        )))
        assert len(csv_rows) == 121  # header + 120 items


class TestPdiExport:
    """
    Proves the full loop this milestone exists for: an extracted
    product_code survives validation, gets persisted into
    invoice_items.product_sku, and comes back out through the PDI
    formatter — with zero code in this test knowing about PDI's byte
    layout (that's exercised in tests/test_export_service.py).
    """

    async def test_product_code_flows_from_extraction_to_pdi_export(
        self, api_client, app  # noqa: F811
    ):
        from app.api.v1.invoices import get_pipeline

        sample_invoice = extracted_invoice(
            line_items=[
                ExtractedLineItem(
                    description="NORTHWIND LAGER 12PK CAN", product_code="999000000015",
                    quantity=3.0, unit_price=21.95, line_total=65.85,
                )
            ],
            subtotal=65.85, grand_total=65.85,
        )
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(sample_invoice)
        )
        accepted = await process_file(
            api_client, content=build_pdf(["sample invoice " + "pad " * 300]),
            filename="sample-invoice.pdf",
        )
        status = (await api_client.get(accepted["status_url"])).json()["data"]

        # The product_code is visible through the existing JSON export slot
        # too (it was already wired to product_sku; this is that column
        # finally getting populated, not a new export field).
        json_export = (
            await api_client.get(f"/api/v1/invoices/{status['invoice_id']}/export")
        ).json()
        assert json_export["line_items"][0]["sku_upc"] == "999000000015"

        response = await api_client.get(
            f"/api/v1/invoices/{status['invoice_id']}/export", params={"format": "pdi"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert (
            response.headers["content-disposition"]
            == 'attachment; filename="invoice_INV-001_pdi.txt"'
        )

        lines = response.text.splitlines()
        assert lines[0].startswith("AMOUNT ")
        detail_line = lines[1]
        assert len(detail_line) == 70
        assert detail_line[0] == "B"
        assert detail_line[1:12] == "99900000001"  # 12-digit UPC, check digit stripped
        assert detail_line[12:37].strip() == "NORTHWIND LAGER 12PK CAN"
        assert detail_line[58:62] == "0003"  # quantity 3

    async def test_missing_product_code_produces_blank_item_code(
        self, api_client  # noqa: F811
    ):
        # Default fixture line item has no product_code — proves the
        # formatter degrades safely rather than failing when OCR/the model
        # found no code on the document.
        invoice_id = await processed_invoice_id(api_client)
        response = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )
        detail_line = response.text.splitlines()[1]
        assert detail_line[1:12] == "00000" + " " * 6

    async def test_pdi_export_omits_trailer_records(self, api_client):  # noqa: F811
        invoice_id = await processed_invoice_id(api_client)
        response = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )
        assert "CFUE" not in response.text
        assert "CPPT" not in response.text

    async def test_existing_exports_unaffected_by_pdi_addition(self, api_client):  # noqa: F811
        # Regression guard: adding format=pdi must not perturb json/txt/csv.
        invoice_id = await processed_invoice_id(api_client)
        for fmt, content_type in [
            ("json", "application/json"), ("txt", "text/plain"), ("csv", "text/csv"),
        ]:
            response = await api_client.get(
                f"/api/v1/invoices/{invoice_id}/export", params={"format": fmt}
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(content_type)

    async def test_review_required_invoice_cannot_be_exported_as_pdi(
        self, api_client, app  # noqa: F811
    ):
        # PDI import is intended to feed the target system with minimal
        # human review — an invoice that failed validation must not reach
        # it in this format, even though json/txt/csv remain available.
        from app.api.v1.invoices import get_pipeline

        unreviewed = extracted_invoice(
            line_items=[ExtractedLineItem(
                description="Mismatched item", quantity=2.0,
                unit_price=5.0, line_total=18.9,  # 2 x 5.0 != 18.9
            )],
        )
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(unreviewed)
        )
        accepted = await process_file(
            api_client, content=build_pdf(["unreviewed invoice " + "pad " * 300]),
            filename="unreviewed.pdf",
        )
        status = (await api_client.get(accepted["status_url"])).json()["data"]
        assert status["status"] == "REVIEW_REQUIRED"

        response = await api_client.get(
            f"/api/v1/invoices/{status['invoice_id']}/export", params={"format": "pdi"}
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["error_code"] == "ERR_VALIDATION_FAILED"
        assert body["error"]["detail"]["status"] == "REVIEW_REQUIRED"

        # The other formats remain available for a reviewer to inspect why.
        for fmt in ["json", "txt", "csv"]:
            ok = await api_client.get(
                f"/api/v1/invoices/{status['invoice_id']}/export", params={"format": fmt}
            )
            assert ok.status_code == 200


class TestExportErrors:
    async def test_unknown_invoice_is_404(self, api_client):  # noqa: F811
        response = await api_client.get(f"/api/v1/invoices/{uuid.uuid4()}/export")
        assert response.status_code == 404
        assert response.json()["error"]["error_code"] == "ERR_NOT_FOUND"

    async def test_invalid_format_is_422(self, api_client):  # noqa: F811
        invoice_id = await processed_invoice_id(api_client)
        response = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "xml"}
        )
        assert response.status_code == 422
