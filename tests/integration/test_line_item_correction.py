"""
tests/integration/test_line_item_correction.py — manual correction of a
line item, against real Postgres.

Extraction leaves a few values unassociated when OCR interleaves the
description and price columns; the model reports those as null rather
than guessing, and the export gate blocks. This is how a person supplies
them without reprocessing the document.

The gate must open only because the data became complete — never because
the check was relaxed.
"""

from __future__ import annotations

import uuid

from app.schemas.extraction import ExtractedLineItem
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db

UPC = "062067051463"
NORMALIZED = "06206705146"


async def process(api_client, app, items, filename, pad, **totals):  # noqa: F811
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(
            extracted_invoice(line_items=items, **totals)
        )
    )
    accepted = await process_file(
        api_client, content=build_pdf([pad + " pad " * 300]), filename=filename
    )
    status = (await api_client.get(accepted["status_url"])).json()["data"]
    return status["invoice_id"]


def line(description="LAB 30 PACK CANS", upc=UPC, unit_price=22.70,
         quantity=1.0, line_total=22.70):
    return ExtractedLineItem(
        description=description, product_code=upc, quantity=quantity,
        unit_price=unit_price, line_total=line_total,
    )


async def patch(api_client, invoice_id, sort_order, **fields):  # noqa: F811
    return await api_client.patch(
        f"/api/v1/invoices/{invoice_id}/items/{sort_order}", json=fields
    )


class TestCorrectingAValue:
    async def test_a_null_unit_price_can_be_supplied(self, api_client, app):  # noqa: F811
        # The model could not associate this row's price and said so.
        invoice_id = await process(
            api_client, app, [line(unit_price=None, line_total=None)],
            "nullcost.pdf", "null cost", subtotal=22.70, grand_total=22.70,
        )
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["line_items"][0]["unit_price"] is None

        response = await patch(api_client, invoice_id, 0, unit_price="22.70")
        assert response.status_code == 200, response.text
        data = response.json()["data"]

        assert data["item"]["unit_price"] == 22.70
        assert data["item"]["corrected_fields"] == ["unit_price"]

    async def test_quantity_can_be_corrected(self, api_client, app):  # noqa: F811
        # The PLAT SELTZ case: quantity detached from its row by OCR.
        invoice_id = await process(
            api_client, app, [line(quantity=1.0, unit_price=34.20, line_total=34.95)],
            "qty.pdf", "quantity", subtotal=69.90, grand_total=69.90,
        )
        data = (await patch(api_client, invoice_id, 0, quantity="2")).json()["data"]

        assert data["item"]["quantity"] == 2.0
        assert data["item"]["corrected_fields"] == ["quantity"]

    async def test_line_total_can_be_corrected(self, api_client, app):  # noqa: F811
        invoice_id = await process(
            api_client, app, [line(line_total=34.95)],
            "total.pdf", "line total", subtotal=69.90, grand_total=69.90,
        )
        data = (await patch(api_client, invoice_id, 0, line_total="69.90")).json()["data"]

        assert data["item"]["line_total"] == 69.90
        assert data["item"]["corrected_fields"] == ["line_total"]

    async def test_several_fields_at_once_are_all_recorded(self, api_client, app):  # noqa: F811
        invoice_id = await process(
            api_client, app, [line(quantity=1.0, line_total=34.95)],
            "multi.pdf", "multi", subtotal=69.90, grand_total=69.90,
        )
        data = (await patch(
            api_client, invoice_id, 0, quantity="2", line_total="69.90"
        )).json()["data"]

        assert data["item"]["corrected_fields"] == ["line_total", "quantity"]

    async def test_corrections_accumulate_across_requests(self, api_client, app):  # noqa: F811
        invoice_id = await process(
            api_client, app, [line(unit_price=None, line_total=None)],
            "accum.pdf", "accumulate", subtotal=45.40, grand_total=45.40,
        )
        await patch(api_client, invoice_id, 0, unit_price="22.70")
        data = (await patch(api_client, invoice_id, 0, quantity="2")).json()["data"]

        assert data["item"]["corrected_fields"] == ["quantity", "unit_price"]


class TestProvenance:
    async def test_an_untouched_line_is_never_marked_corrected(self, api_client, app):  # noqa: F811
        invoice_id = await process(
            api_client, app,
            [line("LAB 30 PACK CANS"), line("LAB 12/24 OZ CAN", "062067051623")],
            "prov.pdf", "provenance", subtotal=45.40, grand_total=45.40,
        )
        await patch(api_client, invoice_id, 0, unit_price="22.70")

        second = (await patch(api_client, invoice_id, 1, quantity="3")).json()["data"]
        assert second["item"]["corrected_fields"] == ["quantity"]   # not unit_price

    async def test_correcting_one_field_leaves_the_others_extracted(
        self, api_client, app  # noqa: F811
    ):
        invoice_id = await process(
            api_client, app, [line(quantity=3.0, unit_price=20.30, line_total=62.70)],
            "one.pdf", "one field", subtotal=62.70, grand_total=62.70,
        )
        data = (await patch(api_client, invoice_id, 0, unit_price="21.00")).json()["data"]

        assert data["item"]["corrected_fields"] == ["unit_price"]
        assert data["item"]["quantity"] == 3.0        # untouched
        assert data["item"]["line_total"] == 62.70    # untouched


