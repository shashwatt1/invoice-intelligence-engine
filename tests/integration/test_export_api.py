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
                           "Tax Rate (%)", "SKU/UPC"]
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
