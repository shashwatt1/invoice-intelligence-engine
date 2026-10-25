#!/usr/bin/env python
"""
Identifier resolution SHADOW run — scripts/identifier_resolution_shadow.py

    python scripts/identifier_resolution_shadow.py

Phase 3A. Runs the pure resolver (app/services/product_master/resolution.py)
over the 8 Phase 1 gold invoices using only artifacts already cached under
analysis/identifier-shadow/<invoice>/ — ocr.json, v11.json and the frozen
obs-v1.1 observations.json. No OCR call, no model call, no database: the
Product Master and crosswalk lookups are the empty ones, because no Product
Master is reachable in this environment, so no line can reach VERIFIED here.

For each invoice it writes resolution.json beside the inputs, and it writes
analysis/identifier-shadow/resolution_report.json comparing each gold line's
expected status (Phase 1 expected_future_status, read without a Product
Master) with the resolver's, the resolver's chosen GTIN with the gold GTIN,
and the resolver's would-be PDI code with today's normalize_item_code()
output. Everything under analysis/ is gitignored. Nothing is persisted
anywhere else, and nothing here is reachable from the pipeline or the EDI
writer.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.export_service import normalize_item_code  # noqa: E402  (read-only comparison)
from app.services.product_master.identifier_templates import (  # noqa: E402
    load_templates,
    select_template,
)
from app.services.product_master.resolution import ResolverLine, resolve_invoice  # noqa: E402
from scripts.identifier_shadow_report import align, gold_lines  # noqa: E402

SHADOW_DIR = ROOT / "analysis" / "identifier-shadow"
GOLD_DIR = ROOT / "tests" / "fixtures" / "identifier" / "invoices"


def expected_without_product_master(expected_future_status: str) -> str:
    """
    The status a gold line should get when no Product Master is reachable.

    'VERIFIED if the Product Master matches' cannot be VERIFIED without one:
    the resolver's honest answer is VALID_UNMATCHED. Good 'N Cold's 11-digit
    lines are VERIFIED only under a template convention that D19 has not
    decided, so they stay AMBIGUOUS. 'UNRESOLVED / NEW_PRODUCT' is a valid
    GTIN the Product Master lacks: VALID_UNMATCHED.
    """
    text = expected_future_status.upper()
    if text.startswith("VERIFIED ONLY UNDER A TEMPLATE"):
        return "AMBIGUOUS"
    if text.startswith("VERIFIED") or text.startswith("UNRESOLVED / NEW_PRODUCT"):
        return "VALID_UNMATCHED"
    for status in ("NOT_APPLICABLE", "UNRESOLVED", "INVALID", "AMBIGUOUS"):
        if text.startswith(status):
            return status
    return "UNKNOWN"


def gold_gtin(line: dict) -> str | None:
    values = [o["raw_text"] for o in line["identifiers"] if o["role_expected"] == "gtin_candidate"]
    return re.sub(r"\D", "", values[0]) if len(values) == 1 else None


def run_one(name: str, templates: list) -> dict[str, Any]:
    folder = SHADOW_DIR / name
    ocr_text = json.loads((folder / "ocr.json").read_text())["full_text"]
    v11 = json.loads((folder / "v11.json").read_text())["parsed"]
    recorded = json.loads((folder / "observations.json").read_text())
    if recorded["prompt_version"] != "obs-v1.1":
        raise SystemExit(f"{name}: expected the frozen obs-v1.1 observations, found {recorded['prompt_version']}")
    lines = [ResolverLine(line_index=i, description=item.get("description"), line_type=item.get("line_type"),
                          product_code=item.get("product_code"), supplier_item_id=item.get("supplier_item_id"))
             for i, item in enumerate(v11["line_items"], 1)]
    template, how = select_template(templates, (v11.get("vendor") or {}).get("name"), ocr_text)
    result = resolve_invoice(lines, recorded["parsed"], ocr_text, template, template_selection=how)
    (folder / "resolution.json").write_text(json.dumps(result, indent=2, sort_keys=True))

    gold = json.loads((GOLD_DIR / f"{name}.json").read_text())
    g_lines = gold_lines(gold)
    pairs = align(g_lines, v11["line_items"])
    comparison = []
    for gi, g in enumerate(g_lines):
        expected = expected_without_product_master(g.get("expected_future_status", ""))
        if gi not in pairs:
            comparison.append({"gold_line": g.get("description_as_printed") or g.get("product_as_printed"),
                               "v11_line": None, "expected": expected, "resolver": None,
                               "agree": None, "note": "no v11 line represents this printed row"})
            continue
        res = result["lines"][pairs[gi]]
        truth = gold_gtin(g)
        chosen = res["chosen_gtin"]
        comparison.append({
            "gold_line": g.get("description_as_printed") or g.get("product_as_printed"),
            "v11_line": res["line_index"], "expected": expected, "resolver": res["status"],
            "agree": res["status"] == expected, "reasons": res["reasons"],
            "gold_gtin": truth, "chosen_gtin": chosen,
            "chosen_matches_gold": None if chosen is None or truth is None else chosen == truth,
            "would_be_eligible": res["would_be_eligible"],
        })
    # D20 is NO, so a proposal is never applied — but is it right? The row the
    # gold prints the value on, mapped to its v11 line, against the suggestion.
    owner: dict[str, set[int]] = {}
    for gi, g in enumerate(g_lines):
        for o in g["identifiers"]:
            owner.setdefault(re.sub(r"\s", "", o["raw_text"]), set()).add(gi)
    v11_of_gold = {gi: ai + 1 for gi, ai in pairs.items()}
    proposals = []
    for prop in result["association_proposals"]:
        gold_rows = owner.get(re.sub(r"\s", "", prop["raw"]), set())
        printed_on = {v11_of_gold[g] for g in gold_rows if g in v11_of_gold}
        proposals.append({**prop, "gold_v11_line": sorted(printed_on),
                          "proposal_correct": (prop["ocr_order_suggests_line"] in printed_on) if printed_on else None})
    current_codes = [{"v11_line": i, "product_code": item.get("product_code"),
                      "normalize_item_code_today": normalize_item_code(item.get("product_code")),
                      "resolver_pdi_item_code": result["lines"][i - 1]["pdi_item_code"],
                      "resolver_status": result["lines"][i - 1]["status"]}
                     for i, item in enumerate(v11["line_items"], 1)]
    return {"template": result["template"], "template_selection": how, "status_counts": result["status_counts"],
            "gold_comparison": comparison, "current_edi_code_vs_resolver": current_codes,
            "anomalies": result["anomalies"], "association_proposals": proposals,
            "inputs_hash": result["inputs_hash"]}


def main() -> None:
    templates = load_templates()
    report: dict[str, Any] = {"product_master": "unavailable in this environment — empty lookups; VERIFIED is unreachable",
              "invoices": {}}
    for gold_path in sorted(GOLD_DIR.glob("*.json")):
        report["invoices"][gold_path.stem] = run_one(gold_path.stem, templates)
    (SHADOW_DIR / "resolution_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    for name, r in report["invoices"].items():
        agree = sum(1 for c in r["gold_comparison"] if c["agree"])
        judged = sum(1 for c in r["gold_comparison"] if c["agree"] is not None)
        counts = {k: v for k, v in r["status_counts"].items() if v}
        template_id = r["template"]["template_id"] if r["template"] else None
        print(f"{name:28} {template_id!s:26} agree {agree}/{judged}  {counts}")
    print(f"Output: {(SHADOW_DIR / 'resolution_report.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
