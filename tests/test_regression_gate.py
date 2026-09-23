"""
tests/test_regression_gate.py — regression-gate classification logic (no DB).

Exercises _check_golden/_check_difficult directly against constructed
`seen` snapshots, so the historical_baseline / approved_target / observed
separation is proven without a live database. The gate's DB-reading half
(_observe, run()) is exercised live by `python scripts/regression_gate.py`
itself, part of P1's manual validation — this file covers the pure
classification logic that decides GOLDEN/DIFFICULT verdicts.
"""

from __future__ import annotations

from scripts.regression_gate import DIFFICULT, GOLDEN, _check_difficult, _check_golden

UNIFIRST = DIFFICULT["2310090549"]
RED_BULL = DIFFICULT["2035546957"]
COCA_COLA = DIFFICULT["000007174"]


def _unifirst_seen(**overrides) -> dict:
    """A full observed-state snapshot for the UniFirst approved_target, with overrides."""
    seen = {
        "status": "VALIDATED", "prompt_version": "v10", "grand_total": 202.30,
        "subtotal": 191.04, "tax_amount": 11.26, "line_item_math_failures": 0,
        "products_plus_charges": 191.04, "edi_sha256": None,
    }
    seen.update(overrides)
    return seen


class TestHistoricalBaselinePreserved:
    """1. Historical UniFirst v7/REVIEW_REQUIRED state is preserved as baseline information."""

    def test_historical_baseline_is_present_and_unchanged(self):
        assert UNIFIRST["historical_baseline"] == {
            "status": "REVIEW_REQUIRED", "grand_total": 202.30,
            "subtotal": 191.04, "tax_amount": 11.26, "prompt_version": "v7",
        }

    def test_historical_baseline_never_gates_the_verdict_by_itself(self):
        # A `seen` snapshot that matches approved_target exactly, but
        # differs from historical_baseline on every key: the baseline
        # must not veto or otherwise interfere with that verdict.
        seen = _unifirst_seen()
        finding = _check_difficult("2310090549", UNIFIRST, seen, before=None)
        assert finding.verdict == "IMPROVEMENT"
        assert not any("historical" in d and "moved" not in d for d in finding.detail)


class TestApprovedTargetSatisfied:
    """2. Current UniFirst v10/VALIDATED state satisfies the approved target."""

    def test_current_state_matches_every_approved_target_key(self):
        seen = _unifirst_seen()
        assert all(seen[k] == v for k, v in UNIFIRST["approved_target"].items())

    def test_check_difficult_reports_ok_shaped_success_for_the_real_current_state(self):
        finding = _check_difficult("2310090549", UNIFIRST, _unifirst_seen(), before=None)
        assert finding.verdict == "IMPROVEMENT"
        assert finding.klass == "DIFFICULT"


class TestImprovementReported:
    """3. Gate reports IMPROVEMENT when moving from historical baseline to approved target."""

    def test_full_v10_validated_state_is_improvement(self):
        finding = _check_difficult("2310090549", UNIFIRST, _unifirst_seen(), before=None)
        assert finding.verdict == "IMPROVEMENT"
        assert "approved target reached" in finding.detail

    def test_detail_names_what_moved_from_the_historical_baseline(self):
        finding = _check_difficult("2310090549", UNIFIRST, _unifirst_seen(), before=None)
        moved_line = next(d for d in finding.detail if d.startswith("moved from historical baseline"))
        assert "status" in moved_line
        assert "prompt_version" in moved_line


class TestFutureDeteriorationIsRegression:
    """4. A future state that violates the approved target is reported as REGRESSION."""

    def test_reverting_to_review_required_is_a_regression(self):
        seen = _unifirst_seen(status="REVIEW_REQUIRED")
        finding = _check_difficult("2310090549", UNIFIRST, seen, before=None)
        assert finding.verdict == "REGRESSION"
        assert any("status" in d for d in finding.detail)

    def test_wrong_prompt_version_is_a_regression(self):
        seen = _unifirst_seen(prompt_version="v9")
        finding = _check_difficult("2310090549", UNIFIRST, seen, before=None)
        assert finding.verdict == "REGRESSION"

    def test_line_item_math_failures_above_zero_is_a_regression(self):
        seen = _unifirst_seen(line_item_math_failures=1)
        finding = _check_difficult("2310090549", UNIFIRST, seen, before=None)
        assert finding.verdict == "REGRESSION"
        assert any("line_item_math_failures" in d for d in finding.detail)

    def test_wrong_grand_total_is_a_regression(self):
        seen = _unifirst_seen(grand_total=999.99)
        finding = _check_difficult("2310090549", UNIFIRST, seen, before=None)
        assert finding.verdict == "REGRESSION"
        assert any("grand_total" in d for d in finding.detail)

    def test_still_sitting_exactly_at_the_historical_baseline_is_unresolved_not_regression(self):
        # Never reprocessed at all: nothing has moved, so this is not a
        # regression against the approved target — it is a documented,
        # still-open case.
        seen = _unifirst_seen(
            status="REVIEW_REQUIRED", prompt_version="v7", grand_total=202.30,
            subtotal=191.04, tax_amount=11.26,
        )
        finding = _check_difficult("2310090549", UNIFIRST, seen, before=None)
        assert finding.verdict == "UNRESOLVED"


