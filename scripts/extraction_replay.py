#!/usr/bin/env python
"""
Controlled extraction replay — ESTIMATES by default, never writes to a database.

    python scripts/extraction_replay.py --ocr-file ocr.txt                      # estimate only: no network, no key
    python scripts/extraction_replay.py --invoice-number 48288200 \\
        --dsn postgresql://...                                                  # read the stored OCR text (read-only)
    python scripts/extraction_replay.py --ocr-file ocr.txt --execute \\
        --prompt-version v12 --max-cost-usd 0.15 --output /outside/repo/replay.json

Re-runs ONE model call over OCR text that is already stored — no OCR is repeated — with a chosen prompt version, then
scores the answer with the real validation and completeness gate against the PepsiCo #48288200 acceptance figures
(61 lines, 68 cases, 958 units, subtotal 1,821.18, state/local charges 23.30, tax 0, amount due 1,844.48, COMPLETE,
nothing blocking, confidence not capped). Nothing is persisted, no invoice, proposal or mapping is touched, and the
active prompt is not changed.

Guards, all required to spend anything:
  * `--execute` (the default is an estimate that prints the cost and exits);
  * `--prompt-version` named explicitly (there is no default);
  * `--max-cost-usd` at or above the HARD BOUND — the call is capped at 7,500 output tokens, so its cost cannot
    exceed the (deterministic) input plus that; a lower ceiling refuses to run;
  * `--output` for the result, which must not already exist (one controlled replay, not a loop) and should be
    outside the repository: it holds the extracted lines of a real invoice.
The call is made once: retries are switched off (a failure is reported, never silently billed again) and the timeout
is raised above the 27 s the v11 call took, because the longer v12 answer would otherwise time out at the default 30 s.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.prompts.invoice_extraction import get_prompt  # noqa: E402
from app.schemas.extraction import ExtractedInvoice  # noqa: E402
from app.services.llm.openai_provider import (  # noqa: E402
    MODEL_PRICING_USD_PER_1M,
    estimate_cost_usd,
)
from app.services.validation.service import ValidationService  # noqa: E402

BASELINE_PROMPT = "v11"
# What the v11 call on #48288200 actually used (processing log, 2026-10-27): model gpt-4o-2024-08-06.
BASELINE_INPUT_TOKENS = 11_965
BASELINE_OUTPUT_TOKENS = 4_341
BASELINE_LATENCY_S = 27.1
MODEL = "gpt-4o"
CHARS_PER_TOKEN = 4
# v12 asks for printed_units and section_label on every line plus typed charges: about 12 extra output tokens per line
# and a few dozen for the charges. LOW/HIGH bracket the expected figure.
# First replay (2026-10-31, cap 6,000) was cut by the output cap: v12 needs more than 6,000 output tokens, so the extra
# per line is at least (6,000 - 4,341 - 60) / 61 = 26. 30 is used from here; it is still an estimate, not a measurement.
EXTRA_OUTPUT_PER_LINE = 30
EXTRA_OUTPUT_CHARGES = 60
OUTPUT_LOW, OUTPUT_HIGH = 0.95, 1.15
INPUT_HIGH = 1.10
# The call itself is capped, so its cost has a hard bound that does not depend on the output estimate: the
# (deterministic) input, at most 5% above the calibrated figure, plus MAX_OUTPUT_TOKENS of output. An answer that
# needs more is cut and fails as `length` rather than costing more.
MAX_OUTPUT_TOKENS = 7500
INPUT_BOUND = 1.05
# Google Vision DOCUMENT_TEXT_DETECTION list price per image, for the "if the photos were read again" comparison only.
OCR_USD_PER_IMAGE = 1.50 / 1000


@dataclass(frozen=True)
class Benchmark:
    lines: int = 61
    cases: int = 68
    units: int = 958
    subtotal: Decimal = Decimal("1821.18")
    charges: Decimal = Decimal("23.30")
    tax: Decimal = Decimal("0")
    amount_due: Decimal = Decimal("1844.48")


BENCHMARK_48288200 = Benchmark()


@dataclass(frozen=True)
class Estimate:
    prompt_version: str
    model: str
    input_tokens_expected: int
    input_tokens_high: int
    output_tokens_low: int
    output_tokens_expected: int
    output_tokens_high: int
    cost_usd_low: float
    cost_usd_expected: float
    cost_usd_high: float
    ocr_usd_replay: float          # the stored text is reused: no OCR is repeated
    ocr_usd_if_reread: float       # for comparison: the photos read again
    total_usd_expected: float
    total_usd_high: float
    max_output_tokens: int
    total_usd_bounded: float       # the most the capped call can cost; what --max-cost-usd is checked against
    ocr_chars: int
    truncated: bool


def estimate(ocr_text: str, prompt_version: str, *, photo_count: int, product_lines: int = BENCHMARK_48288200.lines,
             baseline_input: int = BASELINE_INPUT_TOKENS, baseline_output: int = BASELINE_OUTPUT_TOKENS,
             model: str = MODEL) -> Estimate:
    """
    Calibrated on the real v11 call: the same document and schema, so only the prompt text and the extra output
    fields differ. No tokenizer is needed and no network is used.
    """
    from app.core.config import get_settings

    old, new = get_prompt(BASELINE_PROMPT), get_prompt(prompt_version)
    prompt_delta = (len(new.system_prompt) - len(old.system_prompt)) // CHARS_PER_TOKEN
    input_expected = baseline_input + max(prompt_delta, 0) + 150          # +150: the three added schema fields
    input_high = int(input_expected * INPUT_HIGH)
    extra = (EXTRA_OUTPUT_PER_LINE * product_lines + EXTRA_OUTPUT_CHARGES) if prompt_version != BASELINE_PROMPT else 0
    out_expected = baseline_output + extra
    out_low, out_high = int(out_expected * OUTPUT_LOW), int(out_expected * OUTPUT_HIGH)

    def cost(tokens_in: int, tokens_out: int) -> float:
        value = estimate_cost_usd(model, tokens_in, tokens_out)
        if value is None:
            raise SystemExit(f"No price known for model {model!r}; known: {sorted(MODEL_PRICING_USD_PER_1M)}")
        return value

    low = cost(baseline_input, out_low)
    expected = cost(input_expected, out_expected)
    high = cost(input_high, out_high)
    bounded = cost(int(input_expected * INPUT_BOUND), MAX_OUTPUT_TOKENS)
    reread = photo_count * OCR_USD_PER_IMAGE
    budget_chars = get_settings().openai_max_tokens_per_document * CHARS_PER_TOKEN
    return Estimate(
        prompt_version=prompt_version, model=model, input_tokens_expected=input_expected, input_tokens_high=input_high,
        output_tokens_low=out_low, output_tokens_expected=out_expected, output_tokens_high=out_high,
        cost_usd_low=round(low, 4), cost_usd_expected=round(expected, 4), cost_usd_high=round(high, 4),
        ocr_usd_replay=0.0, ocr_usd_if_reread=round(reread, 4),
        total_usd_expected=round(expected, 4), total_usd_high=round(high, 4),
        max_output_tokens=MAX_OUTPUT_TOKENS, total_usd_bounded=round(bounded, 4),
        ocr_chars=len(ocr_text), truncated=len(ocr_text) > budget_chars)


def score(extracted: ExtractedInvoice, ocr_text: str, benchmark: Benchmark = BENCHMARK_48288200) -> list[dict[str, Any]]:
    """Judge a replayed extraction with the real validation and completeness gate; pure and offline."""
    result = ValidationService(review_threshold=0.80).validate_invoice(
        extracted, ocr_confidence=0.97, filename="replay", ocr_text=ocr_text)
    report = result.report
    rows = [li for li in extracted.line_items if (li.line_type or "product") == "product"]
    cases = sum(Decimal(str(li.quantity or 0)) for li in rows)
    cases = cases.quantize(Decimal(1)) if cases == cases.to_integral_value() else cases
    units = sum(li.printed_units or 0 for li in rows)
    charges = sum((Decimal(str(c.amount)) for c in (extracted.other_charges or [])), Decimal("0"))

    def money(value: float | None) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    completeness = (report.completeness or {}).get("status")
    ratio_bad = [li.product_code for li in rows
                 if li.printed_units and li.quantity and li.printed_units % int(li.quantity) != 0]
    checks = [
        ("product lines", benchmark.lines, len(rows)),
        ("cases", benchmark.cases, cases),
        ("units", benchmark.units, units),
        ("subtotal", benchmark.subtotal, money(extracted.subtotal)),
        ("state/local charges", benchmark.charges, charges),
        ("tax", benchmark.tax, money(extracted.tax_amount) or Decimal("0")),
        ("amount due", benchmark.amount_due, money(extracted.grand_total)),
        ("completeness", "COMPLETE", completeness),
        ("blocking checks", [], report.blocking_check_names() or []),
        ("confidence capped", False, report.confidence.composite < report.confidence.composite_uncapped
         if getattr(report.confidence, "composite_uncapped", None) is not None else False),
        ("lines whose printed units are not a whole number per case", [], ratio_bad),
    ]
    return [{"check": name, "expected": str(want), "actual": str(got), "ok": want == got} for name, want, got in checks]


def load_ocr(args: argparse.Namespace) -> tuple[str, int]:
    if args.ocr_file:
        text = Path(args.ocr_file).read_text(encoding="utf-8")
        return text, args.photos or max(text.count("--- PHOTO "), 1)
    import psycopg2

    dsn = args.dsn or os.environ.get("DATABASE_URL_SYNC")
    if not dsn:
        raise SystemExit("Give --ocr-file, or --invoice-number with --dsn / DATABASE_URL_SYNC (a read-only role).")
    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT d.raw_ocr_text, (SELECT count(*) FROM document_pages p WHERE p.document_id = d.id) "
            "FROM invoices i JOIN documents d ON d.id = i.document_id WHERE i.invoice_number = %s", (args.invoice_number,))
        found = cursor.fetchall()
    finally:
        connection.close()
    if len(found) != 1 or not found[0][0]:
        raise SystemExit(f"Expected exactly one stored document with OCR text for invoice {args.invoice_number}.")
    return found[0][0], max(int(found[0][1]), 1)


def print_estimate(e: Estimate) -> None:
    print(f"Replay estimate — prompt {e.prompt_version}, model {e.model}, ONE call, nothing written")
    print(f"  stored OCR text         {e.ocr_chars:,} chars" + ("  (WOULD BE TRUNCATED to the token budget)" if e.truncated else ""))
    print(f"  input tokens            ~{e.input_tokens_expected:,} (high {e.input_tokens_high:,})")
    print(f"  output tokens           ~{e.output_tokens_expected:,} (range {e.output_tokens_low:,}–{e.output_tokens_high:,})")
    print(f"  model cost              ${e.cost_usd_expected:.4f} expected (range ${e.cost_usd_low:.4f}–${e.cost_usd_high:.4f})")
    print(f"  OCR cost                ${e.ocr_usd_replay:.4f} (stored text reused; re-reading the photos would add ${e.ocr_usd_if_reread:.4f})")
    print(f"  TOTAL                   ${e.total_usd_expected:.4f} expected, ${e.total_usd_high:.4f} pessimistic ceiling")
    print(f"  HARD BOUND              ${e.total_usd_bounded:.4f} (output capped at {e.max_output_tokens:,} tokens; a longer answer fails, it does not cost more)")
    print(f"  baseline (v11, actual)  {BASELINE_INPUT_TOKENS:,} in / {BASELINE_OUTPUT_TOKENS:,} out, {BASELINE_LATENCY_S}s")


async def execute(text: str, prompt_version: str) -> tuple[ExtractedInvoice, dict[str, Any]]:
    # Exactly one billed attempt, and a timeout that the longer answer will not hit. Set before settings load.
    os.environ["OPENAI_MAX_RETRIES"] = "0"
    os.environ["OPENAI_TIMEOUT_SECONDS"] = "120"
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.services.llm.openai_provider import OpenAIProvider
    from app.services.ocr.base import OCRResult
    from app.services.structuring_service import StructuringService

    provider = OpenAIProvider(max_output_tokens=MAX_OUTPUT_TOKENS)
    result = await StructuringService(llm_provider=provider, prompt_version=prompt_version).structure_invoice(
        OCRResult(full_text=text, source_type="ocr"), filename="replay")
    metadata = result.metadata
    return result.invoice, {
        "prompt_version": result.prompt_version, "model": metadata.model, "input_tokens": metadata.input_tokens,
        "output_tokens": metadata.output_tokens, "estimated_cost_usd": metadata.estimated_cost_usd,
        "latency_ms": metadata.latency_ms, "ocr_text_truncated": result.ocr_text_truncated}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ocr-file", help="stored OCR text of the invoice, as a file")
    parser.add_argument("--invoice-number", default="48288200")
    parser.add_argument("--dsn", help="read-only psycopg2 DSN to read the stored OCR text")
    parser.add_argument("--photos", type=int, help="number of photos (default: counted from the text or the database)")
    parser.add_argument("--prompt-version", help="prompt to replay with, named explicitly (e.g. v12)")
    parser.add_argument("--execute", action="store_true", help="make the ONE paid model call (default: estimate only)")
    parser.add_argument("--max-cost-usd", type=float, help="ceiling you approve; must be at or above the HARD BOUND")
    parser.add_argument("--output", help="where to write the result; must not exist; keep it outside the repository")
    args = parser.parse_args()

    text, photos = load_ocr(args)
    version = args.prompt_version or "v12"
    e = estimate(text, version, photo_count=photos)
    print_estimate(e)
    if not args.execute:
        print("\nEstimate only. Nothing was called and nothing was written.")
        return 0
    if not args.prompt_version or not args.output or args.max_cost_usd is None:
        raise SystemExit("--execute needs --prompt-version, --max-cost-usd and --output; nothing was called.")
    if args.max_cost_usd < e.total_usd_bounded:
        raise SystemExit(f"--max-cost-usd {args.max_cost_usd} is below the hard bound ${e.total_usd_bounded} "
                         "of the capped call; nothing was called.")
    out = Path(args.output)
    if out.exists():
        raise SystemExit(f"{out} already exists: one controlled replay only; nothing was called.")
    try:
        out.resolve().relative_to(ROOT)
        raise SystemExit("--output is inside the repository; it holds a real invoice's lines. Nothing was called.")
    except ValueError:
        pass

    extracted, usage = asyncio.run(execute(text, version))
    scored = score(extracted, text)
    payload = {"usage": usage, "estimate": asdict(e), "score": scored, "all_ok": all(s["ok"] for s in scored),
               "extraction": extracted.model_dump(mode="json")}
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nActual: {usage['input_tokens']:,} in / {usage['output_tokens']:,} out, ${usage['estimated_cost_usd']}, "
          f"{usage['latency_ms']} ms")
    for row in scored:
        print(f"  [{'ok' if row['ok'] else 'NO'}] {row['check']}: expected {row['expected']}, got {row['actual']}")
    print(f"Written to {out}")
    return 0 if payload["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