class TestRevalidation:
    async def test_validation_is_re_run_and_the_result_returned(self, api_client, app):  # noqa: F811
        invoice_id = await process(
            api_client, app, [line(unit_price=None, line_total=None)],
            "reval.pdf", "revalidate", subtotal=22.70, grand_total=22.70,
        )
        data = (await patch(api_client, invoice_id, 0,
                            unit_price="22.70", line_total="22.70")).json()["data"]

        assert data["status"] in ("VALIDATED", "REVIEW_REQUIRED")
        assert isinstance(data["composite_confidence"], float)
        assert isinstance(data["failed_checks"], int)

    async def test_the_stored_report_is_replaced_not_appended_in_the_ui(
        self, api_client, app  # noqa: F811
    ):
        invoice_id = await process(
            api_client, app, [line(unit_price=None, line_total=None)],
            "report.pdf", "report", subtotal=22.70, grand_total=22.70,
        )
        await patch(api_client, invoice_id, 0, unit_price="22.70", line_total="22.70")

        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        report = detail["validation_report"]
        assert report is not None
        # The detail endpoint reads the latest entry per stage, so the UI
        # shows the post-correction judgement.
        assert detail["status"] == report["decision"]

    async def test_a_correction_that_fixes_the_math_clears_the_failure(
        self, api_client, app  # noqa: F811
    ):
        # quantity x unit_price != line_total, with nothing to explain it.
        invoice_id = await process(
            api_client, app, [line(quantity=1.0, unit_price=22.70, line_total=45.40)],
            "math.pdf", "math", subtotal=45.40, grand_total=45.40,
        )
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        before = sum(1 for c in detail["validation_report"]["checks"]
                     if c["status"] == "FAILED")
        assert before > 0

        data = (await patch(api_client, invoice_id, 0, quantity="2")).json()["data"]
        assert data["failed_checks"] < before


class TestTheExportGate:
    async def test_a_supplied_cost_unblocks_the_missing_cost_gate(
        self, api_client, app  # noqa: F811
    ):
        invoice_id = await process(
            api_client, app, [line(unit_price=None, line_total=None)],
            "gate.pdf", "gate", subtotal=22.70, grand_total=22.70,
        )
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["pdi_export_allowed"] is False
        assert "no extracted unit cost" in detail["pdi_export_blocked_reason"]

        data = (await patch(api_client, invoice_id, 0, unit_price="22.70")).json()["data"]

        # The cost gate is satisfied; the mapping gate now takes over —
        # the export opens only when every gate passes, never because one
        # was relaxed.
        assert "no extracted unit cost" not in (data["pdi_export_blocked_reason"] or "")
        assert "units-per-case" in data["pdi_export_blocked_reason"]

    async def test_the_export_opens_once_cost_and_mapping_are_both_resolved(
        self, api_client, app  # noqa: F811
    ):
        invoice_id = await process(
            api_client, app, [line(unit_price=None, line_total=None)],
            "open.pdf", "open", subtotal=22.70, grand_total=22.70,
        )
        await patch(api_client, invoice_id, 0, unit_price="22.70")
        await api_client.post(
            f"/api/v1/invoices/{invoice_id}/case-mappings",
            json={"mappings": [{"item_code": NORMALIZED, "units_per_case": 30}]},
        )

        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["pdi_export_allowed"] is True

        export = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )
        assert export.status_code == 200
        detail_line = export.text.splitlines()[1]
        assert detail_line[43:49] == "002270"   # the corrected cost
        assert detail_line[53:57] == "0030"
        assert len(detail_line) == 70


class TestRejection:
    async def test_an_empty_body_is_rejected(self, api_client, app):  # noqa: F811
        invoice_id = await process(
            api_client, app, [line()], "empty.pdf", "empty",
            subtotal=22.70, grand_total=22.70,
        )
        response = await patch(api_client, invoice_id, 0)
        assert response.status_code == 422

    async def test_a_negative_value_is_rejected(self, api_client, app):  # noqa: F811
        invoice_id = await process(
            api_client, app, [line()], "neg.pdf", "negative",
            subtotal=22.70, grand_total=22.70,
        )
        assert (await patch(api_client, invoice_id, 0, unit_price="-5")).status_code == 422
        assert (await patch(api_client, invoice_id, 0, quantity="-1")).status_code == 422

    async def test_units_per_case_is_not_editable_here(self, api_client, app):  # noqa: F811
        # It has its own endpoint and its own authority table.
        invoice_id = await process(
            api_client, app, [line()], "upc.pdf", "units",
            subtotal=22.70, grand_total=22.70,
        )
        response = await patch(api_client, invoice_id, 0, units_per_case="12")
        assert response.status_code == 422   # no recognised field supplied

    async def test_an_unknown_line_is_404(self, api_client, app):  # noqa: F811
        invoice_id = await process(
            api_client, app, [line()], "unknown.pdf", "unknown",
            subtotal=22.70, grand_total=22.70,
        )
        assert (await patch(api_client, invoice_id, 99, unit_price="1")).status_code == 404

    async def test_an_unknown_invoice_is_404(self, api_client):  # noqa: F811
        response = await patch(api_client, uuid.uuid4(), 0, unit_price="1")
        assert response.status_code == 404
