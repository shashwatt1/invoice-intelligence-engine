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


async def confirm_mapping(api_client, invoice_id, item_code="99900000001", units=12):  # noqa: F811
    """Confirm a units-per-case mapping, as the review UI does.

    format=pdi is blocked until every product on the invoice has one.
    """
    response = await api_client.post(
        f"/api/v1/invoices/{invoice_id}/case-mappings",
        json={"mappings": [{"item_code": item_code, "units_per_case": units}]},
    )
    assert response.status_code == 200, response.text
    return response


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

        await confirm_mapping(api_client, status["invoice_id"])
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

    async def test_cppt_not_emitted_even_with_tax_amount_present(
        self, api_client, app  # noqa: F811
    ):
        # A prior CPPT-from-tax_amount mapping was reverted: cross-file
        # analysis of real accepted PDI files showed CPPT tracks
        # cigarette-carton volume, not a generic tax total
        # (docs/PDI_DATA_CONTRACT.md §2.2) — so a present tax_amount must
        # NOT produce a CPPT trailer through the real pipeline.
        from app.api.v1.invoices import get_pipeline

        taxed_invoice = extracted_invoice(
            line_items=[
                ExtractedLineItem(
                    description="Blue Widget", quantity=2.0, unit_price=9.45, line_total=18.9,
                )
            ],
            subtotal=18.9, tax_amount=7.78, grand_total=26.68,
        )
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(taxed_invoice)
        )
        accepted = await process_file(
            api_client, content=build_pdf(["taxed invoice " + "pad " * 300]),
            filename="taxed-invoice.pdf",
        )
        status = (await api_client.get(accepted["status_url"])).json()["data"]

        response = await api_client.get(
            f"/api/v1/invoices/{status['invoice_id']}/export", params={"format": "pdi"}
        )
        assert response.status_code == 200
        assert "CPPT" not in response.text
        assert "CFUE" not in response.text

    async def test_repeated_pdi_export_is_byte_identical(self, api_client):  # noqa: F811
        # Formatter is frozen pending real PDI validation — the property
        # that validation depends on is that re-exporting the same,
        # already-persisted invoice never changes a single byte.
        invoice_id = await processed_invoice_id(api_client)
        first = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )
        second = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )
        assert first.status_code == second.status_code == 200
        assert first.text == second.text

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

    async def test_return_invoice_produces_negative_pdi_records(
        self, api_client, app  # noqa: F811
    ):
        # Return/credit invoices carry a negative grand_total end-to-end
        # (extraction and validation preserve the printed sign — see
        # _pdi_is_return in export_service.py); the PDI formatter must flip
        # the sign character while keeping every digit field magnitude-only.
        from app.api.v1.invoices import get_pipeline

        return_invoice = extracted_invoice(
            line_items=[
                ExtractedLineItem(
                    description="NORTHWIND LAGER 12PK CAN", product_code="999000000015",
                    quantity=3.0, unit_price=-15.56, line_total=-46.68,
                )
            ],
            subtotal=-46.68, grand_total=-46.68,
        )
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(return_invoice)
        )
        accepted = await process_file(
            api_client, content=build_pdf(["return invoice " + "pad " * 300]),
            filename="return-invoice.pdf",
        )
        status = (await api_client.get(accepted["status_url"])).json()["data"]

        await confirm_mapping(api_client, status["invoice_id"])
        response = await api_client.get(
            f"/api/v1/invoices/{status['invoice_id']}/export", params={"format": "pdi"}
        )
        # A 200 here (rather than the 422 from the validation-status gate)
        # confirms the return invoice was persisted with status="VALIDATED",
        # not just that the math checks passed.
        assert response.status_code == 200

        lines = response.text.splitlines()
        header, detail_line = lines[0], lines[1]
        assert header[23] == "-"  # header sign position
        assert "-" not in header[24:]  # amount digits stay magnitude-only
        assert detail_line[57] == "-"  # detail-line sign position
        assert detail_line[37:43] == "0" * 6  # unknown product ref, left zero
        assert detail_line[43:49] == "001556"  # case cost: magnitude only, sign is [57]
        assert detail_line[49:53] == "0100"  # confirmed constant marker
        assert detail_line[53:57] == "0012"  # units per case, from the confirmed mapping
        assert detail_line[58:62] == "0003"  # quantity stays magnitude-only
        assert detail_line[62:70] == "0" * 8  # cost tail: placeholder, never fabricated

    async def test_review_required_invoice_with_items_can_be_exported_as_pdi(
        self, api_client, app  # noqa: F811
    ):
        # REVIEW_REQUIRED invoices are eligible as long as they have usable
        # extracted data — the frontend is responsible for confirming with
        # the user first; the backend's job is just to allow the request
        # once that's happened. See export_service.pdi_export_eligibility.
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
        assert response.status_code == 200
        assert response.text.splitlines()[0].startswith("AMOUNT ")

        # The other formats remain available too.
        for fmt in ["json", "txt", "csv"]:
            ok = await api_client.get(
                f"/api/v1/invoices/{status['invoice_id']}/export", params={"format": fmt}
            )
            assert ok.status_code == 200

    async def test_review_required_invoice_without_items_cannot_be_exported(
        self, api_client, app  # noqa: F811
    ):
        # The only remaining block: no usable extracted data at all.
        from app.api.v1.invoices import get_pipeline

        empty = extracted_invoice(line_items=[])
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(empty)
        )
        accepted = await process_file(
            api_client, content=build_pdf(["empty invoice " + "pad " * 300]),
            filename="empty.pdf",
        )
        status = (await api_client.get(accepted["status_url"])).json()["data"]
        assert status["status"] == "REVIEW_REQUIRED"

        response = await api_client.get(
            f"/api/v1/invoices/{status['invoice_id']}/export", params={"format": "pdi"}
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["error_code"] == "ERR_VALIDATION_FAILED"
        assert "no extracted line items" in body["error"]["message"]

    async def test_invoice_detail_exposes_pdi_export_eligibility(
        self, api_client, app  # noqa: F811
    ):
        # The frontend reads these fields instead of re-deriving the rule,
        # so it can never drift from what the export endpoint actually does.
        from app.api.v1.invoices import get_pipeline

        # VALIDATED: allowed, no confirmation needed.
        invoice_id = await processed_invoice_id(api_client)
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["status"] == "VALIDATED"
        assert detail["pdi_export_allowed"] is True
        assert detail["pdi_export_requires_confirmation"] is False
        assert detail["pdi_export_blocked_reason"] is None

        # REVIEW_REQUIRED with items: allowed, but flagged for confirmation.
        unreviewed = extracted_invoice(
            line_items=[ExtractedLineItem(
                description="Mismatched item", quantity=2.0, unit_price=5.0, line_total=18.9,
            )],
        )
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(unreviewed)
        )
        accepted = await process_file(
            api_client, content=build_pdf(["unreviewed invoice " + "pad " * 300]),
            filename="unreviewed2.pdf",
        )
        status = (await api_client.get(accepted["status_url"])).json()["data"]
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert detail["status"] == "REVIEW_REQUIRED"
        assert detail["pdi_export_allowed"] is True
        assert detail["pdi_export_requires_confirmation"] is True
        assert detail["pdi_export_blocked_reason"] is None

        # REVIEW_REQUIRED with no items: blocked, with a reason.
        empty = extracted_invoice(line_items=[])
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(empty)
        )
        accepted = await process_file(
            api_client, content=build_pdf(["empty invoice " + "pad " * 300]),
            filename="empty2.pdf",
        )
        status = (await api_client.get(accepted["status_url"])).json()["data"]
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert detail["pdi_export_allowed"] is False
        assert detail["pdi_export_requires_confirmation"] is False
        assert detail["pdi_export_blocked_reason"]


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
