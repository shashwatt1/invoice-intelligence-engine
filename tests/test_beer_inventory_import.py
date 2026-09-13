"""
tests/test_beer_inventory_import.py — parsing the Beer Inventory workbook.

Pinned against the real file where present, and against the specific
forms that made this workbook hard: four UPC spellings, three strengths
of items-per-case evidence, distributor-scoped codes, and rows where a
source disagrees with itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.product_reference import (
    BASIS_FRONTLINE,
    BASIS_PRICE_CHANGE,
    BASIS_PROMO,
    KIND_DISTRIBUTOR_ITEM_CODE,
    KIND_DISTRIBUTOR_PRODUCT_ID,
    KIND_UNIT_UPC,
)
from app.services.beer_inventory_import import (
    items_per_case_from_package,
    normalize_upc,
    parse_beer_inventory,
)

WORKBOOK = Path("data/reference/store_47708760/Beer Inventory.xlsx")
requires_workbook = pytest.mark.skipif(not WORKBOOK.is_file(), reason="workbook not present")


class TestUpcNormalization:
    def test_all_four_spellings_reach_the_same_key(self):
        # The same product, as four different sheets printed it.
        assert normalize_upc("018200001154") == "01820000115"          # plain 12-digit
        assert normalize_upc("0-18200-00115-4") == "01820000115"       # dashed
        assert normalize_upc("01820000115 4") == "01820000115"         # 11 + space + check
        assert normalize_upc("1.8200001154E10") == "01820000115"       # Excel ate the zero

    def test_scientific_notation_restores_the_leading_zero(self):
        # Without zero-padding this would become "18200250002" — the
        # wrong 11 digits, and it would silently match nothing.
        assert normalize_upc("1.8200250002E10") == "01820025000"

    def test_a_keg_row_with_no_barcode_yields_nothing(self):
        assert normalize_upc("000000000000") is None
        assert normalize_upc("") is None
        assert normalize_upc(None) is None


class TestPackageDecoding:
    def test_only_the_two_fraction_form_is_decoded(self):
        # 24 cans as 2 twelve-packs -> 2 sellable units. Proven against
        # Zink's own arithmetic on 378 of 378 such rows.
        assert items_per_case_from_package("24/12OZ 2/12 CANS") == 2
        assert items_per_case_from_package("24/12OZ 4/6 BOTTLES") == 4
        assert items_per_case_from_package("24/16 CAN 3/8") == 3

    def test_a_single_fraction_is_a_container_count_not_a_unit_count(self):
        # "18/12OZ CANS" is eighteen containers; Zink sells it as ONE
        # unit (unit cost == case cost). 48 of 176 such rows disagree
        # with the numerator, so it is not decoded.
        assert items_per_case_from_package("18/12OZ CANS") is None
        assert items_per_case_from_package("15/25OZ CANS") is None
        assert items_per_case_from_package("24/12OZ LOOSE CANS") is None

    def test_monarch_suffix_is_not_decoded(self):
        # "6P" on a 24-case means six-packs (÷4) on one row and "4P"
        # means ÷24 on another. Inconsistent, so left to the ratio.
        assert items_per_case_from_package("C24 12OZ 6P") is None
        assert items_per_case_from_package("C3012OZ") is None
        assert items_per_case_from_package("C12 19.2OZ") is None


@requires_workbook
class TestAllEightSheets:
    @pytest.fixture(scope="class")
    def report(self):
        return parse_beer_inventory(WORKBOOK)

    def test_every_sheet_parses_with_its_own_structure(self, report):
        assert set(report.per_sheet) == {
            "Sheet1", "Sheet2", "Sheet3", "Zink - Tiki", "Monarch Frontline",
            "Monarch Rung", "Monarch Package", "Monarch Singles",
        }
        assert all(count > 0 for count in report.per_sheet.values())
        assert report.per_sheet["Sheet1"] == 82
        assert report.per_sheet["Monarch Package"] == 1840

    def test_provenance_is_on_every_record(self, report):
        for rec in report.records:
            assert rec.sheet in report.per_sheet
            assert rec.row >= 2                    # never the header
            assert rec.distributor in ("Testani", "Zink", "Monarch")

    def test_pricing_basis_follows_the_sheet_semantics(self, report):
        basis = {rec.sheet: rec.pricing_basis for rec in report.records}
        assert basis["Sheet1"] == BASIS_PROMO                # "Current Promo / New Promo"
        assert basis["Sheet2"] == BASIS_PRICE_CHANGE         # "Old Case Cost / New Case Cost"
        assert basis["Zink - Tiki"] == BASIS_PRICE_CHANGE
        assert basis["Monarch Frontline"] == BASIS_FRONTLINE
        assert basis["Monarch Package"] == BASIS_PROMO

    def test_explicit_items_per_case_only_on_the_testani_sheets(self, report):
        stated = {rec.sheet for rec in report.records if rec.items_per_case_stated}
        assert stated == {"Sheet1", "Sheet2", "Sheet3"}
        # And the known Testani products carry the known values.
        by_code = {rec.item_code: rec for rec in report.records if rec.sheet == "Sheet1"}
        assert by_code["01820000115"].items_per_case_stated == 4      # BUD LT 4/6/16OZ
        assert by_code["01820008989"].items_per_case_stated == 3      # BUD LT 3/8
        assert by_code["01820011218"].items_per_case_stated == 1      # BUD 18 PACK
        assert by_code["01820023986"].items_per_case_stated == 3      # ULTRA 3/8/16

    def test_effective_dates_are_read_where_the_source_has_them(self, report):
        zink = next(r for r in report.records if r.sheet == "Zink - Tiki")
        assert zink.effective_from == date(2026, 2, 1)       # serial 46054
        frontline = next(r for r in report.records if r.sheet == "Monarch Frontline")
        assert frontline.effective_from == date(2026, 2, 1)
        assert frontline.effective_to == date(2026, 10, 3)
        package = next(r for r in report.records if r.sheet == "Monarch Package")
        assert package.effective_from == date(2026, 2, 1)   # from the preamble
        testani = next(r for r in report.records if r.sheet == "Sheet2")
        assert testani.effective_from is None                # undated; not invented

    def test_retail_upc_is_the_key_and_unit_upc_is_an_alias(self, report):
        zink = [r for r in report.records if r.sheet == "Zink - Tiki"]
        with_unit = [r for r in zink if any(i.kind == KIND_UNIT_UPC for i in r.identifiers)]
        assert with_unit, "expected Zink rows carrying a distinct unit UPC"
        for rec in with_unit:
            unit = next(i for i in rec.identifiers if i.kind == KIND_UNIT_UPC)
            assert unit.value != rec.item_code   # alias, never the key

    def test_distributor_codes_are_scoped_to_their_distributor(self, report):
        codes = [i for r in report.records for i in r.identifiers
                 if i.kind in (KIND_DISTRIBUTOR_ITEM_CODE, KIND_DISTRIBUTOR_PRODUCT_ID)]
        assert codes
        assert all(i.distributor for i in codes)
        # Testani 349 and Zink 349 are different products; nothing merges them.
        testani = {i.value for r in report.records if r.distributor == "Testani"
                   for i in r.identifiers if i.kind == KIND_DISTRIBUTOR_ITEM_CODE}
        zink = {i.value for r in report.records if r.distributor == "Zink"
                for i in r.identifiers if i.kind == KIND_DISTRIBUTOR_ITEM_CODE}
        shared = testani & zink
        assert shared, "the workbook does reuse numbers across distributors"
        # …and yet each is recorded against its own distributor only.

    def test_conflicting_rows_are_flagged_with_both_values_and_no_derived_units(self, report):
        conflicted = [r for r in report.records if r.is_conflicted]
        assert len(conflicted) == 14
        assert {r.sheet for r in conflicted} == {"Monarch Package"}
        for rec in conflicted:
            detail = rec.conflict_detail["items_per_case"]
            assert detail["current_unit_cost_divisor"] != detail["new_unit_cost_divisor"]
            assert rec.items_per_case_derived is None       # neither value trusted
        # Row 57 is the one the earlier analysis first surfaced.
        row57 = next(r for r in conflicted if r.row == 57)
        assert row57.conflict_detail["items_per_case"] == {
            "current_unit_cost_divisor": 24, "new_unit_cost_divisor": 1,
        }

    def test_costs_keep_their_source_meaning(self, report):
        # New vs previous are separate; unit cost is separate; nothing
        # is folded into one "cost".
        rec = next(r for r in report.records if r.sheet == "Sheet2" and r.item_code == "06206738062")
        assert rec.case_cost == Decimal("14.3500")            # New Case Cost
        assert rec.previous_case_cost == Decimal("13.4500")   # Old Case Cost
        assert rec.unit_cost == Decimal("1.1958")             # per item cost
        assert rec.items_per_case_stated == 12
