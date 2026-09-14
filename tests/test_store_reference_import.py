"""
tests/test_store_reference_import.py — parsing the store's export.

Pinned against the real files in data/reference/store_47708760/ where
they exist, and against synthetic workbooks for the edge cases the real
files happen not to contain.

The rule this module exists to protect: the export's `Cost` column is a
PERIOD AGGREGATE (Cost = # Sold x Avg Cost, true on 6,138 of 6,138 real
rows, values reaching $2.3M). Reading it as a product cost would be
wrong by orders of magnitude while looking perfectly plausible.
"""

from __future__ import annotations

import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.store_reference_import import (
    ReferenceRow,
    parse_item_sales_summary,
)

DATA_DIR = Path("data/reference/store_47708760")
REAL_FILES = sorted(DATA_DIR.glob("Item_Sales*.xlsx")) if DATA_DIR.is_dir() else []
requires_real_files = pytest.mark.skipif(
    not REAL_FILES, reason="store reference exports not present"
)

SHEET = """<?xml version="1.0"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>{rows}</sheetData></worksheet>"""


def build_workbook(path: Path, rows: list[list[str]]) -> Path:
    """A minimal .xlsx carrying the given rows as inline strings."""
    body = ""
    for r, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{v}</t></is></c>'
            for c, v in enumerate(row) if v is not None
        )
        body += f'<row r="{r}">{cells}</row>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", SHEET.format(rows=body))
    return path


HEADERS = ["Scan code", "Description", "Department", "Primary Vendor",
           "Current Inventory Count", "# Sold", "$ Sold", "Avg Price", "Cost",
           "Avg Cost", "Margin", "Total Profit"]


def row(scan, desc, avg_price="2.99", cost="575.28", avg_cost="1.50"):
    return [scan, desc, "NaN", "NaN", "-10", "87", "260.13", avg_price, cost, avg_cost,
            "0.26", "206.85"]


class TestHeaderDiscovery:
    def test_header_on_the_third_row(self, tmp_path):
        book = build_workbook(tmp_path / "a.xlsx", [
            ["Item Sales Summary"], ["Store: 47708760"], HEADERS,
            row("01820000063", "Busch 6pack cans"),
        ])
        result = parse_item_sales_summary(book)
        assert len(result.rows) == 1
        assert result.store_number == "47708760"

    def test_header_on_the_fourth_row_after_a_report_date_line(self, tmp_path):
        # The third real export carries an extra "Report Date is ..." line.
        book = build_workbook(tmp_path / "b.xlsx", [
            ["Item Sales Summary"], ["Store: 47708760"],
            ["Report Date is 01/01/2024 - 09/07/2026"], HEADERS,
            row("01820000063", "Busch 6pack cans"),
        ])
        result = parse_item_sales_summary(book)
        assert len(result.rows) == 1
        assert result.store_number == "47708760"

    def test_columns_are_read_by_name_not_position(self, tmp_path):
        reordered = ["Description", "Avg Cost", "Scan code", "Avg Price", "Cost"]
        book = build_workbook(tmp_path / "c.xlsx", [
            ["Store: 47708760"], reordered,
            ["Busch 6pack cans", "4.6875", "01820000063", "6.99", "999999"],
        ])
        [product] = parse_item_sales_summary(book).rows
        assert product.item_code == "01820000063"
        assert product.avg_cost == Decimal("4.6875")
        assert product.avg_price == Decimal("6.99")

    def test_a_workbook_without_the_anchor_is_rejected(self, tmp_path):
        book = build_workbook(tmp_path / "d.xlsx", [["something", "else"], ["1", "2"]])
        with pytest.raises(ValueError, match="Item Sales Summary"):
            parse_item_sales_summary(book)


class TestTheAggregateCostIsNeverUsed:
    def test_avg_cost_is_taken_and_the_cost_column_ignored(self, tmp_path):
        # Cost 575.2875 == 87 x 6.6125; only the per-unit figure is a cost.
        book = build_workbook(tmp_path / "e.xlsx", [
            ["Store: 47708760"], HEADERS,
            row("01820000018", "Bud 16oz 6pk cans", cost="575.2875", avg_cost="6.6125"),
        ])
        [product] = parse_item_sales_summary(book).rows
        assert product.avg_cost == Decimal("6.6125")

    def test_a_huge_aggregate_never_leaks_into_avg_cost(self, tmp_path):
        book = build_workbook(tmp_path / "f.xlsx", [
            ["Store: 47708760"], HEADERS,
            row("01820000018", "X", cost="2346981.8707", avg_cost="1.16"),
        ])
        [product] = parse_item_sales_summary(book).rows
        assert product.avg_cost == Decimal("1.16")
        assert product.avg_cost < Decimal("100")


