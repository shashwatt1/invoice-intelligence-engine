"""
tests/test_mapping_coverage_report.py — the read-only mapping coverage report.

Every resolution below comes from the live, pure resolver
(commercial_resolution.resolve_code) or from the exact objects its flag-off
branch builds; the report only classifies what the resolver answered:

  * resolved paths (physical override, global, legacy) and the legacy
    disagreement note when an approved Product Master value wins;
  * CONFLICT sub-reasons (two approved in one scope, physical vs global,
    approved without units);
  * REQUIRES_MAPPING sub-reasons (not approved, Product Master disabled,
    identity known without a conversion, absent from the corpus);
  * no usable code, store pending, and code-only versus physical stores;
  * identifier shapes, summary rates, baseline comparison and regressions;
  * the local-only target guard, and that no description or price is output.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.product_master.commercial_resolution import (
    PATH_CONFLICT,
    PATH_LEGACY_FALLBACK,
    PATH_PRODUCT_MASTER_GLOBAL,
    PATH_PRODUCT_MASTER_PHYSICAL,
    PATH_REQUIRES_MAPPING,
    CodeResolution,
    Scope,
    ScopedMapping,
    resolve_code,
)
from scripts import mapping_coverage_report as report

ROOT = Path(__file__).resolve().parent.parent
CODE = "01820053030"
PHYSICAL = Scope("store-physical", "AF NV Midler")
GLOBAL = Scope("store-47708760", "Global (held under Item Sales · 47708760)")


def mapping(state="APPROVED", units=24, basis="CASE_IS_SELLING_UNIT", mid="m1"):
    return ScopedMapping(mid, "p1", state, basis, units)


def resolve(*, physical=(), global_=(), legacy=None, physical_scope=PHYSICAL, global_scope=GLOBAL):
    return resolve_code(CODE, physical=physical, global_=global_, legacy_units=legacy,
                        physical_scope=physical_scope, global_scope=global_scope)


def flag_off(legacy=None):
    """Exactly what resolve_store_codes builds when PRODUCT_MASTER_COMMERCIAL_RESOLUTION is off."""
    if legacy is not None:
        return CodeResolution(CODE, PATH_LEGACY_FALLBACK, legacy, legacy_units=legacy)
    return CodeResolution(CODE, PATH_REQUIRES_MAPPING, None)


def item(sku=CODE, sort=1):
    return SimpleNamespace(product_sku=sku, sort_order=sort, description="SECRET DESCRIPTION",
                           unit_price=12.34, line_total=56.78)


def line(resolved, *, sku=CODE, store_kind="physical", flag_on=True, states=(), identity=()):
    code = "01820053030" if sku == CODE else None
    return report.line_record("inv-1", "508655", item(sku), store_kind=store_kind, flag_on=flag_on,
                              resolved_by_code={code: resolved} if resolved and code else {},
                              states_by_code={CODE: list(states)} if states else {},
                              identity_codes=set(identity))


class TestResolvedPaths:
    def test_an_approved_physical_override_wins(self):
        rec = line(resolve(physical=[mapping(units=12)], global_=[mapping(units=12)]))
        assert (rec["category"], rec["sub_reason"], rec["units_per_case"]) == (PATH_PRODUCT_MASTER_PHYSICAL, None, 12)
        assert rec["scope"] == "AF NV Midler"

    def test_an_approved_global_mapping_resolves(self):
        rec = line(resolve(global_=[mapping(units=4)]))
        assert (rec["category"], rec["units_per_case"], rec["legacy_disagreement"]) == (
            PATH_PRODUCT_MASTER_GLOBAL, 4, False)

    def test_legacy_resolves_when_nothing_is_approved(self):
        rec = line(resolve(global_=[mapping(state="REVIEW_REQUIRED")], legacy=6),
                   states=["REVIEW_REQUIRED"])
        assert (rec["category"], rec["units_per_case"], rec["legacy_units"]) == (PATH_LEGACY_FALLBACK, 6, 6)

    def test_an_approved_value_that_disagrees_with_legacy_wins_and_is_flagged(self):
        rec = line(resolve(global_=[mapping(units=24)], legacy=1))
        assert (rec["category"], rec["units_per_case"], rec["legacy_units"], rec["legacy_disagreement"]) == (
            PATH_PRODUCT_MASTER_GLOBAL, 24, 1, True)

    def test_an_agreeing_legacy_value_is_not_a_disagreement(self):
        assert line(resolve(global_=[mapping(units=24)], legacy=24))["legacy_disagreement"] is False


class TestConflicts:
    @pytest.mark.parametrize("kwargs, sub", [
        ({"global_": [mapping(units=12, mid="a"), mapping(units=12, mid="b")]}, report.SUB_MULTIPLE_APPROVED),
        ({"physical": [mapping(units=12)], "global_": [mapping(units=24)]}, report.SUB_PHYSICAL_GLOBAL_DISAGREE),
        ({"global_": [mapping(units=None)]}, report.SUB_APPROVED_WITHOUT_UNITS),
    ])
    def test_each_conflict_has_its_reason_and_no_units(self, kwargs, sub):
        rec = line(resolve(**kwargs, legacy=6))
        assert (rec["category"], rec["sub_reason"], rec["units_per_case"], rec["scope"]) == (PATH_CONFLICT, sub, None, None)
        assert rec["legacy_units"] == 6 and rec["legacy_disagreement"] is False


class TestRequiresMapping:
    def test_a_mapping_awaiting_review_is_not_approved(self):
        rec = line(resolve(global_=[mapping(state="REVIEW_REQUIRED")]), states=["REVIEW_REQUIRED"])
        assert (rec["category"], rec["sub_reason"]) == (PATH_REQUIRES_MAPPING, report.SUB_NOT_APPROVED)
        assert rec["product_master_states"] == ["REVIEW_REQUIRED"]

    def test_a_rejected_mapping_is_also_not_approved(self):
        rec = line(resolve(global_=[mapping(state="REJECTED")]), states=["REJECTED"])
        assert rec["sub_reason"] == report.SUB_NOT_APPROVED

    def test_with_the_flag_off_an_approved_mapping_is_reported_as_disabled(self):
        rec = line(flag_off(), flag_on=False, states=["APPROVED"])
        assert (rec["category"], rec["sub_reason"]) == (PATH_REQUIRES_MAPPING, report.SUB_PM_DISABLED)

    def test_with_the_flag_off_an_unapproved_mapping_is_still_not_approved(self):
        assert line(flag_off(), flag_on=False, states=["REVIEW_REQUIRED"])["sub_reason"] == report.SUB_NOT_APPROVED

    def test_with_the_flag_off_legacy_still_resolves(self):
        rec = line(flag_off(legacy=12), flag_on=False, states=["APPROVED"])
        assert (rec["category"], rec["units_per_case"]) == (PATH_LEGACY_FALLBACK, 12)

    def test_a_known_product_without_a_conversion(self):
        rec = line(resolve(), identity=[CODE])
        assert rec["sub_reason"] == report.SUB_IDENTITY_NO_CONVERSION

    def test_a_code_outside_the_master_corpus(self):
        assert line(resolve())["sub_reason"] == report.SUB_ABSENT


class TestStoresAndCodes:
    def test_a_code_only_store_has_no_physical_scope_and_resolves_only_globally(self):
        resolved = resolve(physical=[], global_=[mapping(units=4)], physical_scope=None)
        rec = line(resolved, store_kind="source_identity")
        assert (rec["category"], rec["store_kind"]) == (PATH_PRODUCT_MASTER_GLOBAL, "source_identity")

    def test_a_line_without_a_usable_code(self):
        rec = line(None, sku="")
        assert (rec["category"], rec["item_code"], rec["identifier_shape"]) == (
            report.CATEGORY_NO_USABLE_CODE, None, "none")

    def test_an_all_zero_placeholder_is_not_a_code(self):
        assert line(None, sku="000000000000")["category"] == report.CATEGORY_NO_USABLE_CODE

    def test_a_store_pending_invoice_resolves_nothing(self):
        rec = line(None, store_kind=report.STORE_KIND_PENDING)
        assert (rec["category"], rec["sub_reason"]) == (report.CATEGORY_STORE_PENDING, None)

    def test_a_missing_resolution_for_a_code_is_an_error_not_a_guess(self):
        with pytest.raises(ValueError):
            report.classify(CODE, None, store_kind="physical", flag_on=True)

    @pytest.mark.parametrize("printed, shape, formatted", [
        ("018200530302", "upc_a_12", False),
        ("0-18200-53030-2", "upc_a_12", True),
        ("018200530305", "12_digits_invalid_check", False),
        ("12345670", "8_digits", False),
        ("4101010013663", "13_digits", False),
        ("10018200530301", "14_digits", False),
        ("ABC-123456", "6_digits", True),
        ("", "none", False),
        (None, "none", False),
    ])
    def test_identifier_shape_never_echoes_the_text(self, printed, shape, formatted):
        assert report.identifier_shape(printed) == shape
        assert report.is_formatted(printed) is formatted

    def test_no_description_or_price_reaches_the_output(self):
        rec = line(resolve(global_=[mapping()]))
        assert set(rec) == {"invoice_id", "invoice_number", "sort_order", "item_code", "identifier_shape",
                            "formatted", "store_kind", "category", "sub_reason", "units_per_case", "legacy_units",
                            "legacy_disagreement", "product_master_states", "scope"}
        assert "SECRET" not in str(rec) and "12.34" not in str(rec) and "56.78" not in str(rec)


def _lines():
    base = {"invoice_number": "1", "item_code": "c", "identifier_shape": "upc_a_12", "formatted": False,
            "legacy_units": None, "legacy_disagreement": False, "product_master_states": [], "scope": None,
            "invoice_store_id": "s1", "store_kind": "physical"}
    return [
        {**base, "invoice_id": "i1", "sort_order": 1, "category": PATH_LEGACY_FALLBACK, "sub_reason": None,
         "units_per_case": 6, "item_code": "a"},
        {**base, "invoice_id": "i1", "sort_order": 2, "category": PATH_REQUIRES_MAPPING,
         "sub_reason": report.SUB_ABSENT, "units_per_case": None, "item_code": "b"},
        {**base, "invoice_id": "i2", "sort_order": 1, "category": PATH_REQUIRES_MAPPING,
         "sub_reason": report.SUB_ABSENT, "units_per_case": None, "item_code": "b"},
        {**base, "invoice_id": "i2", "sort_order": 2, "category": report.CATEGORY_NO_USABLE_CODE,
         "sub_reason": None, "units_per_case": None, "item_code": None},
    ]


def _report(lines):
    invoices = [{"invoice_id": i, "mapping_complete": all(
        ln["category"] in report.RESOLVED for ln in lines if ln["invoice_id"] == i)} for i in ("i1", "i2")]
    return {"summary": report.summarize(lines, invoices), "lines": lines}


class TestSummaryAndComparison:
    def test_the_summary_counts_rates_and_the_manual_queue(self):
        s = _report(_lines())["summary"]
        assert (s["pdi_lines"], s["resolved_lines"], s["auto_resolution_rate"]) == (4, 1, 0.25)
        assert s["manual_queue_store_codes"] == 1          # the same store/code on two invoices is one decision
        assert s["lines_without_usable_code"] == 1 and s["invoices_mapping_complete"] == 0
        assert s["by_sub_reason"] == {f"{PATH_REQUIRES_MAPPING} | {report.SUB_ABSENT}": 2}

    def test_an_improvement_is_a_change_not_a_regression(self):
        before = _report(_lines())
        improved = _lines()
        improved[1] = {**improved[1], "category": PATH_PRODUCT_MASTER_GLOBAL, "sub_reason": None, "units_per_case": 12}
        diff = report.compare(before, _report(improved))
        assert (diff["lines_changed"], diff["regressions"]) == (1, [])
        assert diff["summary_deltas"]["by_category"] == {PATH_PRODUCT_MASTER_GLOBAL: 1, PATH_REQUIRES_MAPPING: -1}

    def test_a_resolved_line_that_stops_resolving_or_changes_units_is_a_regression(self):
        before = _report(_lines())
        worse = _lines()
        worse[0] = {**worse[0], "units_per_case": 12}
        diff = report.compare(before, _report(worse))
        assert len(diff["regressions"]) == 1 and diff["regressions"][0]["before"][2] == 6
        worse[0] = {**worse[0], "category": PATH_REQUIRES_MAPPING, "sub_reason": report.SUB_ABSENT,
                    "units_per_case": None}
        assert len(report.compare(before, _report(worse))["regressions"]) == 1

    def test_added_and_removed_lines_are_counted(self):
        before = _report(_lines())
        diff = report.compare(before, _report(_lines()[:3]))
        assert (diff["lines_removed"], diff["lines_added"]) == (1, 0)


class TestTargetGuard:
    def test_a_local_database_is_accepted_by_name(self):
        url = report.target_url("postgresql+asyncpg://u:p@localhost:5432/invoice_db", "pm_audit")
        assert (url.host, url.database) == ("localhost", "pm_audit")

    @pytest.mark.parametrize("configured", [
        "postgresql+asyncpg://u:p@db.abcdefgh.supabase.co:5432/postgres",
        "postgresql+asyncpg://u:p@10.0.0.5:5432/invoice_db",
        "postgresql+asyncpg://u:p@db:5432/invoice_db",
    ])
    def test_a_non_local_host_is_refused_before_connecting(self, configured):
        with pytest.raises(report.RefusedTargetError) as exc:
            report.target_url(configured, "pm_audit")
        assert exc.value.code == 2 and "not local" in str(exc.value)

    def test_the_database_must_be_named(self):
        with pytest.raises(report.RefusedTargetError):
            report.target_url("postgresql+asyncpg://u:p@localhost:5432/invoice_db", " ")

    def test_the_cli_refuses_a_remote_url_without_printing_it(self):
        env = {"PATH": "/usr/bin:/bin", "DATABASE_URL": "postgresql+asyncpg://user:SECRETPW@db.x.supabase.co:5432/postgres",
               "HOME": str(Path.home())}
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "mapping_coverage_report.py"),
                                 "--database", "postgres"], capture_output=True, text=True, env=env, cwd=ROOT)
        assert result.returncode == 2 and "REFUSED" in result.stderr
        assert "SECRETPW" not in result.stderr + result.stdout
