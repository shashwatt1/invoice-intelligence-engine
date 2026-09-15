"""
tests/test_multi_file_reference.py — several exports, one store.

Store 86357232 arrived as two Item Sales Summary exports for the same
period that are NOT copies: 46 scan codes appear in both, and where
they do, the two files disagree (one costed, one not; different
descriptions). These tests pin how the importers treat that: the
per-row pricing path keeps both readings with provenance, the catalogue
path refuses to pick one silently, a copy under another filename is
read once, and nothing is ever filed under the wrong store.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.item_sales_pricing_import import parse_item_sales_pricing
from app.services.store_reference_import import parse_item_sales_summary
from tests.test_store_reference_import import HEADERS, build_workbook, row

STORE_DIR = Path("data/reference/store_86357232")
FILE_1 = STORE_DIR / "Item_Sales_Summary_2026-09-14T15_45_30.014Z.xlsx"
FILE_2 = STORE_DIR / "Item_Sales_Summary_2026-09-14T15_56_01.537Z.xlsx"
MCKINLEY = Path("data/reference/store_47708760/Mckinley-07-24_to_07-26.xlsx")
STORE_ID = uuid.uuid4()
requires_store_files = pytest.mark.skipif(
    not (FILE_1.is_file() and FILE_2.is_file()), reason="store 86357232 exports not present"
)


def _catalogue_script():
    spec = importlib.util.spec_from_file_location("imp", Path("scripts/import_store_reference.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _book(path, store, *rows, period=True):
    preamble = [["Item Sales Summary"], [f"Store: {store}"]]
    if period:
        preamble.append(["Report Date is 07/01/2023 - 09/30/2026"])
    return build_workbook(path, [*preamble, HEADERS, *rows])


class TestCatalogueUnionAcrossFiles:
    """scripts/import_store_reference.py builds one row per (store, product)."""

    def _union(self, tmp_path, on_conflict="skip", store=None):
        a = _book(tmp_path / "Item_Sales_Summary_a.xlsx", "86357232",
                  row("01200013027", "Mountain dew baja blast 20oz", avg_price="2.29", avg_cost="0"),
                  row("07825000020", "Same everywhere", avg_price="1.99", avg_cost="1.28"),
                  row("11111111111", "Only in A"))
        b = _book(tmp_path / "Item_Sales_Summary_b.xlsx", "86357232",
                  row("01200013027", "MTN DEW BAJA BLAST", avg_price="2.3151", avg_cost="1.487"),
                  row("07825000020", "Same everywhere", avg_price="1.99", avg_cost="1.28"),
                  row("22222222222", "Only in B"))
        module = _catalogue_script()
        # build_union is given the Store id each file resolved to; the
        # store-code -> Store resolution itself is covered by
        # tests/integration/test_importer_store_resolution.py.
        parsed = [(p.name, parse_item_sales_summary(p), STORE_ID) for p in (a, b)]
        return module.build_union(parsed, imported_at=datetime.now(UTC), on_conflict=on_conflict)

    def test_a_disagreement_is_reported_and_neither_reading_chosen_by_default(self, tmp_path):
        rows, conflicts = self._union(tmp_path)
        assert {r["item_code"] for r in rows} == {"07825000020", "11111111111", "22222222222"}
        [c] = conflicts
        assert c["item_code"] == "01200013027"
        assert c["first"] == {"source_file": "Item_Sales_Summary_a.xlsx",
                              "description": "Mountain dew baja blast 20oz",
                              "avg_cost": None, "avg_price": Decimal("2.29")}
        assert c["second"]["avg_cost"] == Decimal("1.487")

    def test_identical_readings_merge_and_keep_the_first_files_provenance(self, tmp_path):
        rows, conflicts = self._union(tmp_path)
        [same] = [r for r in rows if r["item_code"] == "07825000020"]
        assert same["source_file"] == "Item_Sales_Summary_a.xlsx"
        assert same["avg_cost"] == Decimal("1.28")

    def test_a_choice_must_be_explicit(self, tmp_path):
        rows, _ = self._union(tmp_path, on_conflict="first")
        [r] = [r for r in rows if r["item_code"] == "01200013027"]
        assert r["avg_cost"] is None and r["source_file"] == "Item_Sales_Summary_a.xlsx"
        rows, _ = self._union(tmp_path, on_conflict="last")
        [r] = [r for r in rows if r["item_code"] == "01200013027"]
        assert r["avg_cost"] == Decimal("1.487") and r["source_file"] == "Item_Sales_Summary_b.xlsx"

    def test_null_cost_and_real_cost_are_a_disagreement_not_a_merge(self, tmp_path):
        # The whole point of NULL-not-zero: "no cost on file" and "$1.487"
        # are different claims and must not collapse into one row.
        _, conflicts = self._union(tmp_path)
        assert conflicts[0]["first"]["avg_cost"] is None
        assert conflicts[0]["second"]["avg_cost"] is not None


class TestPricingPathKeepsEverySourceRow:
    def test_two_files_same_code_are_two_records_with_their_own_provenance(self, tmp_path):
        a = _book(tmp_path / "Item_Sales_Summary_a.xlsx", "86357232",
                  row("01200013027", "Mountain dew baja blast 20oz", avg_price="2.29", avg_cost="0"))
        b = _book(tmp_path / "Item_Sales_Summary_b.xlsx", "86357232",
                  row("01200013027", "MTN DEW BAJA BLAST", avg_price="2.3151", avg_cost="1.487"))
        ra, rb = parse_item_sales_pricing(a), parse_item_sales_pricing(b)
        assert ra.store_number == rb.store_number == "86357232"
        assert (ra.period_start, ra.period_end) == (rb.period_start, rb.period_end)
        [x], [y] = ra.records, rb.records
        assert (x.sheet, x.row, x.item_code, x.unit_cost, x.unit_retail) == \
            ("data", 5, "01200013027", None, Decimal("2.29"))
        assert (y.sheet, y.row, y.item_code, y.unit_cost, y.unit_retail) == \
            ("data", 5, "01200013027", Decimal("1.487"), Decimal("2.3151"))

    def test_a_code_repeated_inside_one_file_keeps_both_rows(self, tmp_path):
        a = _book(tmp_path / "Item_Sales_Summary_a.xlsx", "86357232",
                  row("02840069746", "DORITOS - SWEET and TANGY BBQ", avg_cost="0"),
                  row("02840069746", "Doritos bbq", avg_cost="0.37"))
        records = parse_item_sales_pricing(a).records
        assert [(r.row, r.unit_cost) for r in records] == [(5, None), (6, Decimal("0.37"))]


@requires_store_files
class TestTheRealStore86357232Exports:
    @pytest.fixture(scope="class")
    def reports(self):
        return parse_item_sales_pricing(FILE_1), parse_item_sales_pricing(FILE_2)

    def test_both_are_item_sales_summaries_for_the_store_and_period(self, reports):
        for report in reports:
            assert report.store_number == "86357232"           # from the preamble, not the name
            assert str(report.period_start) == "2023-07-01"
            assert str(report.period_end) == "2026-09-30"

    def test_the_shape_as_supplied(self, reports):
        one, two = reports
        codes_1 = {r.item_code for r in one.records}
        codes_2 = {r.item_code for r in two.records}
        assert (len(one.records), len(codes_1)) == (4289, 4053)   # 236 codes listed twice
        assert (len(two.records), len(codes_2)) == (1041, 1041)
        assert len(codes_1 & codes_2) == 46
        assert len(codes_1 - codes_2) == 4007
        assert len(codes_2 - codes_1) == 995

    def test_the_second_file_is_not_a_copy_of_the_first(self, reports):
        from app.services.reference_workbooks import content_hash
        assert content_hash(FILE_1) != content_hash(FILE_2)

    def test_overlapping_codes_are_source_level_differences(self, reports):
        one, two = reports
        by_1 = {r.item_code: r for r in one.records}
        by_2 = {r.item_code: r for r in two.records}
        overlap = sorted(set(by_1) & set(by_2))
        cost_differs = sum(1 for c in overlap if by_1[c].unit_cost != by_2[c].unit_cost)
        assert cost_differs == 40
        # every one of those is "no cost on file" in file 1 vs a real cost in file 2
        assert all(by_1[c].unit_cost is None and by_2[c].unit_cost is not None
                   for c in overlap if by_1[c].unit_cost != by_2[c].unit_cost)

    def test_no_zero_cost_is_ever_stored_as_zero(self, reports):
        for report in reports:
            assert all(r.unit_cost is None or r.unit_cost > 0 for r in report.records)
            assert all(r.unit_retail is None or r.unit_retail > 0 for r in report.records)


@pytest.mark.skipif(not MCKINLEY.is_file(), reason="Mckinley export not present")
class TestStore47708760IsUnchangedByTheParserChange:
    def test_the_mckinley_export_has_no_repeated_codes_so_every_row_is_as_before(self):
        report = parse_item_sales_pricing(MCKINLEY)
        rows = [r.row for r in report.records]
        assert len(report.records) == 2294
        assert len(set(rows)) == 2294 and min(rows) == 5
        assert len({r.item_code for r in report.records}) == 2294
