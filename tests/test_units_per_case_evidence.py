"""
Units per case from the printed pack text and the printed Units and Cases columns — tests/test_units_per_case_evidence.py

PepsiCo prints "<container size> <package> 1/<per case>": "20OZ PL 1/24S" is one case of 24 twenty-ounce bottles.
The old reader took the number up front (the container size) as the case count and proposed 20, 28 and 16 where the
truth was 24, 15 and 12; 30 of 40 approved proposals on one invoice carried that error.
"""

from __future__ import annotations

import pytest

from app.services.export_service import (
    SUGGESTION_CONFLICT,
    SUGGESTION_FROM_PACK_SIZE,
    SUGGESTION_FROM_PRINTED_UNITS,
    SUGGESTION_FROM_PRINTED_UNITS_AND_PACK,
    suggest_units_per_case,
    suggested_units_per_case,
    units_evidence,
)


class TestTheContainerSizeIsNeverTheCaseCount:
    @pytest.mark.parametrize("pack, expected", [
        ("20OZ PL 1/24S", 24),
        ("28OZ PL 1/15", 15),
        ("1L PL 1/15S", 15),
        ("2L PL 1/8S", 8),
        ("16OZCN1/12SLCEL", 12),
        ("11OZ PL 1/12 TBG", 12),
        ("16OZ CN 1/12 SCP", 12),
        ("13.7OZ NR 1/12", 12),
        ("18.5OZ PL 1/12", 12),
        ("12OZ CN 1/12 SLK", 12),
        ("24OZ PL 1/24GAT", 24),
        ("14OZ PL 1/12 MM", 12),
    ])
    def test_the_denominator_of_one_over_n_is_the_case_count(self, pack, expected):
        assert suggest_units_per_case(pack) == (expected, SUGGESTION_FROM_PACK_SIZE)
        assert suggested_units_per_case(pack) == expected

    @pytest.mark.parametrize("pack", ["20OZ", "2L", "12OZ CN", "1L PL", "16OZ", "13.7OZ NR"])
    def test_a_bare_container_size_is_no_suggestion_at_all(self, pack):
        assert suggest_units_per_case(pack) == (None, None)

    def test_a_size_without_a_one_over_n_is_not_guessed_either(self):
        assert suggested_units_per_case("12OZ 6/4") is None


class TestExistingNotationsAreUnchanged:
    @pytest.mark.parametrize("pack, expected", [
        ("24/12OZ", 24), ("12/14", 12), ("12 CT", 12), ("C-15 25OZ", 15), ("B-12 24OZ", 12), ("C-2/12 12OZ", 2),
    ])
    def test_count_first_forms_still_read_the_count(self, pack, expected):
        assert suggested_units_per_case(pack) == expected

    def test_a_description_pack_notation_still_works_when_the_pack_column_is_a_bare_size(self):
        assert suggested_units_per_case("20OZ", "RB COCONUT 24/12OZ") == 24


class TestPrintedUnitsAndCases:
    def test_units_divided_by_cases_is_the_strongest_evidence(self):
        ev = units_evidence(None, None, cases=1, printed_units=24)
        assert (ev.units, ev.source, ev.conflict) == (24, SUGGESTION_FROM_PRINTED_UNITS, False)

    def test_several_cases_divide_back_to_one_case(self):
        assert units_evidence(None, None, cases=2, printed_units=30).units == 15

    def test_agreeing_sources_are_reported_as_such(self):
        ev = units_evidence("28OZ PL 1/15", None, cases=2, printed_units=30)
        assert (ev.units, ev.source) == (15, SUGGESTION_FROM_PRINTED_UNITS_AND_PACK)

    def test_disagreeing_sources_give_no_suggestion_and_a_note(self):
        ev = units_evidence("28OZ PL 1/15", None, cases=1, printed_units=12)
        assert (ev.units, ev.source, ev.conflict) == (None, SUGGESTION_CONFLICT, True)
        assert "12" in ev.note and "15" in ev.note

    @pytest.mark.parametrize("cases, units", [(2, 25), (0, 24), (None, 24), (1, None), (1, 0), (1, 100000)])
    def test_units_that_do_not_divide_evenly_or_are_missing_are_ignored(self, cases, units):
        ev = units_evidence("20OZ PL 1/24S", None, cases=cases, printed_units=units)
        assert ev.units == 24 and ev.source == SUGGESTION_FROM_PACK_SIZE

    def test_nothing_printed_is_no_evidence(self):
        assert units_evidence(None, None).units is None


class TestTheMappingCardUsesIt:
    """build_case_mapping_status is what the review screen shows for each unmapped product."""

    @staticmethod
    def row(pack_size, *, cases="1", printed_units=None):
        from decimal import Decimal

        from app.models.invoice import Invoice
        from app.models.invoice_item import InvoiceItem
        from app.services.case_mapping_service import build_case_mapping_status

        item = InvoiceItem(description="SYN COLA", product_sku="012000001291", pack_size=pack_size,
                           quantity=Decimal(cases), unit_price=Decimal("38.93"), line_total=Decimal("38.93"),
                           printed_units=printed_units, sort_order=0)
        invoice = Invoice(items=[item])
        [row] = build_case_mapping_status(invoice, {})
        return row

    def test_pack_text_gives_the_case_count_not_the_container_size(self):
        row = self.row("20OZ PL 1/24S")
        assert (row.suggested_units_per_case, row.suggestion_source) == (24, SUGGESTION_FROM_PACK_SIZE)

    def test_printed_units_and_pack_text_that_agree_are_labelled_as_such(self):
        row = self.row("28OZ PL 1/15", cases="2", printed_units=30)
        assert (row.suggested_units_per_case, row.suggestion_source) == (15, SUGGESTION_FROM_PRINTED_UNITS_AND_PACK)

    def test_disagreeing_evidence_prefills_nothing_and_offers_both_readings(self):
        row = self.row("28OZ PL 1/15", cases="1", printed_units=12)
        assert row.suggested_units_per_case is None
        assert row.suggestion_source == SUGGESTION_CONFLICT and row.suggestion_candidates == [12, 15]

    def test_a_bare_size_with_nothing_else_prefills_nothing(self):
        row = self.row("20OZ")
        assert (row.suggested_units_per_case, row.suggestion_source) == (None, None)
