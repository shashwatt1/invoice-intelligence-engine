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
    PDI_BLOCK_A_WIDTH,
    PDI_BLOCK_B_TAIL_WIDTH,
    PDI_DESCRIPTION_WIDTH,
    PDI_ITEM_CODE_WIDTH,
    _pdi_cost_block,
    _pdi_cost_tail,
    _pdi_item_code,
    build_export_payload,
    build_items_csv,
    build_pdi_export,
    build_txt,
    export_basename,
    pdi_export_eligibility,
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
        vendor_name="Acme Distribution Co",
        status="VALIDATED",
        composite_confidence=Decimal("0.9840"),
        extraction_model="gpt-4o-mini",
    )
    invoice.created_at = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)
    invoice.vendor = Vendor(
        id=uuid.uuid4(), name="Acme Distribution Co", tax_id="US-0000001",
        address="1 Commerce Way", email="ap@acme-distribution.test",
    )
    invoice.document = Document(
        id=invoice.document_id, filename="acme-distribution.pdf", mime_type="application/pdf",
        file_size_bytes=2048, file_path="/uploads/x.pdf", file_hash="a" * 64,
        source_type="digital_pdf",
    )
    invoice.items = [
        InvoiceItem(
            invoice_id=invoice.id, description="NORTHWIND LAGER 12PK CAN",
            quantity=Decimal("3.0000"), unit_price=Decimal("21.9500"),
            line_total=Decimal("65.85"), tax_rate=Decimal("6.0000"),
            sort_order=0, product_sku="0000012345",
        ),
        InvoiceItem(
            invoice_id=invoice.id, description="CONTOSO GOLD ALE 12PK",
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
        assert payload["vendor"]["name"] == "Acme Distribution Co"
        assert payload["vendor"]["tax_id"] == "US-0000001"
        assert [item["position"] for item in payload["line_items"]] == [1, 2]
        assert payload["line_items"][0]["sku_upc"] == "0000012345"
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
        assert payload["vendor"]["name"] == "Acme Distribution Co"
        assert payload["vendor"]["email"] is None


class TestTxt:
    def test_txt_is_human_readable(self):
        text = build_txt(make_invoice())

        for expected in [
            "Vendor\n------\nAcme Distribution Co",
            "Invoice Number:\nINV-2026-0042",
            "Items",
            "1.\nDescription:\nNORTHWIND LAGER 12PK CAN",
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
        assert rows[1] == ["NORTHWIND LAGER 12PK CAN", "3.0", "21.95", "65.85", "6.0", "0000012345"]
        assert rows[2] == ["CONTOSO GOLD ALE 12PK", "2.0", "18.5", "37.0", "", ""]

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


class TestPdiItemCode:
    """
    Item-code reduction rule: a 12-digit UPC-A is reduced to PDI's 11-digit
    field by dropping the trailing check digit (standard "UPC without check
    digit" convention). This is a documented, deterministic transformation —
    not verified against a matched ground-truth PDI file (no such pair was
    available), so these tests pin the rule's *behavior*, not its correctness
    against PDI's actual expectation.
    """

    def test_12_digit_upc_drops_check_digit(self):
        assert _pdi_item_code("999000000015") == "99900000001"

    def test_dashed_upc_is_normalized_before_reduction(self):
        # Dash-delimited UPC formatting, as sometimes printed on invoices
        assert _pdi_item_code("9-99000-00026-3") == "99900000026"

    def test_short_vendor_item_number_is_zero_padded(self):
        assert _pdi_item_code("12345") == "00000012345"

    def test_code_longer_than_field_is_truncated(self):
        assert len(_pdi_item_code("1234567890123456")) == PDI_ITEM_CODE_WIDTH

    def test_missing_code_uses_confirmed_blank_convention(self):
        # "00000" + spaces — confirmed against a real blank-code row observed
        # in a supplied PDI sample file, not all-spaces.
        assert _pdi_item_code(None) == "00000" + " " * 6

    def test_empty_string_uses_confirmed_blank_convention(self):
        assert _pdi_item_code("") == "00000" + " " * 6

    def test_non_digit_characters_are_stripped_before_padding(self):
        assert _pdi_item_code("ABC-123") == "00000000123"


class TestPdiExport:
    def test_header_line_format(self):
        # 7-digit invoice number avoids any truncation ambiguity in the assertion.
        invoice = make_invoice(invoice_number="1234567", grand_total=Decimal("46.68"))
        header = build_pdi_export(invoice).splitlines()[0]

        assert header == "AMOUNT 1234567   033126+000004668"

    def test_header_batch_uses_last_seven_digits_of_invoice_number(self):
        invoice = make_invoice(invoice_number="INV-2026-0042")
        header = build_pdi_export(invoice).splitlines()[0]
        # digits-only "20260042" (8 chars) -> last 7 -> "0260042"
        assert header.split()[1] == "0260042"

    def test_detail_line_is_exactly_70_characters(self):
        pdi = build_pdi_export(make_invoice())
        detail_lines = pdi.splitlines()[1:]

        assert len(detail_lines) == 2
        assert all(len(line) == 70 for line in detail_lines)

    def test_detail_line_field_positions(self):
        invoice = make_invoice()
        line = build_pdi_export(invoice).splitlines()[1]  # first item

        assert line[0] == "B"
        assert line[1:12] == "00000012345"  # product_sku "0000012345" -> zero-padded 11
        assert line[12:37] == "NORTHWIND LAGER 12PK CAN".ljust(25)
        # cost block: 6 zeros + case cost (2195c) + "0100" + units/case
        assert line[37:43] == "000000"
        assert line[43:49] == "002195"
        assert line[49:53] == "0100"
        assert line[57] == "+"
        assert line[58:62] == "0003"  # quantity 3.0000
        assert line[62:70] == "0" * PDI_BLOCK_B_TAIL_WIDTH  # placeholder

    def test_long_description_is_truncated_to_25_chars(self):
        invoice = make_invoice()
        invoice.items[0].description = "A" * 40
        line = build_pdi_export(invoice).splitlines()[1]
        assert line[12:37] == "A" * PDI_DESCRIPTION_WIDTH

    def test_short_description_is_space_padded(self):
        invoice = make_invoice()
        invoice.items[0].description = "GUM"
        line = build_pdi_export(invoice).splitlines()[1]
        assert line[12:37] == "GUM".ljust(PDI_DESCRIPTION_WIDTH)

    def test_item_without_product_code_gets_blank_item_code(self):
        invoice = make_invoice()
        invoice.items[0].product_sku = None
        line = build_pdi_export(invoice).splitlines()[1]
        assert line[1:12] == "00000" + " " * 6

    def test_quantity_field_is_zero_padded_to_four_digits(self):
        invoice = make_invoice()
        invoice.items[1].quantity = Decimal("2.0000")
        line = build_pdi_export(invoice).splitlines()[2]  # second item
        assert line[58:62] == "0002"

    def test_missing_grand_total_and_date_do_not_crash(self):
        invoice = make_invoice(grand_total=None, invoice_date=None, invoice_number=None)
        header = build_pdi_export(invoice).splitlines()[0]
        assert header == "AMOUNT 0000000   000000+000000000"

    def test_output_uses_crlf_line_endings(self):
        # Confirmed against every real ground-truth file (both formats) —
        # a plain "\n" file was rejected by a real PDI import attempt with
        # a generic "wrong file format" error.
        pdi = build_pdi_export(make_invoice())
        assert pdi.endswith("\r\n")
        assert "\r\n" in pdi
        assert "\n" not in pdi.replace("\r\n", "")  # no bare LF anywhere

    def test_line_count_matches_header_plus_items(self):
        invoice = make_invoice()
        lines = build_pdi_export(invoice).rstrip("\r\n").split("\r\n")
        assert len(lines) == 1 + len(invoice.items)

    def test_no_trailer_records_are_emitted(self):
        # CFUE/CPPT content was reverted — see docs/PDI_DATA_CONTRACT.md
        # §2.2-2.3 — so no trailer lines are emitted at all right now.
        pdi = build_pdi_export(make_invoice())
        assert "CFUE" not in pdi
        assert "CPPT" not in pdi

    def test_large_invoice_produces_one_line_per_item(self):
        invoice = make_invoice()
        invoice.items = [
            InvoiceItem(
                invoice_id=invoice.id, description=f"Item {i:03d}",
                quantity=Decimal("1.0000"), unit_price=Decimal("2.5000"),
                line_total=Decimal("2.50"), sort_order=i, product_sku=None,
            )
            for i in range(150)
        ]
        pdi = build_pdi_export(invoice)
        detail_lines = pdi.splitlines()[1:]
        assert len(detail_lines) == 150
        assert all(len(line) == 70 for line in detail_lines)
        assert detail_lines[0][12:37].strip() == "Item 000"
        assert detail_lines[149][12:37].strip() == "Item 149"


class TestPdiCostFields:
    """
    Byte map confirmed against live PDI (docs/PDI_CASE_COST_INVESTIGATION.md):
      cost block [6:12]  -> Case Cost
      cost block [16:20] -> Units Per Case
      cost tail  [0:5]   -> EDI SRP (retail), deliberately left zero
    """

    def test_case_cost_is_written_in_cents_at_block_bytes_6_to_12(self):
        item = InvoiceItem(unit_price=Decimal("21.9500"), quantity=Decimal("3.0000"))
        assert _pdi_cost_block(item)[6:12] == "002195"

    def test_cost_block_keeps_the_confirmed_constant_marker(self):
        item = InvoiceItem(unit_price=Decimal("21.9500"), quantity=Decimal("3.0000"))
        assert _pdi_cost_block(item)[12:16] == "0100"

    def test_cost_block_is_still_exactly_twenty_digits(self):
        item = InvoiceItem(unit_price=Decimal("21.9500"), quantity=Decimal("3.0000"))
        block = _pdi_cost_block(item)
        assert len(block) == PDI_BLOCK_A_WIDTH
        assert block.isdigit()

    def test_units_per_case_parsed_from_pack_size(self):
        item = InvoiceItem(
            unit_price=Decimal("50.20"), quantity=Decimal("1"), pack_size="24/12OZ"
        )
        assert _pdi_cost_block(item)[16:20] == "0024"

    def test_units_per_case_defaults_to_one_when_pack_size_missing(self):
        # 1 is benign (Case Retail == Item Retail); a guessed pack size
        # would silently corrupt PDI's Case Retail calculation.
        item = InvoiceItem(unit_price=Decimal("50.20"), quantity=Decimal("1"))
        assert _pdi_cost_block(item)[16:20] == "0001"

    def test_units_per_case_ignores_unparseable_pack_size(self):
        item = InvoiceItem(
            unit_price=Decimal("50.20"), quantity=Decimal("1"), pack_size="CASE"
        )
        assert _pdi_cost_block(item)[16:20] == "0001"

    def test_expensive_case_cost_uses_the_full_six_digits(self):
        # Cigarette cartons run past $100; the real vendor files show
        # e.g. 014723 = $147.23 in this field.
        item = InvoiceItem(unit_price=Decimal("147.23"), quantity=Decimal("1"))
        assert _pdi_cost_block(item)[6:12] == "014723"

    def test_cost_tail_stays_zero_because_srp_is_not_on_a_wholesale_invoice(self):
        item = InvoiceItem(unit_price=Decimal("21.9500"), quantity=Decimal("3.0000"))
        assert _pdi_cost_tail(item) == "0" * PDI_BLOCK_B_TAIL_WIDTH

    def test_negative_unit_price_is_written_as_magnitude(self):
        item = InvoiceItem(unit_price=Decimal("-21.9500"), quantity=Decimal("3.0000"))
        assert _pdi_cost_block(item)[6:12] == "002195"


class TestPdiBatchNumber:
    """Batch/reference field is CONFIRMED to come from the store's
    invoice/reference number (invoice_number), replacing the prior
    placeholder — docs/PDI_OPEN_QUESTIONS.md Q2, now resolved."""

    def test_batch_number_derived_from_invoice_number(self):
        invoice = make_invoice(invoice_number="1234567")
        header = build_pdi_export(invoice).splitlines()[0]
        assert header.split()[1] == "1234567"

    def test_batch_number_uses_last_seven_digits_when_longer(self):
        invoice = make_invoice(invoice_number="INV-2026-0042")
        header = build_pdi_export(invoice).splitlines()[0]
        assert header.split()[1] == "0260042"

    def test_batch_number_is_zero_padded_when_shorter(self):
        invoice = make_invoice(invoice_number="42")
        header = build_pdi_export(invoice).splitlines()[0]
        assert header.split()[1] == "0000042"


class TestPdiReturnInvoice:
    """
    Business rule: normal invoices produce positive records, return/credit
    invoices (negative grand_total) produce negative records —
    docs/PDI_OPEN_QUESTIONS.md Q3, now resolved. "Negative" means the sign
    character flips to "-" on the header and every detail line; the
    amount/cost/quantity digit fields themselves stay magnitude-only.
    """

    def test_return_invoice_header_sign_is_negative(self):
        invoice = make_invoice(grand_total=Decimal("-46.68"))
        header = build_pdi_export(invoice).splitlines()[0]
        assert header == "AMOUNT 0260042   033126-000004668"

    def test_return_invoice_detail_line_sign_is_negative(self):
        invoice = make_invoice(grand_total=Decimal("-46.68"))
        lines = build_pdi_export(invoice).splitlines()[1:]
        assert all(line[57] == "-" for line in lines)

    def test_return_invoice_amount_field_stays_magnitude_only(self):
        invoice = make_invoice(grand_total=Decimal("-46.68"))
        header = build_pdi_export(invoice).splitlines()[0]
        assert header.endswith("-000004668")  # digits carry no minus sign

    def test_return_invoice_quantity_field_stays_magnitude_only(self):
        invoice = make_invoice(grand_total=Decimal("-46.68"))
        line = build_pdi_export(invoice).splitlines()[1]
        assert line[58:62] == "0003"

    def test_normal_invoice_sign_is_positive(self):
        invoice = make_invoice(grand_total=Decimal("46.68"))
        pdi = build_pdi_export(invoice)
        assert pdi.splitlines()[0][23] == "+"  # header sign position
        assert all(line[57] == "+" for line in pdi.splitlines()[1:])

    def test_zero_grand_total_is_not_treated_as_a_return(self):
        invoice = make_invoice(grand_total=Decimal("0.00"))
        pdi = build_pdi_export(invoice)
        assert pdi.splitlines()[0][23] == "+"
        assert pdi.splitlines()[1][57] == "+"

    def test_missing_grand_total_is_not_treated_as_a_return(self):
        invoice = make_invoice(grand_total=None)
        pdi = build_pdi_export(invoice)
        assert pdi.splitlines()[1][57] == "+"


class TestPdiTrailerRecords:
    """
    Trailer record byte layout (CFUE/CPPT, 38 chars) is CONFIRMED against
    real PDI ground-truth files — see the module note in export_service.py.
    Content is reverted to never-emitted: a prior CPPT-from-tax_amount
    mapping was disproven by cross-file analysis showing CPPT tracks
    cigarette-carton volume, not a generic tax total
    (docs/PDI_DATA_CONTRACT.md §2.2). CFUE was never implemented — it's a
    flat per-delivery constant in every real sample, pending business
    confirmation (§2.3).
    """

    def test_cppt_is_never_emitted_regardless_of_tax_amount(self):
        invoice = make_invoice(tax_amount=Decimal("7.78"))
        pdi = build_pdi_export(invoice)
        assert "CPPT" not in pdi

    def test_cfue_is_never_emitted(self):
        pdi = build_pdi_export(make_invoice())
        assert "CFUE" not in pdi

    def test_no_trailer_lines_at_all(self):
        invoice = make_invoice()
        lines = build_pdi_export(invoice).rstrip("\r\n").split("\r\n")
        assert len(lines) == 1 + len(invoice.items)  # header + items only, no trailer


class TestPdiDeterminism:
    """
    Formatter is now frozen pending real PDI validation — this pins the
    property that validation depends on: the same invoice data always
    produces byte-identical output, run after run, process after process.
    """

    def test_same_invoice_produces_byte_identical_output_across_calls(self):
        invoice = make_invoice()
        assert build_pdi_export(invoice) == build_pdi_export(invoice)

    def test_independently_built_equal_invoices_produce_identical_output(self):
        # Two separate ORM instances built from the same values (as would
        # happen across two different requests/processes) must still
        # produce identical bytes — nothing keyed off object identity,
        # memory address, or a fresh timestamp/uuid.
        assert build_pdi_export(make_invoice()) == build_pdi_export(make_invoice())


class TestPdiExportEligibility:
    """
    Single source of truth for whether format=pdi is allowed, shared by
    the export endpoint's gate and the invoice-detail API's
    pdi_export_allowed/pdi_export_requires_confirmation fields — the
    frontend reads the computed result rather than re-deriving the rule,
    so the two layers can't drift apart.
    """

    def test_validated_invoice_is_allowed_without_confirmation(self):
        invoice = make_invoice(status="VALIDATED")
        result = pdi_export_eligibility(invoice)
        assert result.allowed is True
        assert result.requires_confirmation is False
        assert result.blocked_reason is None

    def test_review_required_invoice_with_items_is_allowed_with_confirmation(self):
        invoice = make_invoice(status="REVIEW_REQUIRED")
        result = pdi_export_eligibility(invoice)
        assert result.allowed is True
        assert result.requires_confirmation is True
        assert result.blocked_reason is None

    def test_invoice_with_no_items_is_blocked_regardless_of_status(self):
        for status in ("VALIDATED", "REVIEW_REQUIRED"):
            invoice = make_invoice(status=status)
            invoice.items = []
            result = pdi_export_eligibility(invoice)
            assert result.allowed is False
            assert result.requires_confirmation is False
            assert result.blocked_reason  # non-empty, explains why
