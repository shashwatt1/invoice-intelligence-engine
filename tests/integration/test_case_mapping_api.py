"""
tests/integration/test_case_mapping_api.py — Case mapping against real Postgres.

Drives the real API: process an invoice, see it blocked for PDI export
until its products are mapped, confirm the mappings, export, then process
a SECOND invoice with the same UPC and confirm it is never asked about
again. That last case is the whole point of the mapping table.
"""

from __future__ import annotations

from app.schemas.extraction import ExtractedLineItem
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import approve_all_pending, requires_db
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db

UPC = "611269321210"          # as printed, 12 digits
NORMALIZED = "61126932121"    # check digit dropped — the mapping key


async def _process(api_client, app, items, filename, pad):  # noqa: F811
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(
            extracted_invoice(line_items=items, subtotal=50.20, grand_total=50.20)
        )
    )
    accepted = await process_file(
        api_client, content=build_pdf([pad + " pad " * 300]), filename=filename
    )
    status = (await api_client.get(accepted["status_url"])).json()["data"]
    return status["invoice_id"]


def _line(upc: str = UPC, pack: str | None = "24/12OZ"):
    return ExtractedLineItem(
        description="RB COCONUT 24/12OZ", product_code=upc, pack_size=pack,
        quantity=1.0, unit_price=50.20, line_total=50.20,
    )


class TestUnmappedProduct:
    async def test_detail_reports_the_product_as_needing_a_mapping(
        self, api_client, app  # noqa: F811
    ):
        invoice_id = await _process(api_client, app, [_line()], "unmapped.pdf", "unmapped")
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]

        assert detail["pdi_export_allowed"] is False
        assert "units-per-case" in detail["pdi_export_blocked_reason"]
        [row] = detail["case_mappings"]
        assert row["mapped"] is False
        assert row["item_code"] == NORMALIZED
        assert row["units_per_case"] is None
        assert row["suggested_units_per_case"] == 24    # offered, not applied

    async def test_pdi_export_is_refused_while_unmapped(self, api_client, app):  # noqa: F811
        invoice_id = await _process(api_client, app, [_line()], "refuse.pdf", "refuse")
        response = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["error_code"] == "ERR_VALIDATION_FAILED"
        assert body["error"]["detail"]["unmapped_item_codes"] == [NORMALIZED]