class TestMissingCost:
    def test_zero_avg_cost_becomes_null_not_zero(self, tmp_path):
        # Two thirds of the real catalogue is uncosted and exports as 0.
        book = build_workbook(tmp_path / "g.xlsx", [
            ["Store: 47708760"], HEADERS, row("01200000108", "WATER", avg_cost="0"),
        ])
        result = parse_item_sales_summary(book)
        [product] = result.rows
        assert product.avg_cost is None
        assert product.avg_cost != Decimal("0")
        assert result.zero_cost_nulled == 1

    def test_absent_and_nan_costs_are_null(self, tmp_path):
        book = build_workbook(tmp_path / "h.xlsx", [
            ["Store: 47708760"], HEADERS,
            row("01200000108", "A", avg_cost="NaN"),
            row("01200000109", "B", avg_cost=None),
        ])
        assert all(p.avg_cost is None for p in parse_item_sales_summary(book).rows)

    def test_an_uncosted_product_is_still_imported(self, tmp_path):
        # It still identifies the product; only the cost is unknown.
        book = build_workbook(tmp_path / "i.xlsx", [
            ["Store: 47708760"], HEADERS, row("01200000108", "WATER", avg_cost="0"),
        ])
        [product] = parse_item_sales_summary(book).rows
        assert product.item_code == "01200000108"
        assert product.description == "WATER"


class TestNormalizationAndSkips:
    def test_scan_codes_normalize_the_same_way_the_edi_does(self, tmp_path):
        book = build_workbook(tmp_path / "j.xlsx", [
            ["Store: 47708760"], HEADERS,
            row("018200000638", "twelve digit"),      # check digit dropped
            row("0-18200-00011-5", "dashed"),
        ])
        codes = [p.item_code for p in parse_item_sales_summary(book).rows]
        assert codes == ["01820000063", "01820000011"]

    def test_the_totals_footer_row_is_skipped(self, tmp_path):
        book = build_workbook(tmp_path / "k.xlsx", [
            ["Store: 47708760"], HEADERS,
            row("01820000063", "Busch"), ["Totals", None, None],
        ])
        result = parse_item_sales_summary(book)
        assert len(result.rows) == 1
        assert result.skipped_no_code == 1

    def test_a_duplicate_scan_code_can_keep_every_row_for_the_pricing_path(self, tmp_path):
        # The POS holds two item records under one code: one uncosted,
        # one costed. The catalogue keeps the first; the per-row pricing
        # table needs both, each under its own row number.
        path = build_workbook(tmp_path / "dup.xlsx", [
            ["Item Sales Summary"], ["Store: 86357232"], HEADERS,
            row("02840069746", "DORITOS - SWEET and TANGY BBQ", avg_cost="0"),
            row("02840069746", "Doritos bbq", avg_cost="0.37"),
        ])
        result = parse_item_sales_summary(path, keep_duplicates=True)
        assert [(r.source_row, r.description, r.avg_cost) for r in result.rows] == [
            (4, "DORITOS - SWEET and TANGY BBQ", None),
            (5, "Doritos bbq", Decimal("0.37")),
        ]
        assert result.skipped_duplicate == 0
        # and the default is unchanged
        first = parse_item_sales_summary(path)
        assert [r.source_row for r in first.rows] == [4] and first.skipped_duplicate == 1

    def test_a_duplicate_scan_code_keeps_the_first_occurrence(self, tmp_path):
        book = build_workbook(tmp_path / "l.xlsx", [
            ["Store: 47708760"], HEADERS,
            row("01820000063", "first", avg_cost="4.6875"),
            row("01820000063", "second", avg_cost="9.99"),
        ])
        result = parse_item_sales_summary(book)
        assert len(result.rows) == 1
        assert result.rows[0].description == "first"
        assert result.skipped_duplicate == 1

    def test_department_and_vendor_are_not_imported(self, tmp_path):
        # Both are the literal string "NaN" on every real row.
        book = build_workbook(tmp_path / "m.xlsx", [
            ["Store: 47708760"], HEADERS, row("01820000063", "Busch"),
        ])
        [product] = parse_item_sales_summary(book).rows
        assert not hasattr(product, "department")
        assert not hasattr(product, "primary_vendor")
        assert set(ReferenceRow.__dataclass_fields__) == {
            "item_code", "scan_code_raw", "description", "avg_cost", "avg_price", "source_row",
        }


@requires_real_files
class TestAgainstTheRealExports:
    def test_every_file_parses_and_reports_the_store(self):
        for path in REAL_FILES:
            result = parse_item_sales_summary(path)
            assert result.store_number == "47708760", path.name
            assert result.rows, path.name

    def test_the_union_matches_the_expected_shape(self):
        union = {}
        for path in REAL_FILES:
            for product in parse_item_sales_summary(path).rows:
                union[product.item_code] = product
        assert len(union) == 6138
        assert sum(1 for p in union.values() if p.avg_cost is not None) == 2040

    def test_no_imported_cost_is_ever_zero(self):
        for path in REAL_FILES:
            for product in parse_item_sales_summary(path).rows:
                assert product.avg_cost is None or product.avg_cost > 0

    def test_costs_stay_in_a_per_unit_range(self):
        # The aggregate column reaches $2.3M; a per-unit cost does not.
        for path in REAL_FILES:
            for product in parse_item_sales_summary(path).rows:
                if product.avg_cost is not None:
                    assert product.avg_cost < Decimal("1000"), product
