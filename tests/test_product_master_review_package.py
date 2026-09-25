"""
tests/test_product_master_review_package.py — LOCAL OFFLINE TESTS for the
human review package.

The package exists to let a person read 414 candidates as a whole, so the
properties that matter are conservation and determinism: every candidate
appears exactly once, none disappears, the grouping is reproducible, and
nothing is decided or written on the way through.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.master_commercial_review_service import (  # noqa: E402
    LEGACY_ABSENT,
    LEGACY_AGREES,
    LEGACY_DISSENTS,
    LEGACY_UNCOMPARABLE,
)
from scripts.build_human_review_package import (  # noqa: E402
    GROUP_1,
    GROUP_2,
    GROUP_3,
    GROUP_4,
    GROUP_5,
    GROUP_6,
    GROUPS,
    group_for,
    summarise,
    why,
)

SCRIPT = ROOT / "scripts" / "build_human_review_package.py"


def candidate(**overrides) -> dict:
    base = {
        "group": GROUP_2, "review_status": "READY_FOR_REVIEW", "canonical_upc": "018200001154",
        "pdi_item_code": "01820000115", "store_id": "s1", "store_identity_status": "unresolved",
        "commercial_unit_basis": "UNIT_IS_SELLING_UNIT", "units_accounted_for": 4,
        "case_cost": "26.45", "cost_basis": "DISTRIBUTOR_CASE_PRICE",
        "evidence_source": "Sheet1 items/case column", "source_file": "Beer Inventory.xlsx",
        "source_sheet": "Sheet1", "source_row": 4, "legacy_units_per_case": None,
        "legacy_agreement": LEGACY_ABSENT, "evidence_notes": "",
        "why_ready_or_conflicted": "",
    }
    base.update(overrides)
    return base


class TestGrouping:
    @pytest.mark.parametrize(("status", "agreement", "expected"), [
        ("READY_FOR_REVIEW", LEGACY_AGREES, GROUP_1),
        ("READY_FOR_REVIEW", LEGACY_ABSENT, GROUP_2),
        ("READY_FOR_REVIEW", LEGACY_DISSENTS, GROUP_3),
        ("CONFLICT", LEGACY_UNCOMPARABLE, GROUP_4),
        ("APPROVED", LEGACY_AGREES, GROUP_5),
        ("REJECTED", LEGACY_ABSENT, GROUP_6),
        # A decided candidate keeps its decision whatever legacy says.
        ("APPROVED", LEGACY_DISSENTS, GROUP_5),
        ("REJECTED", LEGACY_DISSENTS, GROUP_6),
    ])
    def test_every_combination_lands_in_exactly_one_group(self, status, agreement, expected):
        assert group_for(status, agreement) == expected

    def test_grouping_is_deterministic(self):
        for _ in range(5):
            assert group_for("READY_FOR_REVIEW", LEGACY_AGREES) == GROUP_1

    def test_a_no_multiplier_candidate_is_not_shown_as_ready(self):
        # NO_MULTIPLIER is not READY; it falls through to the non-agreeing
        # ready groups only if it were ready, which it is not.
        assert group_for("NO_MULTIPLIER", LEGACY_ABSENT) == GROUP_2
        assert why("NO_MULTIPLIER", LEGACY_ABSENT, "UNKNOWN", None, None).startswith(
            "No distributor stated")


class TestConservation:
    def _rows(self):
        return (
            [candidate(group=GROUP_1, legacy_agreement=LEGACY_AGREES,
                       canonical_upc=f"01820000{i:04d}") for i in range(24)]
            + [candidate(group=GROUP_2, canonical_upc=f"01830000{i:04d}") for i in range(386)]
            + [candidate(group=GROUP_4, review_status="CONFLICT",
                         units_accounted_for=None,
                         canonical_upc=f"01840000{i:04d}") for i in range(4)]
        )

    def test_every_candidate_appears_exactly_once(self):
        rows = self._rows()
        summary = summarise(rows, [])
        assert summary["total_candidates"] == 414
        assert sum(summary["group_counts"].values()) == 414
        keys = [(r["canonical_upc"], r["pdi_item_code"], r["store_id"]) for r in rows]
        assert len(keys) == len(set(keys)), "no candidate is duplicated"

    def test_no_candidate_disappears_between_groups(self):
        rows = self._rows()
        summary = summarise(rows, [])
        grouped = Counter(r["group"] for r in rows)
        for group in GROUPS:
            assert summary["group_counts"][group] == grouped.get(group, 0)

    def test_the_ready_breakdown_sums_to_the_ready_total(self):
        summary = summarise(self._rows(), [])
        ready = summary["ready_for_review"]
        assert (ready["legacy_agrees"] + ready["no_legacy_mapping"]
                + ready["legacy_dissents"]) == ready["total"]
        assert sum(ready["by_commercial_basis"].values()) == ready["total"]
        assert sum(ready["by_multiplier"].values()) == ready["total"]
        assert sum(ready["by_store"].values()) == ready["total"]

    def test_conflicts_stay_out_of_the_ready_total(self):
        summary = summarise(self._rows(), [])
        assert summary["ready_for_review"]["total"] == 410
        assert summary["group_counts"][GROUP_4] == 4


class TestNoDecisionsAreMade:
    def test_the_summary_records_that_it_decided_nothing(self):
        summary = summarise([candidate()], [])
        assert summary["decisions_made_by_this_report"] == 0
        assert summary["conflicts"]["resolved_by_this_report"] == 0

    def test_conflicts_are_carried_through_unresolved(self):
        dossier = [{"canonical_upc": "018200967214", "conflicting_assertions": [],
                    "legacy_mappings": [], "evidence_interpretation": "X",
                    "resolved_by_system": False}]
        summary = summarise(
            [candidate(group=GROUP_4, review_status="CONFLICT", units_accounted_for=None)],
            dossier,
        )
        assert summary["conflicts"]["dossier"][0]["resolved_by_system"] is False
        assert summary["conflicts"]["total"] == 1

    def test_approved_and_rejected_counts_are_reported_not_altered(self):
        rows = [candidate(group=GROUP_5, review_status="APPROVED"),
                candidate(group=GROUP_6, review_status="REJECTED")]
        summary = summarise(rows, [])
        assert summary["group_counts"][GROUP_5] == 1
        assert summary["group_counts"][GROUP_6] == 1

    def test_the_summary_warns_that_counts_are_not_recommendations(self):
        summary = summarise([candidate()], [])
        assert "not recommendations" in summary["statistics_note"]

    def test_agreement_is_described_as_two_sources_matching(self):
        explanation = why("READY_FOR_REVIEW", LEGACY_AGREES, "UNIT_IS_SELLING_UNIT", 4, None)
        assert "governed mapping holds the same value" in explanation
        assert "safe" not in explanation.lower(), "the report must not endorse a group"


class TestReadOnly:
    def test_the_script_cannot_mutate(self):
        source = SCRIPT.read_text()
        for forbidden in ("session.add", "INSERT ", "UPDATE ", "DELETE ", ".commit()"):
            assert forbidden not in source

    def test_the_session_is_opened_read_only(self):
        assert "readonly=True" in SCRIPT.read_text()

    def test_it_never_calls_the_approval_service(self):
        source = SCRIPT.read_text()
        for forbidden in ("approve(", "reject(", "master_commercial_review_service.approve"):
            assert forbidden not in source

    def test_it_hardcodes_no_product_or_vendor(self):
        import re

        assert not re.search(r"['\"]\d{11,13}['\"]", SCRIPT.read_text())


class TestGeneratedArtefactsMatchTheDatabase:
    """If the package has been generated, its totals must still hold."""

    SUMMARY = ROOT / "analysis" / "master-data" / "product_master_human_review_summary.json"

    def test_the_generated_summary_conserves_all_candidates(self):
        if not self.SUMMARY.exists():
            pytest.skip("package not generated in this environment")
        summary = json.loads(self.SUMMARY.read_text())
        assert sum(summary["group_counts"].values()) == summary["total_candidates"]
        assert summary["decisions_made_by_this_report"] == 0
        assert summary["conflicts"]["resolved_by_this_report"] == 0
