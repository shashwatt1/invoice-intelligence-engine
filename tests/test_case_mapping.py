"""
tests/test_case_mapping.py — UPC → units-per-case mapping (no DB).

Offline coverage of the rules that decide what reaches the EDI:
mapping priority over the LLM's pack_size, refusal to guess an unknown
product's pack size, and the export gate. Persistence behaviour (upsert,
duplicate prevention, cross-invoice reuse through the real API) is
covered against Postgres in tests/integration/test_case_mapping_api.py.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.services.case_mapping_service import build_case_mapping_status
from app.services.export_service import (
    build_pdi_export,
    normalize_item_code,
    pdi_export_eligibility,
    suggested_units_per_case,
    unmapped_item_codes,
)

RB_COCONUT = "61126932121"      # 11 digits already — normalized form
NESQ_CHOCO = "02800077212"


def make_invoice(*items: InvoiceItem, status: str = "VALIDATED") -> Invoice:
    invoice = Invoice(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        invoice_number="3376587",
        currency="USD",
        grand_total=Decimal("273.66"),
        status=status,
    )
    invoice.items = list(items)
    return invoice


def make_item(
    product_sku: str | None, description: str = "ITEM", pack_size: str | None = None,
    sort_order: int = 0,
) -> InvoiceItem:
    return InvoiceItem(
        description=description,
        product_sku=product_sku,
        pack_size=pack_size,
        quantity=Decimal("1.0000"),
        unit_price=Decimal("50.20"),
        line_total=Decimal("50.20"),
        sort_order=sort_order,
    )


class TestItemCodeNormalization:
    """The mapping key and the emitted EDI code must be the same string,
    or a mapping saved from one invoice is never found from another."""

    def test_dashed_upc_normalizes_to_the_same_key_as_undashed(self):
        assert normalize_item_code("0-48500-20603-4") == normalize_item_code("048500206034")

    def test_twelve_digit_upc_drops_the_check_digit(self):
        assert normalize_item_code("028000772123") == "02800077212"

    def test_missing_or_unusable_code_is_none(self):
        for value in (None, "", "   ", "ABC"):
            assert normalize_item_code(value) is None


class TestSuggestionFromDocument:
    def test_leading_integer_is_the_case_pack(self):
        assert suggested_units_per_case("24/12OZ") == 24
        assert suggested_units_per_case("12/14") == 12

    def test_unparseable_pack_size_yields_no_suggestion(self):
        # None, not 1 — callers must handle "unknown" rather than be
        # handed a fabricated default.
        for value in (None, "", "CASE", "EACH"):
            assert suggested_units_per_case(value) is None

    def test_out_of_range_values_are_rejected(self):
        assert suggested_units_per_case("0/12OZ") is None
        assert suggested_units_per_case("99999/12OZ") is None


class TestExportGate:
    def test_unmapped_product_blocks_the_export(self):
        invoice = make_invoice(make_item(RB_COCONUT, "RB COCONUT"))
        result = pdi_export_eligibility(invoice, {})
        assert result.allowed is False
        assert "units-per-case" in result.blocked_reason

    def test_mapped_product_allows_the_export(self):
        invoice = make_invoice(make_item(RB_COCONUT, "RB COCONUT"))
        result = pdi_export_eligibility(invoice, {RB_COCONUT: 24})
        assert result.allowed is True
        assert result.blocked_reason is None

    def test_blocked_reason_counts_the_products_needing_mapping(self):
        invoice = make_invoice(
            make_item(RB_COCONUT, "RB COCONUT", sort_order=0),
            make_item(NESQ_CHOCO, "NESQ CHOCO", sort_order=1),
        )
        assert "2 products" in pdi_export_eligibility(invoice, {}).blocked_reason
        one_left = pdi_export_eligibility(invoice, {RB_COCONUT: 24})
        assert "1 product " in one_left.blocked_reason

    def test_multiple_unmapped_products_are_all_reported_without_duplicates(self):
        invoice = make_invoice(
            make_item(RB_COCONUT, "RB COCONUT", sort_order=0),
            make_item(NESQ_CHOCO, "NESQ CHOCO", sort_order=1),
            make_item(RB_COCONUT, "RB COCONUT again", sort_order=2),
        )
        assert unmapped_item_codes(invoice, {}) == [RB_COCONUT, NESQ_CHOCO]

    def test_line_without_a_product_code_does_not_block_export(self):
        # Nothing to key a mapping on; blocking would make such an
        # invoice permanently un-exportable.
        invoice = make_invoice(make_item(None, "NO CODE"))
        assert unmapped_item_codes(invoice, {}) == []
        assert pdi_export_eligibility(invoice, {}).allowed is True

    def test_empty_invoice_is_still_blocked_for_its_own_reason(self):
        invoice = make_invoice()
        result = pdi_export_eligibility(invoice, {})
        assert result.allowed is False
        assert "no extracted line items" in result.blocked_reason


class TestMappingDrivesTheEdi:
    def test_edi_carries_the_mapped_units_per_case(self):
        invoice = make_invoice(make_item(RB_COCONUT, "RB COCONUT"))
        line = build_pdi_export(invoice, {RB_COCONUT: 24}).splitlines()[1]
        assert line[53:57] == "0024"

    def test_mapping_overrides_the_llm_pack_size(self):
        # Document says 6; a human confirmed 24. The mapping wins.
        invoice = make_invoice(make_item(RB_COCONUT, "RB COCONUT", pack_size="6/12OZ"))
        line = build_pdi_export(invoice, {RB_COCONUT: 24}).splitlines()[1]
        assert line[53:57] == "0024"

    def test_export_refuses_rather_than_guessing_for_an_unmapped_product(self):
        invoice = make_invoice(make_item(RB_COCONUT, "RB COCONUT", pack_size="24/12OZ"))
        with pytest.raises(ValueError, match="No confirmed units-per-case"):
            build_pdi_export(invoice, {})

    def test_case_cost_is_unchanged_by_the_mapping_work(self):
        # Regression guard on the confirmed Case Cost byte range.
        invoice = make_invoice(make_item(RB_COCONUT, "RB COCONUT"))
        line = build_pdi_export(invoice, {RB_COCONUT: 24}).splitlines()[1]
        assert line[43:49] == "005020"       # $50.20
        assert line[49:53] == "0100"         # confirmed constant marker
        assert len(line) == 70


class TestReviewStatus:
    def test_mapped_and_unmapped_rows_are_reported_for_the_ui(self):
        invoice = make_invoice(
            make_item(RB_COCONUT, "RB COCONUT", pack_size="24/12OZ", sort_order=0),
            make_item(NESQ_CHOCO, "NESQ CHOCO", sort_order=1),
        )
        rows = build_case_mapping_status(invoice, {RB_COCONUT: 24})

        assert rows[0].mapped is True
        assert rows[0].units_per_case == 24
        assert rows[1].mapped is False
        assert rows[1].units_per_case is None

    def test_document_suggestion_is_offered_for_an_unmapped_product(self):
        invoice = make_invoice(make_item(RB_COCONUT, "RB COCONUT", pack_size="24/12OZ"))
        [row] = build_case_mapping_status(invoice, {})
        assert row.mapped is False
        assert row.suggested_units_per_case == 24   # a suggestion, not applied
        assert row.pack_size == "24/12OZ"

    def test_no_suggestion_when_the_document_says_nothing(self):
        invoice = make_invoice(make_item(RB_COCONUT, "RB COCONUT"))
        [row] = build_case_mapping_status(invoice, {})
        assert row.suggested_units_per_case is None
