"""
tests/test_product_master.py — LOCAL OFFLINE TESTS for the Product Master
model and its candidate construction.

Nothing here touches a database. The Product Master is additive and unused
by invoice processing, the mapping queue and the EDI writer, and these
tests pin the properties that let it stay safe to build out:

  * identity comes from a resolved identifier, never from wording;
  * the repair applied to an identifier is chosen by its SOURCE, because
    the two 11-digit populations in this corpus need opposite repairs;
  * what is physically in a package and what PDI multiplies retail by are
    different records built from different evidence — 18 and 1 are both
    recordable for one product without contradiction;
  * a disagreement is an output, never something resolved by counting rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.product_master import (  # noqa: E402
    BASIS_SUPPLIER_ID,
    BASIS_UNRESOLVED,
    BASIS_UPC_A,
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    DERIVE_APPEND_CHECK_DIGIT,
    DERIVE_EXPAND_SCIENTIFIC,
    DERIVE_NONE,
    DERIVE_RESTORE_LEADING_ZERO,
    DERIVE_STRIP_SEPARATORS,
    ID_EAN_13,
    ID_PDI_ITEM_CODE,
    ID_SHORT_CODE,
    ID_SUPPLIER_ITEM_ID,
    ID_UNRESOLVED,
    ID_UPC_A,
    STATE_REVIEW_REQUIRED,
    STATE_UNRESOLVED,
    MasterCommercialMapping,
    MasterPackComposition,
    MasterProduct,
    MasterProductDescription,
    MasterProductIdentifier,
)
from app.services.product_master.candidates import (  # noqa: E402
    CONFLICT_COMMERCIAL_UNIT,
    CONFLICT_DESCRIPTION,
    CONFLICT_UNRESOLVED_IDENTITY,
    MasterSourceRow,
    build_candidates,
    parse_package_count,
)
from app.services.product_master.identifiers import (  # noqa: E402
    DISTRIBUTOR_WORKBOOK,
    ITEM_SALES_SUMMARY,
    ROLE_SUPPLIER_ID,
    SourceProfile,
    canonical_key_for,
    derive_identifier,
)

MICHELOB_UPC = "018200967214"
MICHELOB_SCAN = "01820096721"        # Item Sales: check digit omitted
MICHELOB_FLOAT = "1.8200967214E10"   # distributor: leading zero lost
MICHELOB_CAN = "0-18200-00334-9"


def row(**overrides) -> MasterSourceRow:
    base = {
        "source_system": "item_sales_summary", "profile": ITEM_SALES_SUMMARY,
        "source_file": "f.xlsx", "source_sheet": "data", "source_row": 1,
    }
    base.update(overrides)
    return MasterSourceRow(**base)


class TestSourceSpecificDerivation:
    """The rule the whole architecture rests on: the source picks the repair."""

    def test_a_scan_code_gains_its_check_digit(self):
        derived = derive_identifier(MICHELOB_SCAN, ITEM_SALES_SUMMARY)
        assert derived.identifier_type == ID_PDI_ITEM_CODE
        assert derived.derivation == DERIVE_APPEND_CHECK_DIGIT
        assert derived.canonical_upc == MICHELOB_UPC
        # The eleven-digit value is what the source holds; it is not rewritten.
        assert derived.normalized_value == MICHELOB_SCAN

    def test_a_distributor_float_regains_its_leading_zero(self):
        derived = derive_identifier(MICHELOB_FLOAT, DISTRIBUTOR_WORKBOOK)
        assert derived.identifier_type == ID_UPC_A
        assert derived.derivation == DERIVE_RESTORE_LEADING_ZERO
        assert derived.canonical_upc == MICHELOB_UPC
        chain = derived.derivation_detail["derivation_chain"]
        assert chain == [DERIVE_EXPAND_SCIENTIFIC, DERIVE_RESTORE_LEADING_ZERO]

    def test_the_same_digits_resolve_differently_under_different_profiles(self):
        # This is the property a length-based rule would destroy.
        eleven = "18200967214"
        as_scan = derive_identifier(eleven, ITEM_SALES_SUMMARY)
        as_float = derive_identifier(eleven, DISTRIBUTOR_WORKBOOK)
        assert as_scan.derivation == DERIVE_APPEND_CHECK_DIGIT
        assert as_float.derivation == DERIVE_RESTORE_LEADING_ZERO
        assert as_scan.canonical_upc != as_float.canonical_upc

    def test_a_leading_zero_restoration_that_fails_the_check_digit_is_not_asserted(self):
        derived = derive_identifier("99999999999", DISTRIBUTOR_WORKBOOK)
        assert derived.identifier_type == ID_UNRESOLVED
        assert derived.canonical_upc is None
        assert derived.evidence_state == STATE_REVIEW_REQUIRED

    def test_separators_are_stripped_without_changing_the_digits(self):
        derived = derive_identifier(MICHELOB_CAN, DISTRIBUTOR_WORKBOOK)
        assert derived.derivation == DERIVE_STRIP_SEPARATORS
        assert derived.canonical_upc == "018200003349"
        assert derived.raw_value == MICHELOB_CAN

    def test_a_valid_twelve_digit_upc_needs_no_repair(self):
        derived = derive_identifier(MICHELOB_UPC, DISTRIBUTOR_WORKBOOK)
        assert derived.derivation == DERIVE_NONE
        assert derived.identity_basis == BASIS_UPC_A

    def test_a_thirteen_digit_ean_is_kept_whole(self):
        # Truncating to eleven is the known normalize_item_code() defect;
        # it is deliberately not repeated here.
        derived = derive_identifier("4101010013663", DISTRIBUTOR_WORKBOOK)
        assert derived.identifier_type == ID_EAN_13
        assert derived.normalized_value == "4101010013663"

    def test_a_supplier_id_is_typed_by_the_profile_not_by_its_length(self):
        profile = SourceProfile(name="monarch", role=ROLE_SUPPLIER_ID)
        derived = derive_identifier("50335", profile)
        assert derived.identifier_type == ID_SUPPLIER_ITEM_ID
        assert derived.identity_basis == BASIS_SUPPLIER_ID

    @pytest.mark.parametrize("value", ["6", "21", "2000", "81234"])
    def test_short_codes_are_never_promoted_to_barcodes(self, value):
        derived = derive_identifier(value, ITEM_SALES_SUMMARY)
        assert derived.identifier_type == ID_SHORT_CODE
        assert derived.canonical_upc is None
        assert derived.normalized_value == value

    @pytest.mark.parametrize("value", [None, "", "NaN", "Totals", "000000000000"])
    def test_nothing_useful_resolves_to_nothing(self, value):
        derived = derive_identifier(value, ITEM_SALES_SUMMARY)
        assert derived.identity_basis == BASIS_UNRESOLVED
        assert derived.evidence_state == STATE_UNRESOLVED


class TestCanonicalIdentity:
    def test_two_sources_resolve_to_one_product(self):
        graph = build_candidates([
            row(raw_identifier=MICHELOB_SCAN, description="Mich ultra 18cans"),
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_file="Beer Inventory.xlsx", source_sheet="Sheet1", source_row=60,
                raw_identifier=MICHELOB_UPC, description="MICHELOB ULTRA 18/12 CAN"),
        ])
        assert len(graph.products) == 1
        product = graph.products[f"upc:{MICHELOB_UPC}"]
        assert product.identity_basis == BASIS_UPC_A
        assert len(product.identifiers) == 2
        assert {i["derivation"] for i in product.identifiers} == {
            DERIVE_APPEND_CHECK_DIGIT, DERIVE_NONE,
        }

    def test_the_cross_source_convergence_needs_no_bridge_rule(self):
        # Neither source ever writes the 12-digit form; they meet only
        # because each resolved independently.
        graph = build_candidates([
            row(raw_identifier="08769200057", description="Twisted halfhalf 18 pack can"),
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_file="Beer Inventory.xlsx", source_sheet="Monarch Package",
                source_row=190, raw_identifier="8.769200057E10",
                description="TWISTED TEA HALF AND HALF"),
        ])
        assert list(graph.products) == ["upc:087692000570"]

    def test_identity_is_never_taken_from_a_description(self):
        graph = build_candidates([
            row(source_row=1, raw_identifier=None, description="LABATT BLUE 2/12PK"),
            row(source_row=2, raw_identifier=None, description="LABATT BLUE 2/12PK"),
        ])
        assert len(graph.products) == 2, "identical wording must not merge two rows"
        assert all(p.identity_state == STATE_UNRESOLVED for p in graph.products.values())

    def test_raw_source_values_stay_recoverable(self):
        graph = build_candidates([
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                raw_identifier="01820096721 4"),
        ])
        product = graph.products[f"upc:{MICHELOB_UPC}"]
        assert product.identifiers[0]["raw_value"] == "01820096721 4"
        assert product.identifiers[0]["source_file"] == "f.xlsx"
        assert product.identifiers[0]["source_row"] == 1

    def test_an_unresolved_row_keys_on_itself_and_cannot_collide(self):
        derived = derive_identifier(None, ITEM_SALES_SUMMARY)
        assert canonical_key_for(derived, fallback="a|b|1") == "unresolved:a|b|1"
        assert canonical_key_for(derived, fallback="a|b|2") != "unresolved:a|b|1"


class TestPackCompositionVersusCommercialUnit:
    """The split that made a fifth table necessary."""

    def _michelob_rows(self):
        return [
            # Zink: parent barcode, unit barcode and an explicit package column.
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_sheet="Zink - Tiki", source_row=43,
                raw_identifier="01820096721 4", raw_unit_identifier=MICHELOB_CAN,
                package_notation="18/12OZ CANS", description="MICHELOB ULTRA"),
            # Sheet1: the distributor's own items/case statement.
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_sheet="Sheet1", source_row=60, raw_identifier=MICHELOB_UPC,
                description="MICHELOB ULTRA 18/12 CAN", commercial_units_statement="1.0",
                commercial_statement_evidence="Sheet1 items/case column"),
        ]

    def test_eighteen_and_one_are_both_recorded_without_contradiction(self):
        graph = build_candidates(self._michelob_rows())
        composition = graph.pack_compositions[0]
        commercial = graph.commercial_candidates[0]
        assert composition["child_quantity"] == 18, "what the package physically holds"
        assert commercial["units_accounted_for"] == 1, "what PDI multiplies retail by"
        assert composition["parent_canonical_key"] == commercial["canonical_key"]
        # Neither was derived from the other, and neither was overwritten.
        assert composition["composition_basis"] == "PACKAGE_NOTATION"
        assert commercial["commercial_unit_basis"] == COMMERCIAL_CASE_IS_SELLING_UNIT

    def test_the_contained_unit_is_its_own_product(self):
        graph = build_candidates(self._michelob_rows())
        assert "upc:018200003349" in graph.products
        composition = graph.pack_compositions[0]
        assert composition["child_canonical_upc"] == "018200003349"

    def test_no_composition_is_invented_without_a_child_barcode(self):
        graph = build_candidates([
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                raw_identifier=MICHELOB_UPC, package_notation="18/12OZ CANS"),
        ])
        assert graph.pack_compositions == [], "a count alone is not a composition"

    def test_composition_is_not_parsed_out_of_a_product_description(self):
        graph = build_candidates([
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                raw_identifier=MICHELOB_UPC, raw_unit_identifier=MICHELOB_CAN,
                description="MICHELOB ULTRA 18/12 CAN", package_notation=None),
        ])
        assert graph.pack_compositions == []

    @pytest.mark.parametrize(("notation", "expected"), [
        ("18/12 CAN", 18), ("18/12OZ CANS", 18), ("C18 12OZ", 18),
        ("C24 12OZ 6P", 24), ("", None), (None, None), ("CANS", None),
    ])
    def test_only_explicit_package_notations_are_read(self, notation, expected):
        assert parse_package_count(notation) == expected

    def test_a_commercial_candidate_is_never_auto_approved(self):
        graph = build_candidates(self._michelob_rows())
        assert graph.commercial_candidates[0]["approval_state"] == STATE_REVIEW_REQUIRED


class TestStoreScope:
    def test_commercial_candidates_keep_their_store_context_unresolved(self):
        graph = build_candidates([
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                raw_identifier=MICHELOB_UPC, commercial_units_statement="1",
                store_context="47708760"),
        ])
        candidate = graph.commercial_candidates[0]
        assert candidate["store_context"] == "47708760"
        # A workbook directory name is not a store UUID, and nothing here
        # invents the association.
        assert candidate["store_id_resolved"] is False

    def test_two_stores_disagreeing_does_not_duplicate_the_product(self):
        graph = build_candidates([
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_row=1, raw_identifier=MICHELOB_UPC,
                commercial_units_statement="1", store_context="A"),
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_row=2, raw_identifier=MICHELOB_UPC,
                commercial_units_statement="18", store_context="B"),
        ])
        assert len(graph.products) == 1, "commercial config never forks identity"
        assert len(graph.commercial_candidates) == 2


class TestDescriptionsAndAliases:
    def test_every_description_is_kept_and_none_is_promoted(self):
        graph = build_candidates([
            row(source_row=1, raw_identifier=MICHELOB_SCAN, description="Mich ultra 18cans"),
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_row=2, raw_identifier=MICHELOB_UPC,
                description="MICHELOB ULTRA 18/12 CAN"),
        ])
        product = graph.products[f"upc:{MICHELOB_UPC}"]
        assert len(product.descriptions) == 2
        assert all(d["role"] == "SOURCE" for d in product.descriptions)

    def test_a_repeated_description_is_counted_not_duplicated(self):
        graph = build_candidates([
            row(source_row=i, raw_identifier=MICHELOB_SCAN, description="Mich ultra 18cans")
            for i in range(3)
        ])
        product = graph.products[f"upc:{MICHELOB_UPC}"]
        assert len(product.descriptions) == 1
        assert product.descriptions[0]["observed_count"] == 3


class TestConflicts:
    def test_differing_descriptions_are_reported_not_resolved(self):
        graph = build_candidates([
            row(source_row=1, raw_identifier=MICHELOB_SCAN, description="Mich ultra 18cans"),
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_row=2, raw_identifier=MICHELOB_UPC, description="MICHELOB ULTRA 18/12 CAN"),
        ])
        conflict = next(c for c in graph.conflicts if c["category"] == CONFLICT_DESCRIPTION)
        assert "MICH ULTRA 18CANS" in conflict["observed"]

    def test_disagreeing_commercial_units_in_one_store_are_a_conflict(self):
        graph = build_candidates([
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_row=1, raw_identifier=MICHELOB_UPC,
                commercial_units_statement="1", store_context="A"),
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_row=2, raw_identifier=MICHELOB_UPC,
                commercial_units_statement="18", store_context="A"),
        ])
        conflict = next(c for c in graph.conflicts if c["category"] == CONFLICT_COMMERCIAL_UNIT)
        assert conflict["observed"] == "1, 18"

    def test_an_unresolved_identity_is_reported_as_such(self):
        graph = build_candidates([row(raw_identifier=None, description="MYSTERY")])
        assert any(c["category"] == CONFLICT_UNRESOLVED_IDENTITY for c in graph.conflicts)

    def test_frequency_never_decides_anything(self):
        graph = build_candidates(
            [row(source_row=i, raw_identifier=MICHELOB_SCAN, description="Popular wording")
             for i in range(5)]
            + [row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                   source_row=99, raw_identifier=MICHELOB_UPC, description="Rare wording")]
        )
        product = graph.products[f"upc:{MICHELOB_UPC}"]
        # The product has no canonical description at all; nothing won.
        assert len(product.descriptions) == 2
        assert any(c["category"] == CONFLICT_DESCRIPTION for c in graph.conflicts)


class TestDeterminism:
    def test_the_same_input_produces_the_same_candidate_graph(self):
        rows = [
            row(source_row=1, raw_identifier=MICHELOB_SCAN, description="Mich ultra"),
            row(source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_row=2, raw_identifier=MICHELOB_UPC, description="MICHELOB ULTRA",
                commercial_units_statement="1"),
        ]
        first, second = build_candidates(rows), build_candidates(list(rows))
        assert first.counts() == second.counts()
        assert sorted(first.products) == sorted(second.products)
        assert [c["category"] for c in first.conflicts] == [c["category"] for c in second.conflicts]


class TestModelShape:
    """The schema keeps the concepts apart that the research separated."""

    def test_the_five_tables_exist_and_are_namespaced(self):
        assert MasterProduct.__tablename__ == "master_products"
        assert MasterProductIdentifier.__tablename__ == "master_product_identifiers"
        assert MasterProductDescription.__tablename__ == "master_product_descriptions"
        assert MasterPackComposition.__tablename__ == "master_pack_compositions"
        assert MasterCommercialMapping.__tablename__ == "master_commercial_mappings"

    def test_pack_composition_and_commercial_mapping_are_different_tables(self):
        assert "child_quantity" in MasterPackComposition.__table__.columns
        assert "units_accounted_for" in MasterCommercialMapping.__table__.columns
        # The physical count must not be reachable from the commercial row.
        assert "child_quantity" not in MasterCommercialMapping.__table__.columns

    def test_the_pdi_multiplier_is_nullable_so_it_is_never_defaulted(self):
        assert MasterCommercialMapping.__table__.columns["units_accounted_for"].nullable

    def test_commercial_mapping_is_store_scoped_and_products_are_not(self):
        assert "store_id" in MasterCommercialMapping.__table__.columns
        assert "store_id" not in MasterProduct.__table__.columns

    def test_an_identifier_keeps_both_its_raw_and_canonical_form(self):
        columns = MasterProductIdentifier.__table__.columns
        assert not columns["raw_value"].nullable
        assert columns["normalized_value"].nullable
        assert "derivation" in columns and "derivation_detail" in columns

    def test_a_contained_product_may_be_unknown_without_losing_the_count(self):
        columns = MasterPackComposition.__table__.columns
        assert columns["child_product_id"].nullable
        assert not columns["child_quantity"].nullable
