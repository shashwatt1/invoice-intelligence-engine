"""
The controlled replay harness — tests/test_extraction_replay.py

One paid model call over stored OCR text, scored against the #48288200 acceptance figures. Nothing here calls a model:
the call is replaced, and the point is that the harness refuses to spend anything without the explicit approvals, never
writes to a database, never changes the active prompt, and judges a replay with the real validation and completeness gate.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from app.prompts import invoice_extraction
from scripts import extraction_replay as replay
from tests import synthetic_invoice as syn
from tests.test_extraction_reliability import extracted

SCRIPT = Path(replay.__file__)
OCR = syn.ocr_text()


def by_name(rows):
    return {r["check"]: r for r in rows}


class TestTheEstimate:
    def test_v11_reproduces_the_real_call_it_is_calibrated_on(self):
        e = replay.estimate(OCR, "v11", photo_count=4)
        assert e.cost_usd_expected == pytest.approx(0.0733, abs=0.0005)        # the logged cost of the real v11 call

    def test_v12_costs_a_little_more_and_the_ceiling_is_above_the_expectation(self):
        v11, v12 = replay.estimate(OCR, "v11", photo_count=4), replay.estimate(OCR, "v12", photo_count=4)
        assert v11.cost_usd_expected < v12.cost_usd_expected < 2 * v11.cost_usd_expected
        assert v12.cost_usd_low < v12.cost_usd_expected < v12.cost_usd_high
        assert 0.07 < v12.total_usd_expected < 0.12                            # cents, not dollars

    def test_the_capped_call_has_a_hard_bound_above_the_expectation(self):
        e = replay.estimate(OCR, "v12", photo_count=4)
        assert e.max_output_tokens == replay.MAX_OUTPUT_TOKENS
        assert e.output_tokens_expected < e.max_output_tokens          # the cap leaves room for the expected answer
        assert e.total_usd_expected < e.total_usd_bounded

    def test_the_stored_text_is_reused_so_no_ocr_is_paid_for(self):
        e = replay.estimate(OCR, "v12", photo_count=4)
        assert e.ocr_usd_replay == 0.0 and e.ocr_usd_if_reread == pytest.approx(0.006)
        assert e.total_usd_expected == e.cost_usd_expected

    def test_truncation_is_flagged_when_the_text_would_exceed_the_token_budget(self):
        assert replay.estimate(OCR, "v12", photo_count=4).truncated is False
        assert replay.estimate("x" * 30_000, "v12", photo_count=4).truncated is True


class TestScoring:
    def test_a_correct_extraction_meets_every_acceptance_figure(self):
        rows = replay.score(extracted(syn.benchmark_lines()), OCR)
        assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
        figures = by_name(rows)
        assert (figures["product lines"]["actual"], figures["cases"]["actual"], figures["units"]["actual"]) == ("61", "68", "958")
        assert figures["amount due"]["actual"] == "1844.48" and figures["completeness"]["actual"] == "COMPLETE"

    def test_a_replay_that_loses_rows_fails_lines_cases_units_and_completeness(self):
        failed = {r["check"] for r in replay.score(extracted(syn.benchmark_lines()[:40]), OCR) if not r["ok"]}
        assert {"product lines", "cases", "units", "completeness", "blocking checks"} <= failed

    def test_a_replay_that_drops_the_charges_fails_them(self):
        failed = {r["check"] for r in replay.score(extracted(syn.benchmark_lines(), charges=False), OCR) if not r["ok"]}
        assert "state/local charges" in failed


class TestNothingIsSpentWithoutTheApprovals:
    @pytest.fixture
    def run(self, monkeypatch, tmp_path):
        ocr = tmp_path / "ocr.txt"
        ocr.write_text(OCR, encoding="utf-8")
        calls = []

        async def fake_execute(text, version):
            calls.append(version)
            return extracted(syn.benchmark_lines()), {
                "prompt_version": version, "model": "gpt-4o", "input_tokens": 12_400, "output_tokens": 5_100,
                "estimated_cost_usd": 0.082, "latency_ms": 30_000, "ocr_text_truncated": False}

        monkeypatch.setattr(replay, "execute", fake_execute)

        def go(*argv):
            monkeypatch.setattr(sys, "argv", ["extraction_replay.py", "--ocr-file", str(ocr), "--photos", "4", *argv])
            return replay.main()
        go.calls, go.dir = calls, tmp_path
        return go

    def test_the_default_is_an_estimate_and_calls_nothing(self, run, capsys):
        assert run() == 0
        assert run.calls == []
        assert "Estimate only" in capsys.readouterr().out

    @pytest.mark.parametrize("missing", ["--prompt-version", "--max-cost-usd", "--output"])
    def test_execute_needs_the_version_the_ceiling_and_the_output(self, run, missing):
        args = {"--prompt-version": "v12", "--max-cost-usd": "0.15", "--output": "OUT"}
        args.pop(missing)
        flat = ["--execute"] + [x for k, v in args.items() for x in (k, v.replace("OUT", str(run.dir / "out.json")))]
        with pytest.raises(SystemExit):
            run(*flat)
        assert run.calls == []

    def test_a_ceiling_below_the_high_estimate_refuses(self, run):
        with pytest.raises(SystemExit, match="below the hard bound"):
            run("--execute", "--prompt-version", "v12", "--max-cost-usd", "0.05", "--output", str(run.dir / "o.json"))
        assert run.calls == []

    def test_an_existing_output_refuses_so_it_is_one_replay_not_a_loop(self, run):
        out = run.dir / "o.json"
        out.write_text("{}")
        with pytest.raises(SystemExit, match="one controlled replay"):
            run("--execute", "--prompt-version", "v12", "--max-cost-usd", "0.15", "--output", str(out))
        assert run.calls == []

    def test_an_output_inside_the_repository_refuses(self, run):
        inside = replay.ROOT / "replay_should_not_exist.json"
        with pytest.raises(SystemExit, match="inside the repository"):
            run("--execute", "--prompt-version", "v12", "--max-cost-usd", "0.15", "--output", str(inside))
        assert run.calls == [] and not inside.exists()

    def test_with_every_approval_it_calls_once_scores_and_writes_only_the_output(self, run):
        out = run.dir / "result.json"
        code = run("--execute", "--prompt-version", "v12", "--max-cost-usd", "0.15", "--output", str(out))
        assert code == 0 and run.calls == ["v12"]
        payload = json.loads(out.read_text())
        assert payload["all_ok"] is True and payload["usage"]["prompt_version"] == "v12"
        assert [p.name for p in run.dir.iterdir() if p.name not in ("ocr.txt", "result.json")] == []


class TestItCannotWriteOrChangeThePrompt:
    def test_the_script_has_no_database_write_path(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("commit(", "session.add", "get_session_factory", "persistence_service", "pipeline_service",
                          "InvoiceProcessingPipeline", "proposal_service", "INSERT ", "UPDATE ", "DELETE ", "ACTIVE_VERSION ="):
            assert forbidden not in source, forbidden
        assert "set_session(readonly=True" in source

    def test_the_active_prompt_is_unchanged_and_v12_stays_registered(self):
        assert invoice_extraction.ACTIVE_VERSION == "v11"
        assert invoice_extraction.get_prompt("v12").version == "v12"

    def test_the_execute_path_caps_the_output(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert "OpenAIProvider(max_output_tokens=MAX_OUTPUT_TOKENS)" in source

    def test_the_execute_path_allows_exactly_one_billed_attempt(self):
        source = SCRIPT.read_text(encoding="utf-8")
        assert re.search(r'OPENAI_MAX_RETRIES"\] = "0"', source)
        assert re.search(r'OPENAI_TIMEOUT_SECONDS"\] = "120"', source)
