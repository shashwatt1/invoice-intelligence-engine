"""
tests/test_pdi_audit.py — decode-and-verify of the generated PDI file.

The audit exists to catch the failure mode that is invisible in a diff:
one field changing width shifts every later field, and the file still
looks like a plausible fixed-width record. These tests corrupt a
known-good file in exactly that way and assert the audit notices.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.services.export_service import build_pdi_export
from app.services.pdi_audit import B_RECORD_WIDTH, audit_pdi_export

RB_COCONUT = "61126932121"
NESQ_CHOCO = "02800077212"
UNITS = {RB_COCONUT: 24, NESQ_CHOCO: 12}


def make_invoice(*items: InvoiceItem, grand_total: str = "69.16") -> Invoice:
    invoice = Invoice(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        invoice_number="3376587",
        invoice_date=dt.date(2025, 10, 10),
        currency="USD",
        grand_total=Decimal(grand_total),
        status="VALIDATED",
    )
    invoice.items = list(items)
    return invoice


def make_item(sku, description, unit_price="50.20", quantity="1", sort_order=0):
    return InvoiceItem(
        description=description,
        product_sku=sku,
        quantity=Decimal(quantity),
        unit_price=Decimal(unit_price),
        line_total=Decimal(unit_price) * Decimal(quantity),
        sort_order=sort_order,
    )


@pytest.fixture
def good_invoice() -> Invoice:
    return make_invoice(
        make_item(RB_COCONUT, "RB COCONUT 24/12OZ", "50.20", sort_order=0),
        make_item(NESQ_CHOCO, "NESQ MILK 12/14 CHO", "18.96", sort_order=1),
    )


class TestCleanFilePasses:
    def test_a_generated_file_passes_every_check(self, good_invoice):
        audit = audit_pdi_export(build_pdi_export(good_invoice, UNITS), good_invoice)
        assert audit.ok, [c for c in audit.checks if not c.passed]
        assert audit.detail_count == 2
        assert audit.record_count == 3  # header + 2 details

    def test_fields_decode_to_the_invoice_values(self, good_invoice):
        audit = audit_pdi_export(build_pdi_export(good_invoice, UNITS), good_invoice)
        first = audit.records[1]
        decoded = {f.name: f.raw for f in first.fields}

        assert decoded["item_code"] == "61126932121"
        assert decoded["description"] == "RB COCONUT 24/12OZ".ljust(25)
        assert decoded["case_cost"] == "005020"
        assert decoded["marker"] == "0100"
        assert decoded["units_per_case"] == "0024"
        assert decoded["quantity"] == "0001"

    def test_audit_is_json_serializable(self, good_invoice):
        import json

        audit = audit_pdi_export(build_pdi_export(good_invoice, UNITS), good_invoice)
        assert json.loads(json.dumps(audit.to_dict()))["ok"] is True


class TestStructuralCorruption:
    """Each case is a real encoder bug expressed as a corrupted file."""

    def test_a_widened_field_shifts_the_rest_and_is_caught(self, good_invoice):
        text = build_pdi_export(good_invoice, UNITS)
        header, first, *rest = text.split("\r\n")
        # One extra description character: every later field slides right.
        corrupted = first[:12] + "X" + first[12:]
        audit = audit_pdi_export("\r\n".join([header, corrupted, *rest]))

        assert not audit.ok
        names = {c.name for c in audit.structural_failures}
        assert "b_records_are_70_chars" in names

    def test_a_shifted_field_that_keeps_the_length_is_still_caught(self, good_invoice):
        text = build_pdi_export(good_invoice, UNITS)
        header, first, *rest = text.split("\r\n")
        # Same 70 chars, but everything from the description onward slides
        # one byte right and the last byte falls off the end. The length
        # check cannot see this; the digit-only check can.
        corrupted = first[:12] + "X" + first[12:69]
        assert len(corrupted) == B_RECORD_WIDTH
        audit = audit_pdi_export("\r\n".join([header, corrupted, *rest]))

        assert not audit.ok
        assert "no_field_shifting" in {c.name for c in audit.structural_failures}

    def test_lf_line_endings_are_caught(self, good_invoice):
        text = build_pdi_export(good_invoice, UNITS).replace("\r\n", "\n")
        audit = audit_pdi_export(text)

        assert "line_endings_crlf_only" in {c.name for c in audit.structural_failures}

    def test_a_second_amount_record_is_caught(self, good_invoice):
        text = build_pdi_export(good_invoice, UNITS)
        audit = audit_pdi_export(text.replace("B6112", "AMOUNT 9999999   101025+000000001\r\nB6112", 1))

        assert "exactly_one_amount_record" in {c.name for c in audit.structural_failures}

    def test_a_broken_marker_is_caught(self, good_invoice):
        text = build_pdi_export(good_invoice, UNITS)
        header, first, *rest = text.split("\r\n")
        corrupted = first[:49] + "9999" + first[53:]
        audit = audit_pdi_export("\r\n".join([header, corrupted, *rest]))

        assert "marker_constant" in {c.name for c in audit.structural_failures}

    def test_zero_units_per_case_is_caught(self, good_invoice):
        text = build_pdi_export(good_invoice, UNITS)
        header, first, *rest = text.split("\r\n")
        corrupted = first[:53] + "0000" + first[57:]
        audit = audit_pdi_export("\r\n".join([header, corrupted, *rest]))

        assert "units_per_case_in_range" in {c.name for c in audit.structural_failures}

    def test_a_fabricated_retail_price_is_caught(self, good_invoice):
        text = build_pdi_export(good_invoice, UNITS)
        header, first, *rest = text.split("\r\n")
        corrupted = first[:62] + "00269" + first[67:]
        audit = audit_pdi_export("\r\n".join([header, corrupted, *rest]))

        assert "no_fabricated_values" in {c.name for c in audit.structural_failures}


class TestExportRefusesToShipABrokenFile:
    def test_build_pdi_export_raises_when_the_contract_is_violated(
        self, good_invoice, monkeypatch
    ):
        # Simulate an encoder bug: a description encoder that forgets to
        # truncate. The file must never reach the caller.
        monkeypatch.setattr(
            "app.services.export_service._pdi_description",
            lambda description: description.ljust(25),
        )
        broken = make_invoice(
            make_item(RB_COCONUT, "A DESCRIPTION LONGER THAN TWENTY-FIVE CHARACTERS")
        )
        with pytest.raises(ValueError, match="violates the confirmed byte contract"):
            build_pdi_export(broken, UNITS)


class TestContentChecks:
    def test_a_wrong_case_cost_is_reported_as_content_not_structure(self, good_invoice):
        text = build_pdi_export(good_invoice, UNITS)
        header, first, *rest = text.split("\r\n")
        corrupted = first[:43] + "009999" + first[49:]
        audit = audit_pdi_export("\r\n".join([header, corrupted, *rest]), good_invoice)

        assert not audit.ok
        assert not audit.structural_failures          # bytes still well-formed
        assert any(c.name.startswith("case_cost") for c in audit.content_failures)

    def test_missing_detail_line_is_reported(self, good_invoice):
        text = build_pdi_export(good_invoice, UNITS)
        header, first, _second, *rest = text.split("\r\n")
        audit = audit_pdi_export("\r\n".join([header, first, *rest]), good_invoice)

        assert "b_record_count_matches_line_items" in {
            c.name for c in audit.content_failures
        }

    def test_batch_date_and_amount_are_cross_checked(self, good_invoice):
        audit = audit_pdi_export(build_pdi_export(good_invoice, UNITS), good_invoice)
        by_name = {c.name: c for c in audit.checks}

        assert by_name["batch_is_invoice_number"].actual == "3376587"
        assert by_name["date_matches_invoice"].actual == "101025"
        assert by_name["header_amount_is_grand_total"].actual == "000006916"


class TestHeaderDetailBalance:
    """
    Measured, never judged: whether PDI requires the AMOUNT header to
    equal the sum of the detail lines is unresolved (Q7). The audit
    reports the gap and does NOT fail the file over it.
    """

    def test_the_gap_is_measured_and_reported(self):
        # Goods 69.16, grand total 78.96 — a 9.80 deposit+fuel gap, the
        # exact shape of the real Balkan invoice.
        invoice = make_invoice(
            make_item(RB_COCONUT, "RB COCONUT 24/12OZ", "50.20", sort_order=0),
            make_item(NESQ_CHOCO, "NESQ MILK 12/14 CHO", "18.96", sort_order=1),
            grand_total="78.96",
        )
        audit = audit_pdi_export(build_pdi_export(invoice, UNITS), invoice)

        assert audit.header_amount_cents == 7896
        assert audit.detail_total_cents == 6916
        assert audit.balance_difference_cents == 980
        assert audit.ok  # unresolved, so not a failure

    def test_quantity_is_multiplied_into_the_detail_total(self):
        invoice = make_invoice(
            make_item(RB_COCONUT, "RB COCONUT", "50.20", quantity="3"),
            grand_total="150.60",
        )
        audit = audit_pdi_export(build_pdi_export(invoice, UNITS), invoice)

        assert audit.detail_total_cents == 15060
        assert audit.balance_difference_cents == 0