class TestConfirmingAMapping:
    async def test_confirmation_proposes_and_approval_unblocks_the_export(
        self, api_client, app, db_session  # noqa: F811
    ):
        invoice_id = await _process(api_client, app, [_line()], "confirm.pdf", "confirm")

        response = await api_client.post(
            f"/api/v1/invoices/{invoice_id}/case-mappings",
            json={"mappings": [
                {"item_code": NORMALIZED, "units_per_case": 24,
                 "description": "RB COCONUT 24/12OZ"}
            ]},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        # Confirm submits a proposal. It does NOT unblock anything.
        assert data["saved"] == 1
        assert data["pdi_export_allowed"] is False
        assert data["case_mappings"][0]["mapped"] is False
        assert data["case_mappings"][0]["pending_value"] == 24

        assert await approve_all_pending(db_session) == 1
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["pdi_export_allowed"] is True
        assert detail["case_mappings"][0]["mapped"] is True
        assert detail["case_mappings"][0]["units_per_case"] == 24

    async def test_saved_mapping_is_used_by_the_edi_immediately(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await _process(api_client, app, [_line()], "immediate.pdf", "immediate")
        await api_client.post(
            f"/api/v1/invoices/{invoice_id}/case-mappings",
            json={"mappings": [{"item_code": NORMALIZED, "units_per_case": 24}]},
        )
        await approve_all_pending(db_session)
        response = await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )
        assert response.status_code == 200
        line = response.text.splitlines()[1]
        assert line[53:57] == "0024"          # units per case, from the APPROVED mapping
        assert line[43:49] == "005020"        # case cost unchanged
        assert len(line) == 70

    async def test_dashed_upc_confirms_the_same_product(self, api_client, app, db_session):  # noqa: F811
        # A user pasting the printed, dashed UPC must hit the same key the
        # formatter uses, or the mapping would never be found again.
        invoice_id = await _process(api_client, app, [_line()], "dashed.pdf", "dashed")
        await api_client.post(
            f"/api/v1/invoices/{invoice_id}/case-mappings",
            json={"mappings": [{"item_code": "6-11269-32121-0", "units_per_case": 24}]},
        )
        await approve_all_pending(db_session)
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["pdi_export_allowed"] is True

    async def test_reconfirming_updates_in_place_without_duplicating(
        self, api_client, app, db_session  # noqa: F811
    ):
        invoice_id = await _process(api_client, app, [_line()], "reconfirm.pdf", "reconfirm")
        url = f"/api/v1/invoices/{invoice_id}/case-mappings"
        await api_client.post(url, json={"mappings": [
            {"item_code": NORMALIZED, "units_per_case": 12}]})
        await approve_all_pending(db_session)
        second = await api_client.post(url, json={"mappings": [
            {"item_code": NORMALIZED, "units_per_case": 24}]})
        assert second.status_code == 200
        await approve_all_pending(db_session)

        line = (await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )).text.splitlines()[1]
        assert line[53:57] == "0024"          # the corrected value, one mapping row

    async def test_multiple_products_resolved_in_one_request(self, api_client, app, db_session):  # noqa: F811
        items = [
            _line(),
            ExtractedLineItem(
                description="NESQ MILK", product_code="028000772123",
                quantity=1.0, unit_price=18.96, line_total=18.96,
            ),
        ]
        invoice_id = await _process(api_client, app, items, "multi.pdf", "multi")
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert sum(1 for r in detail["case_mappings"] if not r["mapped"]) == 2

        response = await api_client.post(
            f"/api/v1/invoices/{invoice_id}/case-mappings",
            json={"mappings": [
                {"item_code": NORMALIZED, "units_per_case": 24},
                {"item_code": "02800077212", "units_per_case": 12},
            ]},
        )
        assert response.json()["data"]["saved"] == 2
        assert await approve_all_pending(db_session) == 2
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["pdi_export_allowed"] is True
        assert all(row["mapped"] for row in detail["case_mappings"])


class TestFutureInvoicesReuseTheMapping:
    async def test_same_upc_on_a_later_invoice_is_never_asked_about_again(
        self, api_client, app, db_session  # noqa: F811
    ):
        first = await _process(api_client, app, [_line()], "first.pdf", "first invoice")
        await api_client.post(
            f"/api/v1/invoices/{first}/case-mappings",
            json={"mappings": [{"item_code": NORMALIZED, "units_per_case": 24}]},
        )
        await approve_all_pending(db_session)

        # A completely separate invoice carrying the same product.
        second = await _process(api_client, app, [_line()], "second.pdf", "second invoice")
        detail = (await api_client.get(f"/api/v1/invoices/{second}")).json()["data"]

        assert detail["pdi_export_allowed"] is True      # no prompt
        [row] = detail["case_mappings"]
        assert row["mapped"] is True
        assert row["units_per_case"] == 24

        line = (await api_client.get(
            f"/api/v1/invoices/{second}/export", params={"format": "pdi"}
        )).text.splitlines()[1]
        assert line[53:57] == "0024"

    async def test_mapping_survives_deletion_of_the_invoice_it_came_from(
        self, api_client, app, db_session  # noqa: F811
    ):
        first = await _process(api_client, app, [_line()], "gone.pdf", "will be deleted")
        await api_client.post(
            f"/api/v1/invoices/{first}/case-mappings",
            json={"mappings": [{"item_code": NORMALIZED, "units_per_case": 24}]},
        )
        await approve_all_pending(db_session)
        assert (await api_client.delete(f"/api/v1/invoices/{first}")).status_code == 200

        second = await _process(api_client, app, [_line()], "after.pdf", "after deletion")
        detail = (await api_client.get(f"/api/v1/invoices/{second}")).json()["data"]
        assert detail["case_mappings"][0]["units_per_case"] == 24


