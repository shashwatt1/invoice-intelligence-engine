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
    suggest_units_per_case,
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


class TestSuggestionFromDescription:
    """
    Observed failure: on the Balkan receipt layout all 7 line items came
    back with pack_size=None because that vendor prints no pack column —
    the pack is inside the description ("RB COCONUT 24/12OZ"). The
    operator was asked for 7 values with nothing offered on screen.
    """

    def test_pack_notation_in_description_is_recovered(self):
        assert suggested_units_per_case(None, "RB COCONUT 24/12OZ") == 24
        assert suggested_units_per_case(None, "NESQ MILK 12/14 CHO") == 12
        assert suggested_units_per_case(None, "RED BULL 12/16OZ CN") == 12

    def test_explicit_pack_size_still_wins_over_the_description(self):
        # A vendor that prints a real pack column is authoritative; the
        # description is only consulted when that column is empty.
        assert suggested_units_per_case("6/12OZ", "RB COCONUT 24/12OZ") == 6

    def test_a_bare_size_is_never_read_as_a_case_pack(self):
        # No slash: "20OZ" is a container size, not 20 units per case.
        for description in ("RED BULL 20OZ CAN", "SPRING WATER 2L", "CHIPS LARGE"):
            assert suggested_units_per_case(None, description) is None

    def test_one_is_never_suggested_from_a_description(self):
        # "1/2 GALLON" is a fraction, not a single-unit case. Proposing 1
        # for an unknown product is the failure this table exists to stop.
        assert suggested_units_per_case(None, "MILK 1/2 GALLON") is None

    def test_out_of_range_description_values_are_rejected(self):
        assert suggested_units_per_case(None, "WIDGET 99999/12OZ") is None

    def test_no_description_and_no_pack_size_yields_nothing(self):
        assert suggested_units_per_case(None, None) is None


class TestSuggestionReachesTheReviewUi:
    def test_balkan_layout_now_offers_every_suggestion(self):
        # The exact 7 descriptions from the real invoice, all with
        # pack_size=None as extraction actually returned them.
        descriptions = [
            ("NESQ MILK 12/14 CHO", 12), ("NESQ MILK 12/14 STR", 12),
            ("RB AMBER APRCT 24/1", 24), ("RB COCONUT 24/12OZ", 24),
            ("RB RED WTRMEL 24/12", 24), ("RED BULL 12/16OZ CN", 12),
            ("RED BULL 12/200Z CN", 12),
        ]
        invoice = make_invoice(*(
            make_item(f"6112690013{i}", description, pack_size=None, sort_order=i)
            for i, (description, _) in enumerate(descriptions)
        ))
        rows = build_case_mapping_status(invoice, {})

        assert [row.suggested_units_per_case for row in rows] == [
            units for _, units in descriptions
        ]
        assert all(row.mapped is False for row in rows)  # still needs confirming


class TestSuggestionProvenance:
    """
    The operator has to weigh a prefilled number, so the UI must say
    where it came from. Previously it always claimed the pack column,
    which rendered as an empty pair of quotes on vendors that print none.
    """

    def test_pack_column_is_reported_as_such(self):
        assert suggest_units_per_case("24/12OZ", "RB COCONUT") == (24, "pack_size")

    def test_description_derived_suggestions_are_labelled(self):
        assert suggest_units_per_case(None, "RB COCONUT 24/12OZ") == (24, "description")

    def test_structurally_ambiguous_packaging_is_flagged(self):
        # Four six-packs: 4 units per case, or 24 individual cans? The
        # invoice cannot answer that — only the store can.
        for description in ("BUSCH 4/6/160Z CAN", "ULTRA 3/8/16", "FIREBALL 100ML 8/6PK"):
            units, source = suggest_units_per_case(None, description)
            assert units is not None
            assert source == "description_ambiguous", description

    def test_nothing_to_suggest_reports_no_source(self):
        assert suggest_units_per_case(None, "BEATBOX MALT MYSTIC") == (None, None)

    def test_n_pack_never_becomes_units_per_case(self):
        # "30 PACK" describes the retail package. Whether the store sells
        # the 30-pack as one unit or breaks singles is a business fact the
        # mapping database owns, so no suggestion is offered at all.
        for description in ("BUD 18 PACK CANS", "BUD 30 PACK CANS",
                            "ULTRA 18 PACK CANS", "LAB 30 PACK CANS"):
            assert suggest_units_per_case(None, description) == (None, None), description

    def test_a_confirmed_mapping_reports_the_database_as_its_source(self):
        invoice = make_invoice(make_item(RB_COCONUT, "RB COCONUT 24/12OZ"))
        [row] = build_case_mapping_status(invoice, {RB_COCONUT: 6})

        assert row.mapped is True
        assert row.units_per_case == 6              # the confirmed value wins
        assert row.suggestion_source == "database"  # not "description"

    def test_ambiguous_rows_are_still_only_suggestions(self):
        invoice = make_invoice(make_item(RB_COCONUT, "BUSCH 4/6/160Z CAN"))
        [row] = build_case_mapping_status(invoice, {})

        assert row.mapped is False          # confirmation still required
        assert row.units_per_case is None   # nothing applied automatically
        assert row.suggested_units_per_case == 4
