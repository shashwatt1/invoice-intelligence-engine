"""
tests/test_mapping_evidence_report.py — the read-only per-code mapping evidence report.

Pure parts only (the database end is tests/integration/test_mapping_evidence_report_db.py):

  * the decision status: resolver conflict, approved out of scope (another
    store), pending candidate in a scope the resolver reads, no candidate;
  * the evidence: conflicting, single-valued, identity only, none;
  * commercial multipliers and physical items per case are never merged;
  * a mapping without units, a rejected proposal, a flagged pricing row;
  * the scope a store's evidence is reported under;
  * no approval is ever inferred, the summary is deterministic, and the
    output guards (descriptions only into analysis/, files mode 600, the
    local-only target guard reused from the coverage report).
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import mapping_evidence_report as ev

ROOT = Path(__file__).resolve().parent.parent
INVOICE_STORE, HOLDER, OTHER = "store-physical", "store-holder", "store-other"


def pm(state="REVIEW_REQUIRED", units=12, scope=ev.SCOPE_GLOBAL, reads=True, row=7):
    return {"scope": scope, "store_kind": "source_identity", "store_label": "Global", "approval_state": state,
            "basis": "CASE_IS_SELLING_UNIT", "units": units, "resolver_reads": reads,
            "source_file": "seed.xlsx", "source_sheet": "Mappings", "source_row": row}


def legacy(units=24, scope=ev.SCOPE_OTHER, reads=False):
    return {"scope": scope, "store_kind": "physical", "store_label": "Other", "units_per_case": units,
            "source": "APPROVED", "proposal_status": "APPROVED", "resolver_reads": reads}


def pricing(stated=None, derived=12, conflicted=False, scope=ev.SCOPE_GLOBAL, sheet="Monarch Package", row=422):
    return {"scope": scope, "store_kind": "source_identity", "store_label": "Global", "distributor": "Monarch",
            "pricing_basis": "case", "package": "C12 24OZ", "items_per_case_stated": stated,
            "items_per_case_derived": derived, "derivation": "ratio", "is_conflicted": conflicted,
            "source_file": "data/reference/Beer Inventory.xlsx", "source_sheet": sheet, "source_row": row}


def proposal(status="PENDING", value=6, scope=ev.SCOPE_SAME_STORE):
    return {"scope": scope, "store_label": "Physical", "status": status, "value": value}


def verdict(*, categories=None, product_master=(), legacy_=(), proposals=(), pricing_=(), identity=False):
    return ev.classify(resolver_categories=categories or {"REQUIRES_MAPPING | identity_known_no_conversion": 1},
                       product_master=list(product_master), legacy=list(legacy_), proposals=list(proposals),
                       pricing=list(pricing_), identity_known=identity)


class TestDecisionStatus:
    def test_a_resolver_conflict_comes_first(self):
        v = verdict(categories={"CONFLICT | physical_global_disagree": 2},
                    product_master=[pm("APPROVED", 24), pm("APPROVED", 12, scope=ev.SCOPE_SAME_STORE)])
        assert v["decision_status"] == ev.DECISION_RESOLVER_CONFLICT
        assert v["evidence"] == ev.EVIDENCE_CONFLICTING
        assert v["commercial_multiplier"]["values"] == [12, 24]

    def test_another_stores_approved_legacy_mapping_is_evidence_not_a_resolution(self):
        v = verdict(legacy_=[legacy(24)])
        assert v["decision_status"] == ev.DECISION_APPROVED_OUT_OF_SCOPE
        assert v["commercial_multiplier"]["sources"] == {"24": ["legacy:other_store"]}
        assert v["requires_human_decision"] is True

    def test_an_approved_mapping_in_an_unread_code_only_store_is_out_of_scope(self):
        v = verdict(product_master=[pm("APPROVED", 6, scope=ev.SCOPE_OTHER, reads=False)])
        assert v["decision_status"] == ev.DECISION_APPROVED_OUT_OF_SCOPE

    def test_a_suggestion_awaiting_review_is_a_pending_candidate_never_an_approval(self):
        v = verdict(product_master=[pm("REVIEW_REQUIRED", 12)], pricing_=[pricing(derived=12)])
        assert v["decision_status"] == ev.DECISION_PENDING
        assert v["evidence"] == ev.EVIDENCE_SINGLE
        assert v["requires_human_decision"] is True
        assert "approved" not in json.dumps(v).lower().replace("approved_out_of_scope", "")

    def test_a_pending_mapping_the_resolver_would_not_read_is_not_a_candidate(self):
        v = verdict(product_master=[pm("PENDING", 12, scope=ev.SCOPE_OTHER, reads=False)])
        assert v["decision_status"] == ev.DECISION_NONE
        assert v["commercial_multiplier"]["values"] == [12]      # still shown as evidence

    def test_nothing_to_decide(self):
        assert verdict()["decision_status"] == ev.DECISION_NONE


class TestEvidence:
    def test_one_physical_count_is_single_valued_and_not_a_multiplier(self):
        v = verdict(pricing_=[pricing(stated=12, derived=12), pricing(derived=12, sheet="Monarch Singles", row=56)])
        assert v["evidence"] == ev.EVIDENCE_SINGLE
        assert v["physical_items_per_case"]["values"] == [12]
        assert v["commercial_multiplier"]["values"] == []           # 12 cans per case is not "multiply by 12"
        assert v["physical_items_per_case"]["sources"] == {"12": ["pricing:derived:global_holder",
                                                                  "pricing:stated:global_holder"]}

    def test_a_multiplier_and_a_different_physical_count_are_not_a_conflict(self):
        # a case sold as one unit (multiplier 1) that physically holds 18 cans
        v = verdict(legacy_=[legacy(1, scope=ev.SCOPE_SAME_STORE, reads=True)], pricing_=[pricing(derived=18)])
        assert v["evidence"] == ev.EVIDENCE_SINGLE
        assert (v["commercial_multiplier"]["values"], v["physical_items_per_case"]["values"]) == ([1], [18])

    def test_two_physical_counts_conflict(self):
        v = verdict(pricing_=[pricing(derived=12), pricing(derived=24, row=900)])
        assert v["evidence"] == ev.EVIDENCE_CONFLICTING
        assert v["physical_items_per_case"]["values"] == [12, 24]

    def test_stated_and_derived_disagreeing_on_one_row_conflict(self):
        assert verdict(pricing_=[pricing(stated=24, derived=12)])["evidence"] == ev.EVIDENCE_CONFLICTING

    def test_two_stores_with_different_multipliers_conflict_across_stores(self):
        v = verdict(legacy_=[legacy(24), legacy(12, scope=ev.SCOPE_SAME_STORE, reads=True)])
        assert v["evidence"] == ev.EVIDENCE_CONFLICTING
        assert v["commercial_multiplier"]["sources"] == {"12": ["legacy:same_store"], "24": ["legacy:other_store"]}

    def test_a_pricing_row_the_import_flagged_is_conflicting_even_with_one_value(self):
        v = verdict(pricing_=[pricing(derived=12, conflicted=True)])
        assert v["evidence"] == ev.EVIDENCE_CONFLICTING
        assert v["physical_items_per_case"]["source_flagged_conflict"] is True

    def test_a_mapping_without_units_adds_no_value_and_is_reported(self):
        v = verdict(product_master=[pm("REVIEW_REQUIRED", None)], identity=True)
        assert v["commercial_multiplier"]["values"] == []
        assert v["pending_without_units"] is True
        assert v["evidence"] == ev.EVIDENCE_IDENTITY_ONLY

    def test_pricing_rows_without_any_count_are_missing_units(self):
        assert verdict(pricing_=[pricing(derived=None)])["evidence"] == ev.EVIDENCE_NONE

    def test_rejected_values_are_kept_apart_and_never_count_as_evidence(self):
        v = verdict(proposals=[proposal("REJECTED", 6), proposal("PENDING", 12)],
                    product_master=[pm("REJECTED", 24)])
        assert v["commercial_multiplier"]["values"] == [12]
        assert v["previously_rejected_values"] == [6]

    def test_identity_only_and_none(self):
        assert verdict(identity=True)["evidence"] == ev.EVIDENCE_IDENTITY_ONLY
        assert verdict()["evidence"] == ev.EVIDENCE_NONE


class TestScope:
    def test_scopes(self):
        stores = {INVOICE_STORE}
        assert ev.scope_of(INVOICE_STORE, stores, HOLDER) == ev.SCOPE_SAME_STORE
        assert ev.scope_of(HOLDER, stores, HOLDER) == ev.SCOPE_GLOBAL
        assert ev.scope_of(OTHER, stores, HOLDER) == ev.SCOPE_OTHER
        assert ev.scope_of(HOLDER, stores, None) == ev.SCOPE_OTHER    # no configured holder: no global scope

    def test_the_resolver_reads_the_holder_and_a_physical_invoice_store_only(self):
        assert ev.resolver_reads(ev.SCOPE_GLOBAL, "source_identity")
        assert ev.resolver_reads(ev.SCOPE_SAME_STORE, "physical")
        assert not ev.resolver_reads(ev.SCOPE_SAME_STORE, "source_identity")
        assert not ev.resolver_reads(ev.SCOPE_OTHER, "physical")

    def test_proposal_units(self):
        assert [ev.proposal_units(v) for v in (6, "12", " 4 ", True, "six", None, 1.5)] == [6, 12, 4, None, None,
                                                                                         None, None]


def _code(code, lines, decision, evidence):
    return {"item_code": code, "occurrences": {"lines": lines}, "decision_status": decision, "evidence": evidence,
            "commercial_multiplier": {"values": []}, "physical_items_per_case": {"values": [12]}}


class TestSummaryAndOutput:
    CODES = [_code("002", 2, ev.DECISION_NONE, ev.EVIDENCE_NONE),
             _code("001", 1, ev.DECISION_PENDING, ev.EVIDENCE_SINGLE),
             _code("003", 1, ev.DECISION_NONE, ev.EVIDENCE_SINGLE)]

    def test_summary_counts_codes_and_lines(self):
        s = ev.summarize(self.CODES, {"NO_USABLE_CODE": 3})
        assert (s["distinct_codes"], s["unresolved_lines"], s["lines_without_usable_code"]) == (3, 4, 3)
        assert s["codes_by_evidence"] == {"none": 1, "single_valued": 2}
        assert s["lines_by_decision_and_evidence"] == {"no_candidate | none": 2, "no_candidate | single_valued": 1,
                                                       "pending_candidate | single_valued": 1}

    def test_summary_is_independent_of_input_order(self):
        a = ev.summarize(self.CODES, {})
        b = ev.summarize(list(reversed(self.CODES)), {})
        assert json.dumps(a) == json.dumps(b)

    def test_evidence_lists_sort_deterministically(self):
        rows = [pricing(row=9), pricing(row=1), pricing(row=5)]
        assert ev._sorted(rows) == ev._sorted(list(reversed(rows)))

    def test_rendered_summary_lists_codes_with_evidence_first_and_nothing_else(self):
        report = {"database": {"name": "pm_audit", "alembic_revisions": ["0030"]},
                  "settings": {"product_master_commercial_resolution": True},
                  "summary": ev.summarize(self.CODES, {}), "codes": self.CODES}
        text = ev.render_summary(report)
        assert text.index("001") < text.index("002") and text.index("003") < text.index("002")
        assert "2 codes with reusable evidence" in text and "1 codes without unit evidence" in text


class TestOutputGuards:
    def test_descriptions_need_an_output_file(self):
        with pytest.raises(ev.RefusedTargetError, match="needs --output"):
            ev.output_allowed(None, True)

    def test_descriptions_only_inside_analysis(self, tmp_path):
        with pytest.raises(ev.RefusedTargetError, match="inside analysis/"):
            ev.output_allowed(tmp_path / "x.json", True)
        with pytest.raises(ev.RefusedTargetError, match="inside analysis/"):
            ev.output_allowed(ROOT / "analysis" / ".." / "x.json", True)
        ev.output_allowed(ROOT / "analysis" / "evidence" / "x.json", True)
        ev.output_allowed(tmp_path / "x.json", False)                  # no descriptions: any path

    def test_files_are_written_mode_600_in_mode_700_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "report.json"
        ev.write_private(target, "{}")
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "a" / "b").stat().st_mode) == 0o700

    def test_the_cli_refuses_a_remote_url_without_printing_it(self):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
               "DATABASE_URL": "postgresql+asyncpg://user:SECRETPW@db.x.supabase.co:5432/postgres"}
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "mapping_evidence_report.py"),
                                 "--database", "postgres"], capture_output=True, text=True, env=env, cwd=ROOT)
        assert result.returncode == 2 and "REFUSED" in result.stderr
        assert "SECRETPW" not in result.stderr + result.stdout

    def test_the_cli_refuses_descriptions_outside_analysis_before_connecting(self, tmp_path):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
               "DATABASE_URL": "postgresql+asyncpg://user:SECRETPW@localhost:1/nothing"}
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "mapping_evidence_report.py"),
                                 "--database", "invoice_test", "--include-descriptions",
                                 "--output", str(tmp_path / "x.json")],
                                capture_output=True, text=True, env=env, cwd=ROOT)
        assert result.returncode == 2 and "inside analysis/" in result.stderr
        assert not (tmp_path / "x.json").exists()
