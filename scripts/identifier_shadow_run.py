#!/usr/bin/env python
"""
Identifier observation SHADOW run — scripts/identifier_shadow_run.py

    python scripts/identifier_shadow_run.py --observe                 # the 8 gold invoices
    python scripts/identifier_shadow_run.py --observe --only mn_9452  # one of them
    python scripts/identifier_shadow_run.py --baseline                # OCR + v11, if not cached

Phase 2 offline experiment, for the 8 Phase 1 gold invoices only.

--baseline  (already done for all 8; cached outputs are never repeated
            unless --force) checks each local original photo against the
            SHA-256 in tests/fixtures/identifier/image_evidence.json, OCRs it
            with the production OCR provider and runs the production v11
            structuring path on that text: ocr.json, v11.json.

--observe   makes exactly ONE observation-only call per invoice (prompt
            obs-v1, schema IdentifierObservationResult) on the CACHED OCR
            text, with the cached v11 line descriptions as numbered,
            input-only row anchors: observations.json. It makes no OCR call,
            never re-runs v11 and never rewrites v11.json (its SHA-256 is
            recorded before and after). It refuses when either cache is
            missing. An existing observations.json is not repeated unless
            --force, and is then kept as observations.run<N>.json.

Everything is written under analysis/identifier-shadow/ (gitignored). It
reads no database and writes none. It does not import persistence,
validation, normalization, mapping, export or Product Master code, and
nothing it produces is read by the production pipeline. There is no mode
that reads the stored corpus. (v12.json / v12.run1.json from the earlier
combined experiment are left in place as historical evidence only.)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.prompts.identifier_observation import OBSERVATION_PROMPT  # noqa: E402
from app.schemas.identifier_observation import IdentifierObservationResult  # noqa: E402
from app.services.llm.factory import get_llm_provider  # noqa: E402
from app.services.ocr.base import OCRResult  # noqa: E402
from app.services.ocr.factory import get_ocr_provider  # noqa: E402
from app.services.structuring_service import StructuringService  # noqa: E402

OUT = ROOT / "analysis" / "identifier-shadow"
EVIDENCE = ROOT / "tests" / "fixtures" / "identifier" / "image_evidence.json"
PHOTOS = ROOT / "Store-Wise Invoices" / "RCM"

# The 8 Phase 1 gold invoices -> their local original photo (gitignored).
GOLD: dict[str, str] = {
    "frito_lay_rcm": "FRITO LAY .jpeg",
    "red_bull_2035546957": "WhatsApp Image 2026-09-17 at 23.42.09.jpeg",
    "coca_cola_1000007174": "WhatsApp Image 2026-09-17 at 23.43.23.jpeg",
    "carlson_80120531": "WhatsApp Image 2026-09-18 at 20.53.55.jpeg",
    "mn_9452": "WhatsApp Image 2026-09-18 at 20.54.10.jpeg",
    "coca_cola_1000006716_bib": "WhatsApp Image 2026-09-18 at 20.55.26.jpeg",
    "good_n_cold_RT0119-6596": "WhatsApp Image 2026-09-18 at 20.55.47.jpeg",
    "unifirst_2310090549": "WhatsApp Image 2026-09-18 at 20.57.39.jpeg",
}

CURRENT_PROMPT = "v11"
OBSERVATIONS = "observations"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expected_hashes() -> dict[str, str]:
    photos = json.loads(EVIDENCE.read_text())["photos"]
    return {p["invoice"]: p["sha256"] for p in photos}


def verified_photo(name: str) -> bytes:
    path = PHOTOS / GOLD[name]
    data = path.read_bytes()
    if sha256(data) != expected_hashes()[name]:
        raise SystemExit(f"REFUSING: {path.name} does not match the Phase 1 hash for {name}.")
    return data


async def ocr(name: str, data: bytes, *, force: bool, cache_only: bool = False) -> OCRResult:
    cache = OUT / name / "ocr.json"
    if cache.exists() and not force:
        cached = json.loads(cache.read_text())
        return OCRResult(full_text=cached["full_text"], source_type=cached["source_type"],
                         mean_confidence=cached["mean_confidence"])
    if cache_only:
        raise SystemExit(f"REFUSING: no cached OCR for {name}; --observe makes no OCR calls.")
    result = await get_ocr_provider().extract_text(data, "image/jpeg", GOLD[name])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "invoice": name, "photo": GOLD[name], "photo_sha256": sha256(data),
        "source_type": result.source_type, "mean_confidence": result.mean_confidence,
        "full_text": result.full_text,
        "full_text_sha256": sha256(result.full_text.encode()),
    }, indent=2))
    return result


def _metadata(meta) -> dict:
    return {"model": meta.model, "input_tokens": meta.input_tokens,
            "output_tokens": meta.output_tokens, "total_tokens": meta.total_tokens,
            "latency_ms": meta.latency_ms, "finish_reason": meta.finish_reason}


async def run_current(name: str, text: OCRResult, *, force: bool) -> None:
    """The production v11 structuring path, on the shadow's own OCR text."""
    target = OUT / name / f"{CURRENT_PROMPT}.json"
    if target.exists() and not force:
        return
    result = await StructuringService(prompt_version=CURRENT_PROMPT).structure_invoice(text, GOLD[name])
    target.write_text(json.dumps({
        "prompt_version": result.prompt_version, **_metadata(result.metadata),
        "parsed": result.invoice.model_dump(mode="json"), "raw_response": result.raw_response,
    }, indent=2, default=str))


