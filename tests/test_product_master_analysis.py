"""
tests/test_product_master_analysis.py — LOCAL OFFLINE TESTS for the
master-data analysis logic (scripts/analyze_product_master.py).

No workbook, no database, no filesystem output: these pin the rules that
decide what an identifier means, because that is where a wrong assumption
would quietly corrupt a product master.

The rule this file exists to defend: eleven digits is NOT one thing. In
this corpus it is either a UPC that lost its leading zero to Excel's float
format, or a Scan code with its check digit removed — and the repairs are
opposites. Nothing here may pad, promote or guess.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.analyze_product_master import (  # noqa: E402
    RawSourceRecord,
    build_candidate,
    consolidate,
    has_valid_upc_check,
    normalize_description,
    normalize_identifier,
    normalize_units_per_case,
    upc_a_check_digit,
)


def record(**overrides) -> RawSourceRecord:
    """A source row with only the fields a test cares about set."""
    base = {
        "record_id": "f|s|1", "source_file": "f.xlsx", "source_sheet": "s", "source_row": 1,
        "source_type": "distributor_price_sheet", "store_context": "47708760", "source_period": "",
        "raw_identifier": None, "identifier_role": "barcode", "raw_secondary_identifier": None,
        "raw_item_code": None, "raw_manufacturer_id": None, "raw_manufacturer_name": None,
        "raw_description": None, "raw_units_per_case": None, "raw_pack_hint": None,
        "raw_case_cost": None, "raw_unit_cost": None,
    }
    base.update(overrides)
    return RawSourceRecord(**base)


class TestIdentifierNormalization:
    """Only the transformations the corpus actually forces."""

    def test_a_leading_zero_is_never_lost(self):
        result = normalize_identifier("018200001154")
        assert result.normalized == "018200001154"
        assert result.kind == "upc12"
        assert "leading_zero_present" in result.flags

    def test_hyphen_groups_are_joined(self):
        result = normalize_identifier("8-51133-00676-2")
        assert result.normalized == "851133006762"
        assert "hyphen_groups_removed" in result.flags

    def test_an_internal_space_is_removed(self):
        result = normalize_identifier("01820096539 5")
        assert result.normalized == "018200965395"
        assert "internal_spaces_removed" in result.flags

    def test_excel_integer_decimals_are_stripped(self):
        assert normalize_identifier("130.0").normalized == "130"

    def test_scientific_notation_is_expanded_and_flagged_as_lossy(self):
        result = normalize_identifier("6.97560681826E11")
        assert result.normalized == "697560681826"
        assert "scientific_notation_expanded" in result.flags
        # The float form cannot carry a leading zero, so the value may be
        # one digit short of the truth. That has to travel with it.
        assert "leading_zero_indeterminate" in result.flags

    def test_a_scientific_value_that_is_not_an_integer_is_not_an_identifier(self):
        assert normalize_identifier("1.23456E2").kind.startswith("unresolved")

    def test_all_zero_placeholders_are_not_an_identity(self):
        # Kegs in Sheet1 carry this; it is filler, not a product.
        assert normalize_identifier("000000000000").kind == "placeholder_zeros"

    def test_a_missing_identifier_stays_missing(self):
        for empty in (None, "", "   ", "NaN", "Totals"):
            assert normalize_identifier(empty).kind == "missing"

    def test_non_numeric_text_is_never_forced_into_an_identity(self):
        assert normalize_identifier("BUD LIGHT 1/2 BBL").kind == "unresolved_non_numeric"


class TestNoGenericPadding:
    """The single most dangerous thing this pipeline could do."""

    @pytest.mark.parametrize("value", ["6", "21", "150", "2000", "81234", "11080314"])
    def test_short_and_odd_length_codes_are_never_padded_to_twelve(self, value):
        result = normalize_identifier(value)
        assert result.normalized == value, "the raw digits must survive untouched"
        assert not result.normalized.startswith("00000"), "no zfill"
        assert result.kind != "upc12"

    def test_a_short_code_is_classified_as_short_not_as_a_barcode(self):
        # '6' is the .05 Deposit PLU — being short does not make it a UPC.
        assert normalize_identifier("6").kind == "short_code"

    def test_an_eleven_digit_value_is_not_promoted_to_a_upc(self):
        result = normalize_identifier("01820000018")
        assert result.kind == "identifier11"
        assert result.normalized == "01820000018", "kept at eleven digits"

    def test_the_reconstructed_upc_is_offered_as_evidence_not_substituted(self):
        result = normalize_identifier("01820000018")
        # Recorded beside the value, never in place of it.
        assert result.derived_upc12 == "018200000188"
        assert has_valid_upc_check(result.derived_upc12)
        assert result.normalized != result.derived_upc12


class TestCheckDigits:
    def test_known_good_upc_validates(self):
        assert has_valid_upc_check("018200001154")

    def test_a_wrong_check_digit_is_caught(self):
        assert not has_valid_upc_check("018200001155")

    def test_check_digit_is_computed_from_the_first_eleven(self):
        assert upc_a_check_digit("01820000115") == "4"

    def test_twelve_digits_with_a_bad_check_are_kept_out_of_the_trusted_namespace(self):
        result = normalize_identifier("858289044446")
        assert result.kind == "upc12_check_failed"
        candidate = build_candidate(record(raw_identifier="858289044446"))
        assert candidate.identity_key.startswith("upc12bad:")


class TestIdentityKeys:
    """Namespaces must not leak into one another."""

    def test_a_valid_upc_grounds_the_identity(self):
        assert build_candidate(record(raw_identifier="018200001154")).identity_key == "upc12:018200001154"

    def test_an_eleven_digit_code_gets_its_own_namespace(self):
        candidate = build_candidate(record(raw_identifier="01820000018"))
        assert candidate.identity_key == "id11:01820000018"
        assert not candidate.identity_key.startswith("upc12:"), "never auto-promoted"

    def test_a_row_without_an_identifier_gets_a_private_bucket_not_a_shared_one(self):
        first = build_candidate(record(record_id="a|s|1", raw_description="LABATT BLUE"))
        second = build_candidate(record(record_id="b|s|9", raw_description="LABATT BLUE"))
        # Identical descriptions must not become one product.
        assert first.identity_key != second.identity_key
        assert first.identity_key.startswith("unresolved:")

    def test_a_secondary_barcode_can_ground_identity_when_the_primary_cannot(self):
        candidate = build_candidate(record(
            raw_identifier="000000000000", raw_secondary_identifier="0-18200-00473-5",
        ))
        assert candidate.identity_key == "upc12:018200004735"


class TestDescriptionsAreNotIdentity:
    def test_descriptions_are_normalized_only_for_comparison(self):
        assert normalize_description("Labatt Blue 2/12pk bottle") == "LABATT BLUE 2 12PK BOTTLE"

    def test_blank_descriptions_are_none(self):
        for empty in (None, "", "NaN"):
            assert normalize_description(empty) is None

    def test_two_rows_sharing_a_upc_merge_even_with_different_descriptions(self):
        records = [
            record(record_id="a|s|1", raw_identifier="018200001154", raw_description="BUD LIGHT 24/16 CAN"),
            record(record_id="b|s|2", raw_identifier="018200001154", raw_description="Bud lt 16oz 6 cans"),
        ]
        canonicals, conflicts, _ = consolidate(records, [build_candidate(r) for r in records])
        assert len(canonicals) == 1
        assert canonicals[0].status == "DUPLICATE_WITH_CONFLICT"
        assert "DESCRIPTION_VARIANTS" in canonicals[0].conflicts
        # Every description survives; none is crowned.
        assert len(canonicals[0].descriptions) == 2
        assert any(c["conflict_type"] == "DESCRIPTION_VARIANTS" for c in conflicts)


class TestConflictReporting:
    def _consolidate(self, records):
        return consolidate(records, [build_candidate(r) for r in records])

    def test_two_pack_sizes_make_one_product_with_a_conflict_not_two_products(self):
        records = [
            record(record_id="a|s|1", raw_identifier="018200001154", raw_units_per_case="12"),
            record(record_id="b|s|2", raw_identifier="018200001154", raw_units_per_case="24"),
        ]
        canonicals, conflicts, _ = self._consolidate(records)
        assert len(canonicals) == 1, "pack size is not part of identity"
        assert "PACK_SIZE_CONFLICT" in canonicals[0].conflicts
        assert canonicals[0].units_per_case_values == ["12", "24"]
        pack = next(c for c in conflicts if c["conflict_type"] == "PACK_SIZE_CONFLICT")
        assert "12" in pack["observed_values"] and "24" in pack["observed_values"]

    def test_a_repeated_pack_size_is_not_a_conflict(self):
        records = [
            record(record_id=f"a|s|{i}", raw_identifier="018200001154", raw_units_per_case="12")
            for i in range(3)
        ]
        canonicals, _, _ = self._consolidate(records)
        assert canonicals[0].units_per_case_values == ["12"]
        assert "PACK_SIZE_CONFLICT" not in canonicals[0].conflicts
        assert canonicals[0].status == "SAFE_DUPLICATE"

    def test_conflicting_manufacturer_ids_are_reported_never_resolved(self):
        records = [
            record(record_id="a|s|1", raw_identifier="018200001154", raw_manufacturer_id="66782"),
            record(record_id="b|s|2", raw_identifier="018200001154", raw_manufacturer_id="62117"),
        ]
        canonicals, _, _ = self._consolidate(records)
        assert "MANUFACTURER_CONFLICT" in canonicals[0].conflicts
        assert canonicals[0].manufacturer_ids == ["66782", "62117"]

    def test_differing_cost_alone_never_splits_or_flags_a_product(self):
        records = [
            record(record_id="a|s|1", raw_identifier="018200001154", raw_unit_cost="6.6125"),
            record(record_id="b|s|2", raw_identifier="018200001154", raw_unit_cost="7.10"),
        ]
        canonicals, _, _ = self._consolidate(records)
        assert len(canonicals) == 1
        assert canonicals[0].conflicts == [], "cost is not identity and not a conflict"
        assert canonicals[0].unit_costs == ["6.6125", "7.10"]

    def test_rows_without_an_identifier_are_reported_unresolved_not_invented(self):
        records = [record(record_id="a|s|1", raw_description="MYSTERY ITEM")]
        canonicals, _, _ = self._consolidate(records)
        assert canonicals[0].status == "UNRESOLVED_IDENTITY"
        assert canonicals[0].identity_trustworthy is False


class TestPossibleMatchesAreNotMerges:
    def test_an_eleven_digit_code_is_linked_to_a_real_upc_but_kept_separate(self):
        records = [
            record(record_id="sales|data|4", source_type="item_sales_summary",
                   raw_identifier="01820000115", raw_description="Bud lt 16oz 6 cans"),
            record(record_id="beer|Sheet1|3", raw_identifier="018200001154",
                   raw_description="BUD LIGHT 24/16 CAN 4/6"),
        ]
        canonicals, _, possible = consolidate(records, [build_candidate(r) for r in records])
        # Two canonical candidates, NOT one: the relationship is evidence.
        assert len(canonicals) == 2
        assert len(possible) == 1
        assert possible[0]["identifier11"] == "01820000115"
        assert possible[0]["derived_upc12"] == "018200001154"
        assert possible[0]["auto_merged"].startswith("NO")


class TestProvenance:
    def test_every_contributing_row_stays_addressable_on_the_candidate(self):
        records = [
            record(record_id="Item_Sales.xlsx|data|17", source_file="data/reference/Item_Sales.xlsx",
                   raw_identifier="018200001154", raw_description="Bud lt"),
            record(record_id="Beer Inventory.xlsx|Sheet1|3", source_file="data/reference/Beer Inventory.xlsx",
                   raw_identifier="018200001154", raw_description="BUD LIGHT"),
        ]
        canonicals, _, _ = consolidate(records, [build_candidate(r) for r in records])
        assert canonicals[0].source_record_count == 2
        assert sorted(canonicals[0].source_files) == [
            "data/reference/Beer Inventory.xlsx", "data/reference/Item_Sales.xlsx",
        ]

    def test_the_raw_value_survives_normalization(self):
        result = normalize_identifier("8-51133-00676-2")
        assert result.raw == "8-51133-00676-2"
        assert result.normalized == "851133006762"


class TestUnitsPerCase:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("12", "12"), ("12.0", "12"), ("2.0", "2"), ("4", "4"), ("1.5", "1.5"),
    ])
    def test_numeric_forms_normalize_without_inventing_a_value(self, raw, expected):
        assert normalize_units_per_case(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "NaN", "0", "-3", "N/A"])
    def test_absent_or_impossible_values_stay_absent(self, raw):
        assert normalize_units_per_case(raw) is None
