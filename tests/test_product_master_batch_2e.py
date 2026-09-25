"""
tests/test_product_master_batch_2e.py — LOCAL OFFLINE TESTS for the
description policy, legacy reconciliation and shadow EDI comparison.

The properties defended here:

  * canonical descriptions come from a sanctioned product-master source or
    nowhere — never from frequency, length, recency or invoice wording;
  * a description difference is never an identity conflict;
  * the shadow EDI path reuses the real exporter, uses only APPROVED master
    data, and can never alter a financial value;
  * an invoice with no approved mapping is BLOCKED, not silently fallen
    back to the legacy value.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.product_master.descriptions import (
    OUTCOME_CANONICAL,
    OUTCOME_NO_DESCRIPTIONS,
    OUTCOME_NO_SANCTIONED_SOURCE,
    OUTCOME_SOURCE_CONFLICT,
    OUTCOME_TOO_LONG_FOR_PDI,
    PDI_DESCRIPTION_WIDTH,
    decide_canonical_description,
)
from app.services.product_master.shadow_edi import (
    SHADOW_BLOCKED,
    SHADOW_DIFFERENCE_EXPECTED,
    SHADOW_MATCH,
    SHADOW_UNSAFE_DIFFERENCE,
    classify,
    compare_line_fields,
)


def described(text, sheet="Sheet1", row=60):
    return {"description": text, "source_sheet": sheet,
            "source_file": "Beer Inventory.xlsx", "source_row": row}


class TestDescriptionPolicy:
    def test_a_product_sheet_description_that_fits_becomes_canonical(self):
        decision = decide_canonical_description([described("MICHELOB ULTRA 18/12 CAN")])
        assert decision.outcome == OUTCOME_CANONICAL
        assert decision.description == "MICHELOB ULTRA 18/12 CAN"
        assert decision.source_sheet == "Sheet1"

    def test_item_sales_shorthand_is_never_canonical(self):
        decision = decide_canonical_description([described("Mich ultra 18cans", sheet="data")])
        assert decision.outcome == OUTCOME_NO_SANCTIONED_SOURCE
        assert decision.description is None

    @pytest.mark.parametrize("sheet", ["Monarch Package", "Monarch Frontline", "Zink - Tiki"])
    def test_price_feed_and_duplicated_sources_are_not_canonical(self, sheet):
        decision = decide_canonical_description([described("TWISTED TEA HALF AND HALF", sheet=sheet)])
        assert decision.outcome == OUTCOME_NO_SANCTIONED_SOURCE

    def test_a_sanctioned_description_that_would_be_truncated_is_refused(self):
        long_text = "BUSCH LIGHT 24/12 NRLN 2/12"
        assert len(long_text) > PDI_DESCRIPTION_WIDTH
        decision = decide_canonical_description([described(long_text)])
        assert decision.outcome == OUTCOME_TOO_LONG_FOR_PDI
        assert decision.description is None, "the package notation must not be cut"

    def test_a_self_disagreeing_source_yields_nothing(self):
        decision = decide_canonical_description([
            described("BUD LIGHT 24/16 CAN 4/6"), described("BUD LIGHT 24/16", row=61),
        ])
        assert decision.outcome == OUTCOME_SOURCE_CONFLICT
        assert decision.description is None

    def test_no_descriptions_at_all_is_its_own_outcome(self):
        assert decide_canonical_description([]).outcome == OUTCOME_NO_DESCRIPTIONS

    def test_frequency_never_wins(self):
        # Five POS rows against one product-sheet row: the single sanctioned
        # source is chosen, and volume is irrelevant.
        decision = decide_canonical_description(
            [described("Mich ultra 18cans", sheet="data", row=i) for i in range(5)]
            + [described("MICHELOB ULTRA 18/12 CAN")]
        )
        assert decision.description == "MICHELOB ULTRA 18/12 CAN"

    def test_length_never_wins(self):
        decision = decide_canonical_description([
            described("A VERY LONG MONARCH PRODUCT NAME INDEED", sheet="Monarch Package"),
            described("BUSCH 24/16 CAN 4/6"),
        ])
        assert decision.description == "BUSCH 24/16 CAN 4/6"

    def test_abbreviations_and_punctuation_are_preserved_verbatim(self):
        for text in ("MICHELOB ULTRA 1/2 BBL", "BUD LIGHT 24/12 NRLN 2/12"):
            if len(text) <= PDI_DESCRIPTION_WIDTH:
                assert decide_canonical_description([described(text)]).description == text


class TestShadowEdiComparison:
    def _b(self, item_code="01820000115", description="BUD LIGHT", cost="002645",
           units="0004", quantity="0003"):
        return ("B" + item_code + description.ljust(25)
                + "000000" + cost + "0100" + units + "+" + quantity + "0" * 8)

    def test_identical_records_show_no_differences(self):
        line = self._b()
        assert compare_line_fields(line, line) == []

    def test_a_description_difference_is_expected_not_unsafe(self):
        differences = compare_line_fields(
            self._b(description="MICHELOB ULTRA C-18 12OZ"),
            self._b(description="MICHELOB ULTRA 18/12 CAN"),
        )
        assert [d["field"] for d in differences] == ["description"]
        assert differences[0]["kind"] == "expected"

    @pytest.mark.parametrize(("field", "left", "right"), [
        ("units_accounted_for", {"units": "0004"}, {"units": "0018"}),
        ("case_cost", {"cost": "002645"}, {"cost": "001670"}),
        ("item_code", {"item_code": "01820000115"}, {"item_code": "01820096721"}),
        ("quantity", {"quantity": "0003"}, {"quantity": "0007"}),
    ])
    def test_identity_commercial_and_financial_differences_are_unsafe(self, field, left, right):
        differences = compare_line_fields(self._b(**left), self._b(**right))
        assert [d["field"] for d in differences] == [field]
        assert differences[0]["kind"] == "unsafe"

    def test_classification_prefers_blocked_over_any_difference(self):
        assert classify([{"kind": "unsafe"}], ["no approved mapping"], False) == SHADOW_BLOCKED

    def test_byte_equality_with_no_differences_is_a_match(self):
        assert classify([], [], True) == SHADOW_MATCH

    def test_an_unsafe_difference_outranks_an_expected_one(self):
        assert classify(
            [{"kind": "expected"}, {"kind": "unsafe"}], [], False,
        ) == SHADOW_UNSAFE_DIFFERENCE

    def test_only_expected_differences_classify_as_expected(self):
        assert classify([{"kind": "expected"}], [], False) == SHADOW_DIFFERENCE_EXPECTED

    def test_the_shadow_reuses_the_real_exporter_rather_than_reimplementing_it(self):
        source = (Path(__file__).resolve().parent.parent
                  / "app/services/product_master/shadow_edi.py").read_text()
        assert "from app.services.export_service import build_pdi_export" in source
        # It must not define its own record formatting.
        assert "def _pdi_" not in source

    def test_the_shadow_never_writes_anything(self):
        source = (Path(__file__).resolve().parent.parent
                  / "app/services/product_master/shadow_edi.py").read_text()
        for forbidden in ("session.add", "commit()", "INSERT", "UPDATE ", "DELETE "):
            assert forbidden not in source


class TestApprovedOnlyShadowInput:
    def test_only_approved_mappings_reach_the_shadow(self):
        source = (Path(__file__).resolve().parent.parent
                  / "scripts/shadow_edi_compare.py").read_text()
        assert "STATE_APPROVED" in source
        assert "approval_state == STATE_APPROVED" in source.replace(
            "MasterCommercialMapping.approval_state == STATE_APPROVED",
            "approval_state == STATE_APPROVED")

    def test_a_missing_mapping_blocks_rather_than_falls_back(self):
        from app.services.product_master.shadow_edi import approved_units_for_invoice

        class Item:
            def __init__(self, sku):
                self.product_sku = sku

        class Invoice:
            items = [Item("018200001154"), Item("018200967214")]

        units, missing = approved_units_for_invoice(Invoice(), {"01820000115": 4})
        assert units == {"01820000115": 4}
        assert missing == ["01820096721"], "the uncovered code is reported, not defaulted"


class TestReconciliationSemantics:
    def test_description_mismatch_is_not_an_identity_finding(self):
        source = (Path(__file__).resolve().parent.parent
                  / "scripts/reconcile_product_master_with_legacy.py").read_text()
        # Both constants exist and are raised independently.
        assert "DESCRIPTION_MISMATCH" in source
        assert "IDENTITY_MISMATCH" in source
        assert "never implies a different" in source

    def test_the_reconciliation_never_writes(self):
        source = (Path(__file__).resolve().parent.parent
                  / "scripts/reconcile_product_master_with_legacy.py").read_text()
        assert "readonly=True" in source
        for forbidden in ("INSERT", "UPDATE ", "DELETE ", "commit()"):
            assert forbidden not in source
