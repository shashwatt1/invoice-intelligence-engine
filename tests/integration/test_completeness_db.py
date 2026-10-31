"""
tests/integration/test_completeness_db.py — completeness through the real pipeline and the database.

A digital PDF whose text prints three product codes is "extracted" (by the offline fake) as only two lines. The
pipeline must persist that as REVIEW_REQUIRED with the critical check recorded and the export blocked; the
reviewer's missing line, added through the normal endpoint, makes the next validation COMPLETE and the block lifts
by itself. Typed charges and per-line fields persist and reload. Nothing here calls a model or an OCR service.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.v1.invoices import get_pipeline
from app.schemas.extraction import ExtractedCharge, ExtractedLineItem
from app.services.gtin import gtin_check_digit
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db


def upc(n: int) -> str:
    body = f"9{n:010d}"
    return body + gtin_check_digit(body)


def hyphen(code: str) -> str:
    return f"{code[0]}-{code[1:6]}-{code[6:11]}-{code[11]}"


CODES = [upc(1), upc(2), upc(3)]
FILLER = "\n".join(f"Terms and conditions line {n}: all sales are final unless stated otherwise." for n in range(1, 25))


def pdf_text(tag: str) -> str:
    """Printed rows for three products, plus filler so the file clears the upload minimum and a tag so each upload is unique."""
    rows = "\n".join(f"ITEM {i} {hyphen(c)} 1 12 10.00 10.00" for i, c in enumerate(CODES, 1))
    return f"SYN BEVERAGE CO INVOICE 100 ({tag})\n{rows}\nTotal 30.00\n{FILLER}"


def line(i: int) -> ExtractedLineItem:
    return ExtractedLineItem(description=f"ITEM {i}", product_code=CODES[i - 1], pack_size="20OZ PL 1/12",
                             quantity=1.0, unit_price=10.0, line_total=10.0, printed_units=12,
                             section_label="20OZ PL 1/12", confidence=0.95)


@pytest.fixture
def structure_as(app, api_client):  # noqa: F811
    def install(invoice):
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(invoice))
    return install


async def upload(client, name):
    accepted = await process_file(client, build_pdf([pdf_text(name)]), name)
    status = (await client.get(accepted["status_url"])).json()["data"]
    detail = (await client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
    return status["invoice_id"], detail


class TestAMissingLineIsCaughtBlockedAndFixable:
    async def test_two_of_three_printed_rows_is_incomplete_and_blocked_from_export(self, structure_as, api_client):  # noqa: F811
        structure_as(extracted_invoice(subtotal=20.0, grand_total=20.0, line_items=[line(1), line(2)]))
        _, detail = await upload(api_client, "short.pdf")
        assert detail["status"] == "REVIEW_REQUIRED"
        assert "COMPLETENESS_MISSING_LINES" in detail["blocking_checks"]
        report = detail["validation_report"]
        assert report["completeness"]["status"] == "INCOMPLETE"
        assert report["completeness"]["missing_codes"] == [CODES[2][:11]]
        assert report["confidence"]["composite"] <= 0.60 < report["confidence"]["composite_uncapped"]
        assert detail["pdi_export_allowed"] is False
        assert "critical checks" in detail["pdi_export_blocked_reason"]

    async def test_the_reviewer_adding_the_missing_row_makes_it_complete_and_lifts_the_block(self, structure_as, api_client):  # noqa: F811
        structure_as(extracted_invoice(subtotal=30.0, grand_total=30.0, line_items=[line(1), line(2)]))
        invoice_id, _ = await upload(api_client, "short2.pdf")
        added = await api_client.post(f"/api/v1/invoices/{invoice_id}/items", json={
            "description": "ITEM 3", "product_code": CODES[2], "pack_size": "20OZ PL 1/12", "quantity": "1",
            "unit_price": "10.00", "line_total": "10.00", "added_by": "x", "note": "missed by the extraction"})
        assert added.status_code in (200, 201), added.text
        detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert detail["validation_report"]["completeness"]["status"] == "COMPLETE"
        assert "COMPLETENESS_MISSING_LINES" not in detail["blocking_checks"]
        assert detail["pdi_export_blocked_reason"] is None or "critical checks" not in detail["pdi_export_blocked_reason"]

    async def test_a_complete_extraction_is_complete_and_not_blocked(self, structure_as, api_client):  # noqa: F811
        structure_as(extracted_invoice(subtotal=30.0, grand_total=30.0, line_items=[line(1), line(2), line(3)]))
        _, detail = await upload(api_client, "whole.pdf")
        assert detail["validation_report"]["completeness"]["status"] == "COMPLETE"
        assert detail["blocking_checks"] == []


class TestNewFieldsPersistAndReload:
    async def test_typed_charges_and_per_line_fields_round_trip(self, structure_as, api_client):  # noqa: F811
        structure_as(extracted_invoice(
            subtotal=30.0, tax_amount=0.0, grand_total=31.0,
            other_charges=[ExtractedCharge(kind="state_container_deposit", label="NY St Deposit @ .05", amount=1.0)],
            line_items=[line(1), line(2), line(3)]))
        _, detail = await upload(api_client, "charges.pdf")
        assert detail["other_charges"] == [{"kind": "state_container_deposit", "label": "NY St Deposit @ .05", "amount": 1.0}]
        assert detail["grand_total"] == 31.0
        checks = {c["name"]: c["status"] for c in detail["validation_report"]["checks"]}
        assert checks["GRAND_TOTAL_MATH"] == "PASSED"                     # 30.00 + 0.00 tax + 1.00 charge = 31.00
        row = next(r for r in detail["case_mappings"] if r["item_code"] == CODES[0][:11])
        assert row["pack_size"] == "20OZ PL 1/12"
        assert (row["suggested_units_per_case"], row["suggestion_source"]) == (12, "printed_units_and_pack")

    async def test_without_the_typed_charge_the_same_invoice_cannot_reconcile(self, structure_as, api_client):  # noqa: F811
        structure_as(extracted_invoice(subtotal=30.0, tax_amount=0.0, grand_total=31.0,
                                       line_items=[line(1), line(2), line(3)]))
        _, detail = await upload(api_client, "nocharge.pdf")
        assert "GRAND_TOTAL_MATH" in detail["blocking_checks"]
        assert detail["pdi_export_allowed"] is False


def test_the_decimal_amounts_of_charges_are_stored_exactly():
    assert Decimal("23.30") + Decimal("1821.18") == Decimal("1844.48")
