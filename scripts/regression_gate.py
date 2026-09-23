"""
Regression / promotion gate — scripts/regression_gate.py

Answers one question before a prompt, rule or extraction change is
promoted: did anything move that should not have?

    python scripts/regression_gate.py                 # evaluate + classify
    python scripts/regression_gate.py --baseline      # record today's state
    python scripts/regression_gate.py --json out.json # machine-readable

The corpus is split deliberately, because "nothing may ever change" is
the wrong bar for a system still learning to read new layouts:

  GOLDEN — invoices whose behaviour is settled and whose EDI artifact is
    authoritative. Their SHA-256 is a CONTRACT, written here in the
    source, not read from a regenerable baseline file, so a candidate
    cannot quietly re-bless itself. Any movement is a REGRESSION.

  DIFFICULT — invoices with a known, documented problem. Their expected
    state is recorded as the behaviour we currently believe correct.
    Movement towards it is an IMPROVEMENT; movement away is a
    REGRESSION; a case still short of it is UNRESOLVED and reported as
    such rather than failing the gate.

Everything is read-only: invoices are loaded, EDI is built in memory and
hashed. Nothing is written to the database and no EDI file is exported.
Promotion is never automatic — the gate reports, the engineer decides.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.database.session import get_session_factory  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.models.processing_log import PipelineStage, ProcessingLog  # noqa: E402
from app.services.case_mapping_service import (  # noqa: E402
    build_case_mapping_status,
    invoice_units_by_item_code,
)
from app.services.export_service import (  # noqa: E402
    build_pdi_export,
    pdi_export_eligibility,
    unmapped_item_codes,
)

BASELINE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "regression_baseline.json"

# --------------------------------------------------------------------------
# The corpus. Golden hashes are contracts — never regenerate them to match
# a candidate; a change here must be a deliberate, explained edit.
# --------------------------------------------------------------------------

GOLDEN: dict[str, dict] = {
    "3376587": {
        "invoice_id": "6b820daa-1706-495c-a956-dd4dbced5590",
        "vendor": "Balkan Beverage LLC", "invoice_date": "2025-10-10",
        "edi_sha256": "88516117ec2221df9917f0a4beb176e494f7faa680ef453ac9ebf1aa09cf2165",
        "assume_suggested": True,          # mappings unconfirmed: dry run
        "records": 8, "b_records": 7, "status": "VALIDATED",
    },
    "1000540": {
        "invoice_id": "b125d517-2f8a-4009-8765-aef83b8968b3",
        "vendor": "A.L. George / Onondaga Beverage", "invoice_date": "2026-08-27",
        "edi_sha256": "72b6bfe5a9544a48966fa8951c85a366d24e44946c47431813c554163386fe15",
        "records": 5, "b_records": 4, "status": "VALIDATED",
    },
    "101497": {
        "invoice_id": "9a72e038-d190-46f0-b4d7-24873aeb0240",
        "vendor": "T.J. Sheehan", "invoice_date": "2026-09-10",
        "edi_sha256": "997be22975fcdffe57fd48417e736e17f5bd4f7cef17342b69a906f88eb51834",
        "records": 27, "b_records": 26, "status": "VALIDATED",
    },
    "1012818": {
        "invoice_id": "7828f3a7-c30a-43f4-bd0c-aa509771d638",
        "vendor": "RCM / Onondaga Beverage", "invoice_date": "2026-09-17",
        "edi_sha256": "30cbc2e98d0b47c2e1a8b75398cb198de7e91f8c456f612794ec5f19ea20378c",
        "records": 32, "b_records": 31, "status": "VALIDATED",
    },
}

DIFFICULT: dict[str, dict] = {
    "2035546957": {
        "invoice_id": "23e534c1-8a19-4a44-b5f3-6f2f0e6c2a0f",
        "vendor": "Red Bull Distribution", "note": (
            "Printed INVOICE 329.53 against TOTAL DUE 0.00. Governed correction set "
            "grand_total; the production record is corrected and must not be altered casually."
        ),
        "expect": {"status": "VALIDATED", "grand_total": 329.53, "corrected_fields": ["grand_total"]},
        "edi_sha256": "764204bc60819e1076ccfc9018af3879232d78f3d11df251b5e48b05a4c59448",
    },
    "2310090549": {
        "invoice_id": "c568d364-fb65-402e-9424-861b056511bd",
        "vendor": "UniFirst", "note": (
            "RATE/AMOUNT/TAX/TOTAL columns and charges inside the printed subtotal. "
            "Originally extracted under v7 as REVIEW_REQUIRED — recorded below as "
            "historical_baseline, kept for history and never deleted. A governed "
            "reprocess (same document_id/invoice_id, no human corrections) moved it "
            "to v10 / VALIDATED; that accepted result is approved_target, the gate's "
            "actual current pass/fail criterion for this case. EDI readiness is "
            "separately blocked by unresolved case mappings, not evaluated here — "
            "see export_service.pdi_export_eligibility."
        ),
        # Informational only — documents what the state was BEFORE the
        # governed reprocess. Never used to compute the verdict; a case
        # still sitting here (not yet reprocessed) is UNRESOLVED, not a
        # regression, since nothing has moved.
        "historical_baseline": {"status": "REVIEW_REQUIRED", "grand_total": 202.30,
                                 "subtotal": 191.04, "tax_amount": 11.26, "prompt_version": "v7"},
        # The approved current state. Once present, this — not
        # historical_baseline — is the sole gate for this case: met is
        # IMPROVEMENT, a genuine deviation from it is REGRESSION.
        "approved_target": {"status": "VALIDATED", "prompt_version": "v10",
                             "grand_total": 202.30, "subtotal": 191.04, "tax_amount": 11.26,
                             "line_item_math_failures": 0, "products_plus_charges": 191.04},
    },
    "000007174": {
        "invoice_id": "cbbd1e0f-4c38-45f6-8683-6ca7c5392873",
        "vendor": "Coca-Cola of Southern Utah", "note": (
            "The captured photograph is cropped above the totals block. No grand total is "
            "printed anywhere in the source, so none may be invented — the correct "
            "behaviour is to stay unresolved until the rest of the document is supplied."
        ),
        "expect": {"status": "REVIEW_REQUIRED", "grand_total": None, "subtotal": None},
    },
}


@dataclass
class Finding:
    invoice: str
    klass: str                 # GOLDEN | DIFFICULT
    verdict: str               # OK | REGRESSION | IMPROVEMENT | UNRESOLVED | MISSING
    detail: list[str] = field(default_factory=list)
    observed: dict = field(default_factory=dict)


async def _prompt_version(session, document_id) -> str | None:
    """
    The prompt that produced the CURRENT result, from the processing log.

    It is not on the invoice: the pipeline records it in the AI_STRUCTURING
    entry, so the latest such entry describes the extraction now in place.
    """
    logs = (await session.execute(
        select(ProcessingLog)
        .where(ProcessingLog.document_id == document_id)
        .where(ProcessingLog.stage == PipelineStage.AI_STRUCTURING)
        .order_by(ProcessingLog.created_at, ProcessingLog.id)
    )).scalars().all()
    versions = [(log.payload or {}).get("prompt_version") for log in logs]
    return next((v for v in reversed(versions) if v), None)


async def _line_item_math_failures(session, document_id) -> int | None:
    """
    Count of FAILED LINE_ITEM_MATH checks from the CURRENT validation
    result, from the processing log — same pattern as _prompt_version:
    the latest VALIDATION entry (written by both the initial pipeline run
    and any later governed revalidation) describes the decision now in
    place. None if this document has no recorded validation entry.
    """
    logs = (await session.execute(
        select(ProcessingLog)
        .where(ProcessingLog.document_id == document_id)
        .where(ProcessingLog.stage == PipelineStage.VALIDATION)
        .order_by(ProcessingLog.created_at, ProcessingLog.id)
    )).scalars().all()
    if not logs:
        return None
    checks = (logs[-1].payload or {}).get("checks", [])
    return sum(1 for c in checks if c.get("name") == "LINE_ITEM_MATH" and c.get("status") == "FAILED")


async def _observe(session, invoice_number: str, spec: dict) -> dict | None:
    """Read-only snapshot of one invoice, including its EDI if exportable."""
    assume_suggested = spec.get("assume_suggested", False)
    query = (
        select(Invoice)
        .options(selectinload(Invoice.items), selectinload(Invoice.vendor))
        .order_by(Invoice.created_at)
    )
    # Pinned by id: several rows can share an invoice number (an early
    # extraction attempt and the authoritative one), and a contract must
    # not depend on which sorts first.
    pinned = spec.get("invoice_id")
    query = query.where(Invoice.id == uuid.UUID(pinned)) if pinned else \
        query.where(Invoice.invoice_number == invoice_number)
    invoice = (await session.execute(query)).scalars().first()
    if invoice is None:
        return None

    units = await invoice_units_by_item_code(session, invoice)
    missing = unmapped_item_codes(invoice, units)
    eligibility = pdi_export_eligibility(invoice, units)

    effective, dry_run = dict(units), False
    if assume_suggested and missing:
        for status in build_case_mapping_status(invoice, units):
            if status.item_code in missing and status.suggested_units_per_case:
                effective[status.item_code] = status.suggested_units_per_case
        dry_run = not unmapped_item_codes(invoice, effective)

    edi = build_pdi_export(invoice, effective) if (not missing or dry_run) else None
    raw = edi.encode() if edi else None
    records = [line for line in (raw.split(b"\r\n") if raw else []) if line]

    num = lambda v: float(v) if v is not None else None  # noqa: E731
    products = sum((i.line_total or 0) for i in invoice.items if i.line_type == "product")
    charges = sum((i.line_total or 0) for i in invoice.items if i.line_type == "charge")
    return {
        "invoice_number": invoice.invoice_number,
        "invoice_id": str(invoice.id),
        "vendor": invoice.vendor.name if invoice.vendor else None,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "status": invoice.status,
        "prompt_version": await _prompt_version(session, invoice.document_id),
        "line_item_math_failures": await _line_item_math_failures(session, invoice.document_id),
        "model": invoice.extraction_model,
        "subtotal": num(invoice.subtotal), "tax_amount": num(invoice.tax_amount),
        "grand_total": num(invoice.grand_total),
        "products_sum": float(products), "charges_sum": float(charges),
        "products_plus_charges": float(products + charges),
        "line_item_count": len(invoice.items),
        "corrected_fields": list(invoice.corrected_fields or []),
        "correction_entries": len(invoice.correction_history or []),
        "unmapped_products": len(missing),
        "export_allowed": eligibility.allowed,
        "export_blocked_reason": eligibility.blocked_reason,
        "edi_dry_run": dry_run,
        "edi_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
        "edi_bytes": len(raw) if raw else None,
        "edi_records": len(records) or None,
        "edi_b_records": sum(1 for r in records if r.startswith(b"B")) or None,
        "edi_crlf": (raw.count(b"\r\n") == len(records)) if raw else None,
        "edi_trailing_newline": raw.endswith(b"\r\n") if raw else None,
    }


def _check_golden(number: str, spec: dict, seen: dict, before: dict | None) -> Finding:
    detail: list[str] = []
    if seen["edi_sha256"] != spec["edi_sha256"]:
        detail.append(f"EDI SHA-256 changed: expected {spec['edi_sha256'][:16]}… got "
                      f"{(seen['edi_sha256'] or 'none')[:16]}…")
    for key in ("records", "b_records"):
        expected, actual = spec[key], seen[f"edi_{key}"]
        if expected != actual:
            detail.append(f"{key}: expected {expected}, got {actual}")
    if seen["status"] != spec["status"]:
        detail.append(f"status: expected {spec['status']}, got {seen['status']}")
    if seen["edi_sha256"] and not (seen["edi_crlf"] and seen["edi_trailing_newline"]):
        detail.append("EDI line endings are not CRLF-terminated")
    if before:
        for key in ("grand_total", "subtotal", "line_item_count"):
            if before.get(key) != seen.get(key):
                detail.append(f"{key} moved since baseline: {before.get(key)} -> {seen.get(key)}")
    return Finding(number, "GOLDEN", "REGRESSION" if detail else "OK", detail, seen)


def _check_difficult(number: str, spec: dict, seen: dict, before: dict | None) -> Finding:
    """
    Three distinct things, never conflated:

      historical_baseline — informational only. What the state was BEFORE
        a governed fix. Documents history; never gates the verdict.
      approved_target      — the CURRENT enforced expectation, once a
        case has an explicitly approved target. Met is IMPROVEMENT; a
        genuine deviation from it is REGRESSION. Still sitting exactly at
        historical_baseline (never reprocessed) is UNRESOLVED, not a
        regression, since nothing has moved yet.
      expect                — for a DIFFICULT case with no approved_target
        (i.e. still just a documented known problem, not a governed fix
        under evaluation), this remains the enforced current-state check,
        unchanged from before.
      seen                  — the actual observed state, read fresh, read-only.
    """
    detail, verdict = [], "OK"
    approved_target = spec.get("approved_target")
    historical_baseline = spec.get("historical_baseline")

    if approved_target:
        met = all(seen.get(k) == v for k, v in approved_target.items())
        if met:
            verdict = "IMPROVEMENT"
            detail.append("approved target reached")
            if historical_baseline:
                moved = [k for k in historical_baseline if historical_baseline.get(k) != seen.get(k)]
                if moved:
                    detail.append("moved from historical baseline: " + ", ".join(moved))
        else:
            still_at_baseline = bool(historical_baseline) and all(
                seen.get(k) == v for k, v in historical_baseline.items() if k in seen
            )
            if still_at_baseline:
                verdict = "UNRESOLVED"
                detail.append("still at historical baseline, short of approved target: "
                              + ", ".join(f"{k}={approved_target[k]!r}" for k in approved_target))
            else:
                verdict = "REGRESSION"
                mismatches = [(k, v, seen.get(k)) for k, v in approved_target.items() if seen.get(k) != v]
                detail += [f"{k}: expected approved target {v!r}, got {actual!r}" for k, v, actual in mismatches]
    else:
        for key, expected in spec.get("expect", {}).items():
            actual = seen.get(key)
            if actual != expected:
                detail.append(f"{key}: expected {expected!r}, got {actual!r}")
        if detail:
            verdict = "REGRESSION"

    if "edi_sha256" in spec and seen["edi_sha256"] and seen["edi_sha256"] != spec["edi_sha256"]:
        detail.append(f"EDI SHA-256 changed: expected {spec['edi_sha256'][:16]}…")
        verdict = "REGRESSION"

    if before and verdict == "OK":
        moved = [k for k in ("status", "grand_total", "line_item_count")
                 if before.get(k) != seen.get(k)]
        if moved:
            verdict = "EXPECTED CHANGE"
            detail.append("changed since baseline: " + ", ".join(moved))
    return Finding(number, "DIFFICULT", verdict, detail, seen)


async def run(write_baseline: bool, json_path: str | None) -> int:
    baseline = {}
    if BASELINE_PATH.exists() and not write_baseline:
        baseline = json.loads(BASELINE_PATH.read_text()).get("invoices", {})

    findings: list[Finding] = []
    observed: dict[str, dict] = {}
    async with get_session_factory()() as session:
        for number, spec in GOLDEN.items():
            seen = await _observe(session, number, spec)
            if seen is None:
                findings.append(Finding(number, "GOLDEN", "MISSING", ["not present in this database"]))
                continue
            observed[number] = seen
            findings.append(_check_golden(number, spec, seen, baseline.get(number)))
        for number, spec in DIFFICULT.items():
            seen = await _observe(session, number, spec)
            if seen is None:
                findings.append(Finding(number, "DIFFICULT", "MISSING", ["not present in this database"]))
                continue
            observed[number] = seen
            findings.append(_check_difficult(number, spec, seen, baseline.get(number)))

    if write_baseline:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps({"invoices": observed}, indent=2, sort_keys=True) + "\n")
        print(f"Baseline written: {BASELINE_PATH.relative_to(Path.cwd())} ({len(observed)} invoices)")

    width = max(len(f.invoice) for f in findings)
    print(f"\n{'invoice':<{width}}  {'class':<9}  verdict")
    print("-" * (width + 32))
    for f in findings:
        print(f"{f.invoice:<{width}}  {f.klass:<9}  {f.verdict}")
        for line in f.detail:
            print(f"{'':<{width}}             · {line}")

    regressions = [f for f in findings if f.verdict in ("REGRESSION", "MISSING")]
    unresolved = [f for f in findings if f.verdict == "UNRESOLVED"]
    improvements = [f for f in findings if f.verdict == "IMPROVEMENT"]
    print(f"\n{len(findings)} evaluated · {len(regressions)} regression(s) · "
          f"{len(improvements)} improvement(s) · {len(unresolved)} unresolved")
    if json_path:
        Path(json_path).write_text(json.dumps([asdict(f) for f in findings], indent=2) + "\n")
        print(f"Findings written to {json_path}")

    if regressions:
        print("\nDO NOT PROMOTE — a protected case moved. Investigate each regression above.")
        return 1
    print("\nNo regressions. Promotion is a human decision: review improvements and "
          "unresolved cases before releasing.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", action="store_true", help="record the current state as the baseline")
    parser.add_argument("--json", dest="json_path", default=None, help="write findings as JSON")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.baseline, args.json_path)))
