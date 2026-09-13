"""
tests/test_item_sales_pricing.py — a period export as dated pricing rows,
and units-per-case from what the store's till says a unit sells for.

The retail-margin band is a measurement, not a guess: on the 26 Testani
products already approved on stronger evidence, margin at the approved
units ran 14.8%–36.8%. These tests pin what that band does and does not
allow.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.item_sales_pricing_import import parse_item_sales_pricing
from app.services.store_reference_service import (
    RETAIL_MARGIN_HIGH,
    RETAIL_MARGIN_LOW,
    derive_units_from_retail,
)

WORKBOOK = Path("data/reference/store_47708760/Mckinley-07-24_to_07-26.xlsx")
requires_workbook = pytest.mark.skipif(not WORKBOOK.is_file(), reason="workbook not present")


def dec(x):
    return Decimal(str(x))


class TestRetailMarginDerivation:
    def test_one_pack_in_band_is_strong(self):
        # Fireball: $66.86 case, scans at $11.99 (a 6-pack).
        # 8 six-packs -> 30.3% margin, in band. 6 -> 7% (out), 48 -> 88%.
        units, strength, detail = derive_units_from_retail(dec("66.86"), dec("11.99"))
        assert (units, strength) == (8, "strong")
        assert detail["in_band"] == [8]
        assert RETAIL_MARGIN_LOW <= detail["margin"] <= RETAIL_MARGIN_HIGH

    def test_a_30_pack_sold_whole_is_supported_not_strong(self):
        # BUD 30 PACK: $22.70 case scans at $25.72. Only N=1 is anywhere
        # near plausible (11.7%); every other N implies >55%. Below the
        # calibrated floor, so flagged, not silent.
        units, strength, detail = derive_units_from_retail(dec("22.70"), dec("25.7217"))
        assert (units, strength) == (1, "supported")
        assert detail["in_band"] == []
        assert detail["near_floor"] == [1]
        assert "below the calibrated floor" in detail["note"]

    def test_two_packs_in_band_is_ambiguous_and_yields_nothing(self):
        # CLUBTAILS LONG ISLAND: $34.50 case, $2.19 a can -> 20 or 24.
        units, strength, detail = derive_units_from_retail(dec("34.50"), dec("2.1943"))
        assert units is None and strength is None
        assert sorted(detail["in_band"]) == [20, 24]

    def test_an_approved_18_pack_is_reproduced(self):
        # BUD 18 PACK was approved as 1 on cost-ratio evidence; the retail
        # method must agree, at the floor of the calibration.
        units, strength, _ = derive_units_from_retail(dec("15.75"), dec("18.49"))
        assert (units, strength) == (1, "strong")

    def test_a_wrong_units_value_is_not_produced_by_a_thin_margin_alone(self):
        # Two packs near the floor: not "supported", because the rule
        # requires exactly one plausible reading.
        units, strength, detail = derive_units_from_retail(dec("40.00"), dec("2.20"))
        assert units is None or strength != "supported" or len(detail["near_floor"]) == 1

    def test_missing_or_zero_inputs_yield_nothing(self):
        assert derive_units_from_retail(None, dec("5"))[0] is None
        assert derive_units_from_retail(dec("5"), None)[0] is None
        assert derive_units_from_retail(dec("5"), dec("0"))[0] is None
        assert derive_units_from_retail(dec("0"), dec("5"))[0] is None

    def test_every_pack_considered_is_reported_for_the_reviewer(self):
        _, _, detail = derive_units_from_retail(dec("22.70"), dec("25.7217"))
        assert "margin_by_units" in detail
        assert detail["margin_by_units"][1] == pytest.approx(0.1175, abs=1e-3)
        assert detail["margin_by_units"][30] > 0.9          # the absurd reading is visible


@requires_workbook
class TestTheMckinleyExport:
    @pytest.fixture(scope="class")
    def report(self):
        return parse_item_sales_pricing(WORKBOOK)

    def test_report_period_is_read_from_the_preamble(self, report):
        assert report.store_number == "47708760"
        assert report.period_start == date(2024, 7, 1)
        assert report.period_end == date(2026, 7, 8)

    def test_every_row_carries_provenance_and_the_period(self, report):
        assert report.records
        for rec in report.records:
            assert rec.sheet == "data"
            assert rec.row >= 5
            assert rec.effective_from == date(2024, 7, 1)
            assert rec.pricing_basis == "period_average"
            assert rec.raw_identifier

    def test_retail_is_preserved_even_where_cost_is_absent(self, report):
        by_code = {r.item_code: r for r in report.records}
        bud30 = by_code["01820011030"]
        assert bud30.description == "Budweiser 30cans"
        assert bud30.row == 553
        assert bud30.unit_retail == Decimal("25.7217")
        assert bud30.unit_cost is None                       # Avg Cost 0 -> NULL, never 0

    def test_the_unresolved_testani_products_are_present_or_absent_as_reported(self, report):
        codes = {r.item_code for r in report.records}
        assert {"01820011030", "01820053030", "01820096550", "06206705146",
                "06206704946", "08800404091", "68474680041", "01820026128"} <= codes
        assert "85005919593" not in codes                    # BEATBOX SOUR — absent
        assert "01820026129" not in codes                    # PLAT SELTZ WIL — absent


class TestTheCatalogueImporterIgnoresPeriodExports:
    def test_directory_mode_only_matches_catalogue_files(self, tmp_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "imp", Path("scripts/import_store_reference.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        (tmp_path / "Item_Sales_Summary_x.xlsx").write_bytes(b"")
        (tmp_path / "Mckinley-07-24_to_07-26.xlsx").write_bytes(b"")
        (tmp_path / "Beer Inventory.xlsx").write_bytes(b"")
        chosen = [p.name for p in module._workbooks([str(tmp_path)])]
        assert chosen == ["Item_Sales_Summary_x.xlsx"]