class TestGoldenAndDifficultClassUnchanged:
    """5, 6, 7. Existing golden invoices, Red Bull, and Coca-Cola remain unaffected."""

    def test_a_golden_case_matching_its_contract_is_ok(self):
        spec = GOLDEN["3376587"]
        seen = {
            "edi_sha256": spec["edi_sha256"], "edi_records": spec["records"],
            "edi_b_records": spec["b_records"], "status": spec["status"],
            "edi_crlf": True, "edi_trailing_newline": True,
        }
        finding = _check_golden("3376587", spec, seen, before=None)
        assert finding.verdict == "OK"

    def test_a_golden_case_with_a_changed_hash_is_still_a_regression(self):
        """9. Existing gate behavior for genuine golden regressions remains intact."""
        spec = GOLDEN["1000540"]
        seen = {
            "edi_sha256": "0" * 64, "edi_records": spec["records"],
            "edi_b_records": spec["b_records"], "status": spec["status"],
            "edi_crlf": True, "edi_trailing_newline": True,
        }
        finding = _check_golden("1000540", spec, seen, before=None)
        assert finding.verdict == "REGRESSION"
        assert any("SHA-256" in d for d in finding.detail)

    def test_red_bull_still_uses_expect_only_no_approved_target(self):
        assert "approved_target" not in RED_BULL
        seen = {
            "status": "VALIDATED", "grand_total": 329.53, "corrected_fields": ["grand_total"],
            "edi_sha256": RED_BULL["edi_sha256"],
        }
        finding = _check_difficult("2035546957", RED_BULL, seen, before=None)
        assert finding.verdict == "OK"

    def test_red_bull_governed_correction_still_regresses_if_grand_total_moves(self):
        seen = {
            "status": "VALIDATED", "grand_total": 1.00, "corrected_fields": ["grand_total"],
            "edi_sha256": RED_BULL["edi_sha256"],
        }
        finding = _check_difficult("2035546957", RED_BULL, seen, before=None)
        assert finding.verdict == "REGRESSION"

    def test_coca_cola_still_uses_expect_only_no_approved_target(self):
        assert "approved_target" not in COCA_COLA
        seen = {"status": "REVIEW_REQUIRED", "grand_total": None, "subtotal": None}
        finding = _check_difficult("000007174", COCA_COLA, seen, before=None)
        assert finding.verdict == "OK"


class TestNoConfusionBetweenBaselineTargetAndObserved:
    """8. The gate does not confuse historical baseline, approved target, and current observed state."""

    def test_the_three_concepts_are_distinct_dict_keys_on_the_same_spec(self):
        assert set(UNIFIRST["historical_baseline"]) & set(UNIFIRST["approved_target"])
        assert UNIFIRST["historical_baseline"]["status"] != UNIFIRST["approved_target"]["status"]
        assert UNIFIRST["historical_baseline"]["prompt_version"] != UNIFIRST["approved_target"]["prompt_version"]

    def test_matching_the_target_never_gets_reported_as_matching_the_baseline(self):
        finding = _check_difficult("2310090549", UNIFIRST, _unifirst_seen(), before=None)
        assert not any("still at historical baseline" in d for d in finding.detail)

    def test_matching_the_baseline_never_gets_reported_as_reaching_the_target(self):
        seen = _unifirst_seen(
            status="REVIEW_REQUIRED", prompt_version="v7", grand_total=202.30,
            subtotal=191.04, tax_amount=11.26,
        )
        finding = _check_difficult("2310090549", UNIFIRST, seen, before=None)
        assert "approved target reached" not in finding.detail

    def test_seen_is_the_only_thing_that_can_change_the_verdict_the_spec_is_fixed(self):
        # Calling twice with the identical spec but two different observed
        # states must not mutate the spec (historical_baseline/approved_target
        # stay exactly as authored).
        before_spec = dict(UNIFIRST)
        _check_difficult("2310090549", UNIFIRST, _unifirst_seen(), before=None)
        _check_difficult("2310090549", UNIFIRST, _unifirst_seen(status="REVIEW_REQUIRED"), before=None)
        assert UNIFIRST["historical_baseline"] == before_spec["historical_baseline"]
        assert UNIFIRST["approved_target"] == before_spec["approved_target"]
