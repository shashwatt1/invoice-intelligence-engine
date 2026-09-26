"""
tests/test_product_master_commercial.py — LOCAL OFFLINE TESTS for Phase 2C:
commercial-unit decisions and the commercial candidate seed's guards.

The field under test is a multiplier, not a count. PDI computes
Case Retail = Item Retail x units_accounted_for, so the number depends on
what the PDI item's Item Retail prices — and a wrong value silently
corrupts Case Retail. These tests defend the consequences of that:

  * only an explicit distributor statement of sellable units may supply the
    number;
  * physical pack composition, package notation, description text and row
    frequency may never supply it;
  * a governed mapping may only *withhold* it, never provide it;
  * nothing seeded here is approved or authoritative.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.product_master import (  # noqa: E402
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    COMMERCIAL_CONFLICT,
    COMMERCIAL_UNIT_IS_SELLING_UNIT,
    COMMERCIAL_UNKNOWN,
    COST_CONFLICTING_SOURCES,
    COST_DISTRIBUTOR_CASE_PRICE,
    COST_UNRESOLVED,
    STATE_REVIEW_REQUIRED,
    VALID_COMMERCIAL_BASES,
    VALID_COST_BASES,
)
from app.services.product_master.commercial import (  # noqa: E402
    decide_case_cost,
    decide_commercial_unit,
)

SEED = Path(__file__).resolve().parent.parent / "scripts" / "seed_product_master_commercial.py"


def statement(units, *, sheet="Sheet1", row=60, cost=None, note="items/case column"):
    return {
        "units_accounted_for": units,
        "case_cost": cost,
        "evidence": {"statement": note, "raw_value": str(units)},
        "source_file": "data/reference/store_47708760/Beer Inventory.xlsx",
        "source_sheet": sheet,
        "source_row": row,
    }


class TestVocabulary:
    def test_the_four_bases_are_the_whole_vocabulary(self):
        whole_vocabulary = {
            COMMERCIAL_CASE_IS_SELLING_UNIT, COMMERCIAL_UNIT_IS_SELLING_UNIT,
            COMMERCIAL_UNKNOWN, COMMERCIAL_CONFLICT,
        }
        assert VALID_COMMERCIAL_BASES == whole_vocabulary

    def test_cost_bases_distinguish_absent_from_contradictory(self):
        whole_vocabulary = {
            COST_DISTRIBUTOR_CASE_PRICE, COST_UNRESOLVED, COST_CONFLICTING_SOURCES,
        }
        assert VALID_COST_BASES == whole_vocabulary


class TestCommercialUnitDecisions:
    def test_one_sellable_unit_means_the_case_is_the_selling_unit(self):
        decision = decide_commercial_unit([statement(1)])
        assert decision.units_accounted_for == 1
        assert decision.commercial_unit_basis == COMMERCIAL_CASE_IS_SELLING_UNIT

    def test_many_sellable_units_means_the_contained_unit_is_the_selling_unit(self):
        decision = decide_commercial_unit([statement(12)])
        assert decision.units_accounted_for == 12
        assert decision.commercial_unit_basis == COMMERCIAL_UNIT_IS_SELLING_UNIT

    def test_no_statement_leaves_the_unit_unknown_rather_than_defaulted(self):
        decision = decide_commercial_unit([])
        assert decision.units_accounted_for is None
        assert decision.commercial_unit_basis == COMMERCIAL_UNKNOWN

    def test_disagreeing_sources_conflict_and_choose_nothing(self):
        decision = decide_commercial_unit([
            statement(12, sheet="Monarch Package", row=36),
            statement(24, sheet="Monarch Package", row=59),
        ])
        assert decision.units_accounted_for is None
        assert decision.commercial_unit_basis == COMMERCIAL_CONFLICT

    def test_frequency_is_not_a_tiebreak(self):
        # Four sources say 12 and one says 24. Still a conflict.
        decision = decide_commercial_unit(
            [statement(12, row=r) for r in range(4)] + [statement(24, row=99)]
        )
        assert decision.commercial_unit_basis == COMMERCIAL_CONFLICT
        assert decision.units_accounted_for is None
        assert "Frequency is not a tiebreak" in decision.notes

    def test_agreeing_sources_are_one_decision_not_two(self):
        decision = decide_commercial_unit([statement(2), statement(2, row=61)])
        assert decision.units_accounted_for == 2
        assert len(decision.evidence["source_statements"]) == 2

    @pytest.mark.parametrize("bad", [None, "", "abc", 0, -3, 100000])
    def test_unusable_values_never_become_a_multiplier(self, bad):
        decision = decide_commercial_unit([statement(bad)])
        assert decision.units_accounted_for is None
        assert decision.commercial_unit_basis == COMMERCIAL_UNKNOWN


class TestGovernedMappingsMayOnlyDissent:
    def test_a_disagreeing_governed_mapping_withholds_the_value(self):
        # The Michelob case: reference says 1, governed holds 1 and 18.
        decision = decide_commercial_unit([statement(1)], governed_units={1, 18})
        assert decision.units_accounted_for is None
        assert decision.commercial_unit_basis == COMMERCIAL_CONFLICT
        assert "governed mapping holds [1, 18]" in decision.notes

    def test_an_agreeing_governed_mapping_corroborates_without_supplying(self):
        decision = decide_commercial_unit([statement(4)], governed_units={4})
        assert decision.units_accounted_for == 4, "the value came from the distributor"
        assert "independently agrees" in decision.notes

    def test_a_governed_mapping_alone_cannot_supply_a_value(self):
        # No distributor statement at all: the governed value must not fill in.
        decision = decide_commercial_unit([], governed_units={18})
        assert decision.units_accounted_for is None
        assert decision.commercial_unit_basis == COMMERCIAL_UNKNOWN

    def test_the_known_twisted_tea_conflict_stays_unresolved(self):
        decision = decide_commercial_unit(
            [statement(1, sheet="Monarch Package", row=190)], governed_units={1, 18},
        )
        assert decision.units_accounted_for is None
        assert decision.commercial_unit_basis == COMMERCIAL_CONFLICT


class TestPhysicalCompositionIsInadmissible:
    def test_pack_composition_is_not_an_input_to_the_decision(self):
        # There is no parameter through which 18 cans could become a
        # multiplier; a product with a known composition and no commercial
        # statement stays UNKNOWN.
        decision = decide_commercial_unit([])
        assert decision.commercial_unit_basis == COMMERCIAL_UNKNOWN
        assert "physical pack composition" in decision.evidence["inadmissible_evidence"]

    def test_the_inadmissible_list_is_recorded_on_every_decision(self):
        for decision in (decide_commercial_unit([statement(1)]),
                         decide_commercial_unit([])):
            inadmissible = decision.evidence["inadmissible_evidence"]
            assert "package notation" in inadmissible
            assert "description text" in inadmissible
            assert "frequency" in inadmissible


class TestApprovalState:
    @pytest.mark.parametrize("statements,governed", [
        ([statement(1)], None), ([statement(12)], None), ([], None),
        ([statement(1)], {1, 18}), ([statement(12), statement(24)], None),
    ])
    def test_every_decision_begins_review_required(self, statements, governed):
        decision = decide_commercial_unit(statements, governed_units=governed)
        assert decision.approval_state == STATE_REVIEW_REQUIRED

    def test_structural_validity_never_approves(self):
        # A perfectly clean, corroborated decision is still not approved.
        decision = decide_commercial_unit([statement(4)], governed_units={4})
        assert decision.approval_state != "APPROVED"


class TestCostSemantics:
    def test_a_single_case_price_is_recorded_with_its_basis(self):
        cost, basis = decide_case_cost([statement(1, cost="16.7")])
        assert cost == Decimal("16.7")
        assert basis == COST_DISTRIBUTOR_CASE_PRICE

    def test_an_absent_cost_is_unresolved_not_zero(self):
        cost, basis = decide_case_cost([statement(1)])
        assert cost is None
        assert basis == COST_UNRESOLVED

    def test_disagreeing_costs_record_no_value(self):
        cost, basis = decide_case_cost([
            statement(1, cost="16.7"), statement(1, row=61, cost="19.25"),
        ])
        assert cost is None, "no averaging, no first-seen"
        assert basis == COST_CONFLICTING_SOURCES

    def test_identical_costs_from_several_sources_are_one_value(self):
        cost, basis = decide_case_cost([
            statement(1, cost="16.7"), statement(1, row=61, cost="16.7"),
        ])
        assert cost == Decimal("16.7")
        assert basis == COST_DISTRIBUTOR_CASE_PRICE


class TestProvenanceAndReviewability:
    def test_evidence_points_back_to_file_sheet_and_row(self):
        decision = decide_commercial_unit([
            statement(1, sheet="Sheet1", row=60, note="Sheet1 items/case column"),
        ])
        source = decision.evidence["source_statements"][0]
        assert source["source_sheet"] == "Sheet1"
        assert source["source_row"] == 60
        assert "items/case" in source["statement"]
        assert source["units"] == 1

    def test_the_note_explains_why_the_multiplier_is_what_it_is(self):
        assert "unit cost equals its case price" in decide_commercial_unit(
            [statement(1)]).notes
        assert "breaks into 12 sellable units" in decide_commercial_unit(
            [statement(12)]).notes

    def test_a_withheld_value_explains_the_disagreement(self):
        decision = decide_commercial_unit([statement(1)], governed_units={1, 18})
        assert "1" in decision.notes and "18" in decision.notes
        assert decision.evidence["governed_units_observed"] == [1, 18]


class TestSeedSafety:
    def _source(self) -> str:
        return SEED.read_text()

    def test_the_seed_uses_the_same_local_only_guard(self):
        from scripts.seed_product_master_commercial import assert_local_database

        with pytest.raises(SystemExit):
            assert_local_database(
                "postgresql+asyncpg://u:p@db.abc.supabase.co:5432/postgres")
        assert assert_local_database(
            "postgresql+asyncpg://u:p@localhost:5432/x") == "localhost"

    def test_there_is_no_override_flag(self):
        for forbidden in ("--force", "--allow-remote", "--no-guard", "--override",
                          "--approve", "--authoritative"):
            assert forbidden not in self._source()

    def test_the_seed_never_writes_a_legacy_table(self):
        import re

        source = self._source()
        for legacy in ("ProductCaseMapping", "ProductIdentity", "ProductIdentifier",
                       "ProductDataProposal"):
            # ProductCaseMapping is imported to READ governed values; it must
            # never be constructed.
            assert not re.search(rf"(?<!Master){legacy}\(", source), (
                f"{legacy} must never be instantiated by the commercial seed"
            )

    def test_the_seed_writes_only_the_commercial_table(self):
        import re

        source = self._source()
        # Rows are written either as added instances or as one bulk insert.
        written = set(re.findall(r"session\.add\((\w+)\(", source))
        written |= set(re.findall(r"bulk_insert\(\s*session,\s*(\w+)", source))
        written |= set(re.findall(r"insert\((\w+)\)", source))
        assert written == {"MasterCommercialMapping"}

    def test_the_seed_does_not_touch_export_or_edi_code(self):
        source = self._source()
        for forbidden in ("export_service", "build_pdi_export", "pdi_export_eligibility",
                          "_pdi_units_per_case", "normalize_item_code"):
            assert forbidden not in source