async def baseline(names: list[str], *, force: bool) -> None:
    for name in names:
        data = verified_photo(name)
        text = await ocr(name, data, force=False)
        await run_current(name, text, force=force)
        print(f"  {name}: baseline done")


def line_anchors(v11: dict) -> list[str | None]:
    """The ONLY v11 values the observation call sees: line descriptions, in order."""
    return [line.get("description") for line in v11["parsed"]["line_items"]]


def archive(target: Path) -> None:
    """Keep an existing response as <stem>.run<N>.json; an archive is never overwritten."""
    if not target.exists():
        return
    n = 1
    while (archived := target.with_name(f"{target.stem}.run{n}.json")).exists():
        n += 1
    target.rename(archived)


def schema_sha() -> str:
    return sha256(json.dumps(IdentifierObservationResult.model_json_schema(), sort_keys=True).encode())


async def observe_one(name: str, *, force: bool) -> bool:
    """One observation-only call on cached OCR + cached v11 anchors. True if a call was made."""
    target = OUT / name / f"{OBSERVATIONS}.json"
    if target.exists() and not force:
        return False
    data = verified_photo(name)
    v11_path = OUT / name / f"{CURRENT_PROMPT}.json"
    if not v11_path.exists():
        raise SystemExit(f"REFUSING: no recorded {CURRENT_PROMPT} baseline for {name}.")
    text = await ocr(name, data, force=False, cache_only=True)
    v11_before = sha256(v11_path.read_bytes())
    anchors = line_anchors(json.loads(v11_path.read_text()))

    fitted, _truncated = StructuringService._fit_to_budget(text.full_text)
    user_prompt = OBSERVATION_PROMPT.render_user_prompt(fitted, text.source_type, anchors)
    response = await get_llm_provider().generate_structured(
        system_prompt=OBSERVATION_PROMPT.system_prompt,
        user_prompt=user_prompt,
        schema=IdentifierObservationResult,
    )

    v11_after = sha256(v11_path.read_bytes())
    if v11_after != v11_before:
        raise SystemExit(f"ABORT: {v11_path} changed during the observation call.")
    archive(target)
    target.write_text(json.dumps({
        "prompt_version": OBSERVATION_PROMPT.version,
        "system_prompt_sha256": sha256(OBSERVATION_PROMPT.system_prompt.encode()),
        "user_prompt_sha256": sha256(user_prompt.encode()),
        "schema_sha256": schema_sha(),
        "v11_line_anchors": len(anchors),
        "v11_sha256_before": v11_before, "v11_sha256_after": v11_after,
        **_metadata(response.metadata),
        "parsed": response.parsed.model_dump(mode="json"), "raw_response": response.raw_response,
    }, indent=2, default=str))
    return True


async def observe(names: list[str], *, force: bool) -> None:
    for name in names:
        called = await observe_one(name, force=force)
        print(f"  {name}: {'observed' if called else 'cached (no call)'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline identifier-observation shadow run (8 gold invoices).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--observe", action="store_true",
                      help="One observation-only call per invoice on cached OCR + cached v11 anchors.")
    mode.add_argument("--baseline", action="store_true",
                      help="OCR + production v11 structuring, skipped where already cached.")
    parser.add_argument("--only", action="append", choices=sorted(GOLD),
                        help="Run only this gold invoice (repeatable).")
    parser.add_argument("--force", action="store_true",
                        help="Repeat the model call even if its output exists (OCR stays cached).")
    args = parser.parse_args()
    names = args.only or list(GOLD)
    if args.observe:
        print(f"Observation run ({OBSERVATION_PROMPT.version}): {len(names)} gold invoice(s)")
        asyncio.run(observe(names, force=args.force))
    else:
        print(f"Baseline run ({CURRENT_PROMPT}): {len(names)} gold invoice(s)")
        asyncio.run(baseline(names, force=args.force))
    print(f"Output: {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
