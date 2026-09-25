"""
tests/test_product_master_phase3.py — LOCAL OFFLINE TESTS for the Phase 3
review queue, conflict dossier, legacy-only reconciliation and the named
shadow-EDI difference vocabulary.

What these defend:

  * the queue splits by a DERIVED status, so it can never disagree with the
    stored approval state;
  * legacy may corroborate or dissent and is reported either way, but never
    supplies a value;
  * a description difference is never called unsafe merely for differing,
    while identity, multiplier, cost, quantity and amount always are;
  * none of the new reporting tools can mutate anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.product_master import (
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    COMMERCIAL_CONFLICT,
    COMMERCIAL_UNIT_IS_SELLING_UNIT,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_REVIEW_REQUIRED,
)
from app.services.master_commercial_review_service import (
    LEGACY_ABSENT,
    LEGACY_AGREES,
    LEGACY_DISSENTS,
    LEGACY_UNCOMPARABLE,
    REVIEW_APPROVED,
    REVIEW_CONFLICT,
    REVIEW_NO_MULTIPLIER,
    REVIEW_READY,
    REVIEW_REJECTED,
    legacy_agreement,
    review_status,
)
from app.services.product_master.shadow_edi import (
    DIFF_BLOCKED,
    DIFF_EXPECTED_DESCRIPTION,
    DIFF_NONE,
    DIFF_RECORD_COUNT,
    DIFF_UNSAFE_AMOUNT,
    DIFF_UNSAFE_COST,
    DIFF_UNSAFE_IDENTITY,
    DIFF_UNSAFE_MULTIPLIER,
    DIFF_UNSAFE_QUANTITY,
    difference_codes,
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


class Mapping:
    def __init__(self, *, state=STATE_REVIEW_REQUIRED,
                 basis=COMMERCIAL_UNIT_IS_SELLING_UNIT, units=4):
        self.approval_state = state
        self.commercial_unit_basis = basis
        self.units_accounted_for = units


class TestDerivedReviewStatus:
    @pytest.mark.parametrize(("mapping", "expected"), [
        (Mapping(), REVIEW_READY),
        (Mapping(basis=COMMERCIAL_CONFLICT, units=None), REVIEW_CONFLICT),
        (Mapping(units=None), REVIEW_NO_MULTIPLIER),
        (Mapping(state=STATE_APPROVED), REVIEW_APPROVED),
        (Mapping(state=STATE_REJECTED), REVIEW_REJECTED),
    ])
    def test_every_candidate_lands_in_exactly_one_queue(self, mapping, expected):
        assert review_status(mapping) == expected

    def test_a_decided_row_keeps_its_decision_over_its_evidence(self):
        # An approved row that was a conflict still reads APPROVED.
        approved_conflict = Mapping(
            state=STATE_APPROVED, basis=COMMERCIAL_CASE_IS_SELLING_UNIT, units=1)
        assert review_status(approved_conflict) == REVIEW_APPROVED

    def test_the_repository_predicate_covers_every_derived_status(self):
        from app.repositories.master_commercial_repository import _review_status_clause

        for status in (REVIEW_READY, REVIEW_CONFLICT, REVIEW_NO_MULTIPLIER,
                       REVIEW_APPROVED, REVIEW_REJECTED):
            assert _review_status_clause(status) is not None

    def test_an_unknown_status_matches_nothing_rather_than_everything(self):
        from app.repositories.master_commercial_repository import _review_status_clause

        clause = str(_review_status_clause("NOT_A_STATUS"))
        assert "IS NULL" in clause


class TestLegacyAgreement:
    def test_agreement_is_reported_when_the_values_match(self):
        assert legacy_agreement(4, {4}) == LEGACY_AGREES

    def test_dissent_is_reported_without_changing_the_candidate(self):
        assert legacy_agreement(1, {1, 18}) == LEGACY_DISSENTS

    def test_absence_is_distinguished_from_dissent(self):
        assert legacy_agreement(4, set()) == LEGACY_ABSENT

    def test_a_candidate_with_no_value_cannot_be_compared(self):
        assert legacy_agreement(None, {18}) == LEGACY_UNCOMPARABLE


class TestShadowDifferenceVocabulary:
    def test_no_differences_is_none(self):
        assert difference_codes([], []) == [DIFF_NONE]

    def test_a_blocker_outranks_every_other_code(self):
        assert difference_codes(
            [{"field": "units_accounted_for"}], ["missing mapping"],
        ) == [DIFF_BLOCKED]

    def test_a_description_difference_is_not_unsafe(self):
        assert difference_codes([{"field": "description"}], []) == [
            DIFF_EXPECTED_DESCRIPTION
        ]

    @pytest.mark.parametrize(("field", "code"), [
        ("item_code", DIFF_UNSAFE_IDENTITY),
        ("units_accounted_for", DIFF_UNSAFE_MULTIPLIER),
        ("case_cost", DIFF_UNSAFE_COST),
        ("quantity", DIFF_UNSAFE_QUANTITY),
        ("amount", DIFF_UNSAFE_AMOUNT),
        ("record_count", DIFF_RECORD_COUNT),
    ])
    def test_identity_commercial_and_financial_fields_get_named_unsafe_codes(
        self, field, code,
    ):
        assert difference_codes([{"field": field}], []) == [code]

    def test_codes_are_deduplicated_and_ordered(self):
        codes = difference_codes(
            [{"field": "description"}, {"field": "description"},
             {"field": "case_cost"}], [],
        )
        assert codes == [DIFF_EXPECTED_DESCRIPTION, DIFF_UNSAFE_COST]


class TestReportingToolsAreReadOnly:
    @pytest.mark.parametrize("script", [
        "review_product_master_candidates.py",
        "report_commercial_conflicts.py",
        "reconcile_legacy_only.py",
        "report_description_policy_validation.py",
        "product_master_cutover_readiness.py",
    ])
    def test_no_reporting_script_can_mutate(self, script):
        source = (SCRIPTS / script).read_text()
        for forbidden in ("session.add", "INSERT ", "UPDATE ", "DELETE ", ".commit()"):
            assert forbidden not in source, f"{script} must not contain {forbidden!r}"

    @pytest.mark.parametrize("script", [
        "review_product_master_candidates.py",
        "report_commercial_conflicts.py",
        "reconcile_legacy_only.py",
    ])
    def test_psycopg_sessions_are_opened_read_only(self, script):
        assert "readonly=True" in (SCRIPTS / script).read_text()

    def test_the_review_aid_names_its_csv_as_an_input(self):
        source = (SCRIPTS / "review_product_master_candidates.py").read_text()
        assert "REVIEW_INPUT" in source

    def test_the_conflict_report_resolves_nothing(self):
        source = (SCRIPTS / "report_commercial_conflicts.py").read_text()
        assert '"resolved_by_system": False' in source
        assert "EVIDENCE_SUPPORTS_SINGLE_INTERPRETATION" in source

    def test_no_reporting_script_hardcodes_a_upc_or_vendor(self):
        import re

        for script in ("report_commercial_conflicts.py", "reconcile_legacy_only.py",
                       "review_product_master_candidates.py"):
            source = (SCRIPTS / script).read_text()
            # A bare 11+ digit literal would be a hardcoded product.
            assert not re.search(r"['\"]\d{11,13}['\"]", source), script


class TestLegacyOnlyClassification:
    def test_the_classifier_never_bridges_on_weak_signals(self):
        source = (SCRIPTS / "reconcile_legacy_only.py").read_text()
        for weak in ("description", "cost", "pack notation"):
            assert weak in source, "the doc must name what is NOT used"
        # The SQL must match on a recorded identifier, not on wording.
        assert "i.normalized_value = %s" in source
        assert "zfill" not in source

    def test_legacy_rows_are_never_written(self):
        source = (SCRIPTS / "reconcile_legacy_only.py").read_text()
        assert "product_case_mappings" in source
        for forbidden in ("UPDATE product_case_mappings", "DELETE FROM product_case_mappings"):
            assert forbidden not in source
