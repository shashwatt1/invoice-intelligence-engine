"""
tests/test_identity_bridge.py — LOCAL OFFLINE TESTS for the 11-to-12 digit
bridge analysis (scripts/analyze_identity_bridge.py).

The bridge exists to produce evidence, not to repair anything. These tests
pin that distinction: a candidate reconstruction is scored, recorded and
handed to a person, and the original 11-digit value is never rewritten or
promoted into the 12-digit namespace.

The substantive rule being defended is that the two 11-digit populations
must never share one repair. A distributor value written as a float lost
its leading zero; a sales Scan code lost its check digit. Applying either
repair to the other population yields a valid-looking code for a
different product.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_identity_bridge import (  # noqa: E402
    classify_bridge,
    describe_corroboration,
    meaningful_tokens,
    reconcile_case_mappings,
)
from scripts.analyze_product_master import upc_a_check_digit  # noqa: E402

# A real, check-digit-valid UPC from the distributor sheets.
BUD_LIGHT_UPC = "018200001154"
# The same product as the sales system writes it: check digit removed.
BUD_LIGHT_SCAN = "01820000115"
# The same product as Excel's float form writes it: leading zero gone.
BUD_LIGHT_FLOAT = "18200001154"


def corpus(**entries) -> dict:
    """A stand-in for the canonical upc12 candidates keyed by UPC."""
    return {
        upc: {"descriptions": description, "units_per_case_values": units,
              "source_record_count": "1"}
        for upc, (description, units) in entries.items()
    }


class TestMeaningfulTokens:
    """Corroboration must come from brand words, not packaging noise."""

    def test_brand_words_are_kept(self):
        assert meaningful_tokens("BUD LIGHT 24/16 CAN 4/6") == {"BUD", "LIGHT"}

    def test_packaging_words_are_dropped(self):
        assert meaningful_tokens("CAN CANS BOTTLE PACK NR DFT") == set()

    def test_sizes_and_bare_numbers_are_dropped(self):
        assert meaningful_tokens("12OZ 750ML 16 6 1.5L") == set()

    def test_two_unrelated_products_sharing_only_packaging_do_not_corroborate(self):
        kind, _ = describe_corroboration("PEPSI 2 LITER CAN", "LAYS CHIPS CAN")
        assert kind == "no_overlap"


class TestDescriptionCorroboration:
    def test_a_shared_brand_word_is_an_exact_match(self):
        kind, tokens = describe_corroboration("Bud lt 16oz 6 cans", "BUD LIGHT 24/16 CAN 4/6")
        assert kind == "exact_token"
        assert "BUD" in tokens

    def test_a_prefix_relation_is_reported_as_weaker(self):
        kind, tokens = describe_corroboration("BUDWEISER 18/12", "BUDWEIS SPECIAL")
        assert kind == "partial_token"
        assert "BUDWEIS" in tokens

    def test_an_absent_description_is_not_treated_as_agreement(self):
        assert describe_corroboration("", "BUD LIGHT")[0] == "no_description"


class TestBridgeClassification:
    """Each 11-digit code is scored against evidence outside itself."""

    def test_a_scan_code_whose_check_digit_reconstruction_lands_is_strong(self):
        result = classify_bridge(
            BUD_LIGHT_SCAN,
            corpus(**{BUD_LIGHT_UPC: ("BUD LIGHT 24/16 CAN 4/6", "4")}),
            set(), set(), "Bud lt 16oz 6 cans",
        )
        assert result["evidence_suggested_hypothesis"] == "append_check_digit"
        assert result["bridge_target"] == BUD_LIGHT_UPC
        assert result["evidence_strength"] == "STRONG"
        assert "BUD" in result["shared_tokens"]

    def test_a_float_derived_value_whose_padded_form_lands_is_strong(self):
        result = classify_bridge(
            BUD_LIGHT_FLOAT,
            corpus(**{BUD_LIGHT_UPC: ("BUD LIGHT 24/16 CAN 4/6", "4")}),
            set(), set(), "BUD LIGHT 18/12OZ CANS",
        )
        assert result["evidence_suggested_hypothesis"] == "prepend_zero"
        assert result["bridge_target"] == BUD_LIGHT_UPC
        assert result["evidence_strength"] == "STRONG"

    def test_both_repairs_landing_is_ambiguous_and_never_auto_resolved(self):
        both = corpus(**{
            BUD_LIGHT_UPC: ("BUD LIGHT", "4"),
            "0" + BUD_LIGHT_SCAN: ("SOMETHING ELSE ENTIRELY", "12"),
        })
        result = classify_bridge(BUD_LIGHT_SCAN, both, set(), set(), "Bud lt")
        assert result["evidence_strength"] == "AMBIGUOUS"
        assert result["evidence_suggested_hypothesis"] == "ambiguous"
        assert result["bridge_target"] == "", "no winner may be chosen"

    def test_a_padded_form_that_is_valid_but_unknown_is_only_structural(self):
        result = classify_bridge(BUD_LIGHT_FLOAT, {}, set(), set(), "BUD LIGHT")
        assert result["prepend_zero_check_valid"] is True
        assert result["evidence_strength"] == "STRUCTURAL_ONLY"
        assert result["bridge_target"] == ""

    def test_a_code_matching_nothing_gets_no_bridge_evidence(self):
        result = classify_bridge("99999999999", {}, set(), set(), "MYSTERY")
        assert result["evidence_strength"] == "NO_BRIDGE_EVIDENCE"
        assert result["evidence_suggested_hypothesis"] == "none"

    def test_a_landing_whose_description_disagrees_is_demoted_not_accepted(self):
        result = classify_bridge(
            BUD_LIGHT_SCAN,
            corpus(**{BUD_LIGHT_UPC: ("PEPSI 2 LITER", "8")}),
            set(), set(), "Bud lt 16oz 6 cans",
        )
        assert result["evidence_strength"] == "WEAK_CONFLICTING_DESCRIPTION"

    def test_the_application_identifier_tables_count_as_evidence(self):
        result = classify_bridge(
            BUD_LIGHT_SCAN, {}, {BUD_LIGHT_UPC}, set(), "Bud lt",
        )
        assert result["append_check_in_app"] is True
        assert result["bridge_target"] == BUD_LIGHT_UPC


class TestNothingIsRewritten:
    """The whole point: evidence, not repair."""

    def test_the_original_eleven_digit_value_is_preserved_verbatim(self):
        result = classify_bridge(
            BUD_LIGHT_SCAN,
            corpus(**{BUD_LIGHT_UPC: ("BUD LIGHT", "4")}),
            set(), set(), "Bud lt",
        )
        assert result["identifier11"] == BUD_LIGHT_SCAN
        assert result["identifier11"] != result["bridge_target"]

    def test_both_reconstructions_are_always_reported_even_when_one_wins(self):
        result = classify_bridge(
            BUD_LIGHT_SCAN,
            corpus(**{BUD_LIGHT_UPC: ("BUD LIGHT", "4")}),
            set(), set(), "Bud lt",
        )
        # A reviewer must be able to see the rejected alternative.
        assert result["prepend_zero_candidate"] == "0" + BUD_LIGHT_SCAN
        assert result["append_check_candidate"] == BUD_LIGHT_UPC

    def test_the_appended_check_digit_is_computed_not_guessed(self):
        assert BUD_LIGHT_SCAN + upc_a_check_digit(BUD_LIGHT_SCAN) == BUD_LIGHT_UPC


class TestCaseMappingReconciliation:
    """The governed mappings are read, compared and left alone."""

    def _existing(self, *mappings):
        return {"available": True, "case_mappings": list(mappings)}

    def test_agreement_is_reported_when_the_corpus_confirms_the_governed_pack_size(self):
        rows = reconcile_case_mappings(
            self._existing({"item_code": BUD_LIGHT_SCAN, "units_per_case": 4,
                            "description": "BUD LIGHT 24/16 CAN"}),
            corpus(**{BUD_LIGHT_UPC: ("BUD LIGHT 24/16 CAN 4/6", "4")}),
            {},
        )
        assert rows[0]["units_agreement"] == "agrees"
        assert rows[0]["bridge_target"] == BUD_LIGHT_UPC
        assert rows[0]["governed_units_per_case"] == 4, "the governed value is reported as-is"

    def test_a_contradiction_is_surfaced_and_not_silently_corrected(self):
        rows = reconcile_case_mappings(
            self._existing({"item_code": BUD_LIGHT_SCAN, "units_per_case": 18,
                            "description": "BUD LIGHT 24/16 CAN"}),
            corpus(**{BUD_LIGHT_UPC: ("BUD LIGHT 24/16 CAN 4/6", "1")}),
            {},
        )
        assert rows[0]["units_agreement"] == "DISAGREES"
        # Both numbers survive so a person can judge which is right.
        assert rows[0]["governed_units_per_case"] == 18
        assert rows[0]["corpus_units_per_case"] == "1"

    def test_a_mapping_the_corpus_cannot_reach_is_marked_not_guessed(self):
        rows = reconcile_case_mappings(
            self._existing({"item_code": "07199048024", "units_per_case": 12,
                            "description": "KEYSTONE LIGHT 12/24 CAN"}),
            {}, {},
        )
        assert rows[0]["units_agreement"] == "no_corpus_evidence"
        assert rows[0]["bridge_target"] == ""

    def test_a_bridged_mapping_without_pack_evidence_is_distinguished_from_agreement(self):
        rows = reconcile_case_mappings(
            self._existing({"item_code": BUD_LIGHT_SCAN, "units_per_case": 4,
                            "description": "BUD LIGHT"}),
            corpus(**{BUD_LIGHT_UPC: ("BUD LIGHT 24/16 CAN", "")}),
            {},
        )
        assert rows[0]["units_agreement"] == "bridged_but_no_pack_evidence"

    @pytest.mark.parametrize("length_code", ["1234", "123456"])
    def test_item_codes_that_are_not_eleven_digits_are_never_bridged(self, length_code):
        rows = reconcile_case_mappings(
            self._existing({"item_code": length_code, "units_per_case": 6,
                            "description": "SOMETHING"}),
            corpus(**{BUD_LIGHT_UPC: ("BUD LIGHT", "4")}),
            {},
        )
        assert rows[0]["bridge_target"] == ""
        assert rows[0]["units_agreement"] == "no_corpus_evidence"

    def test_an_unreachable_database_yields_no_rows_rather_than_invented_ones(self):
        assert reconcile_case_mappings({"available": False}, {}, {}) == []