class TestValidation:
    async def test_zero_and_negative_units_are_rejected(self, api_client, app):  # noqa: F811
        invoice_id = await _process(api_client, app, [_line()], "invalid.pdf", "invalid")
        for bad in (0, -5, 10000):
            response = await api_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": NORMALIZED, "units_per_case": bad}]},
            )
            assert response.status_code == 422, bad

    async def test_item_code_without_digits_is_rejected(self, api_client, app):  # noqa: F811
        invoice_id = await _process(api_client, app, [_line()], "nodigits.pdf", "nodigits")
        response = await api_client.post(
            f"/api/v1/invoices/{invoice_id}/case-mappings",
            json={"mappings": [{"item_code": "ABC", "units_per_case": 24}]},
        )
        assert response.status_code == 422

    async def test_unknown_invoice_is_404(self, api_client):  # noqa: F811
        import uuid

        response = await api_client.post(
            f"/api/v1/invoices/{uuid.uuid4()}/case-mappings",
            json={"mappings": [{"item_code": NORMALIZED, "units_per_case": 24}]},
        )
        assert response.status_code == 404


class TestCorrectingAMappingThroughTheApi:
    """
    The UI's Update action posts to the same endpoint as the initial
    confirmation. This proves a wrong value can be corrected without
    touching the database by hand, and that the correction propagates to
    later invoices — the BeatBox situation, where PDI received units per
    case 1 and the mapping had to be repaired.
    """

    async def test_a_wrong_value_can_be_corrected_and_reaches_the_edi(
        self, api_client, app, db_session  # noqa: F811
    ):
        invoice_id = await _process(api_client, app, [_line()], "wrong.pdf", "wrong")
        url = f"/api/v1/invoices/{invoice_id}/case-mappings"

        # Approved wrong — the situation the correction path exists for.
        await api_client.post(url, json={"mappings": [
            {"item_code": NORMALIZED, "units_per_case": 1}]})
        await approve_all_pending(db_session)
        line = (await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )).text.splitlines()[1]
        assert line[53:57] == "0001"
        cost_before = line[43:49]

        # The operator proposes a correction; a reviewer approves it.
        response = await api_client.post(url, json={"mappings": [
            {"item_code": NORMALIZED, "units_per_case": 12}]})
        assert response.status_code == 200
        assert response.json()["data"]["case_mappings"][0]["pending_value"] == 12
        assert response.json()["data"]["case_mappings"][0]["units_per_case"] == 1  # not yet
        await approve_all_pending(db_session)

        corrected = (await api_client.get(
            f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"}
        )).text.splitlines()[1]
        assert corrected[53:57] == "0012"
        assert corrected[43:49] == cost_before   # cost bytes untouched

    async def test_the_correction_is_what_a_later_invoice_inherits(
        self, api_client, app, db_session  # noqa: F811
    ):
        first = await _process(api_client, app, [_line()], "c-first.pdf", "correction first")
        url = f"/api/v1/invoices/{first}/case-mappings"
        await api_client.post(url, json={"mappings": [
            {"item_code": NORMALIZED, "units_per_case": 1}]})
        await approve_all_pending(db_session)
        await api_client.post(url, json={"mappings": [
            {"item_code": NORMALIZED, "units_per_case": 12}]})
        await approve_all_pending(db_session)

        second = await _process(api_client, app, [_line()], "c-second.pdf", "correction second")
        detail = (await api_client.get(f"/api/v1/invoices/{second}")).json()["data"]

        assert detail["pdi_export_allowed"] is True     # no prompt
        [row] = detail["case_mappings"]
        assert row["units_per_case"] == 12              # the corrected value
        assert row["suggestion_source"] == "database"
        assert row["suggestion_candidates"] == []


class TestAmbiguousProductsInTheReviewApi:
    async def test_candidates_are_returned_for_an_ambiguous_description(
        self, api_client, app  # noqa: F811
    ):
        item = ExtractedLineItem(
            description="BUSCH 4/6/16OZ CAN", product_code=UPC, pack_size=None,
            quantity=1.0, unit_price=50.20, line_total=50.20,
        )
        invoice_id = await _process(api_client, app, [item], "ambig.pdf", "ambiguous")
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]

        [row] = detail["case_mappings"]
        assert row["mapped"] is False
        assert row["suggestion_source"] == "description_ambiguous"
        assert row["suggestion_candidates"] == [4, 24]
        assert detail["pdi_export_allowed"] is False    # still must be confirmed
