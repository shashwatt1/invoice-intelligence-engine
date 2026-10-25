#!/usr/bin/env python
"""
normalize_item_code() impact report — scripts/normalize_item_code_impact.py

    python scripts/normalize_item_code_impact.py

READ-ONLY. Characterizes export_service.normalize_item_code() — without
changing it or any caller — so a future migration to resolver-driven item
codes can be decided on evidence:

  1. every call site in app/ and scripts/ (static analysis), with its role:
     EDI bytes, identity gate, units-per-case mapping key, proposal key,
     reference-import key, audit;
  2. every value available offline, classified by what the function does to
     it: the gold invoices' v11 product codes and the Product Master seed
     preview's raw identifier values (with their source profile);
  3. where the resolver's would-be code is byte-identical (UPC-A: the key a
     mapping is stored under does not move) and where a "refuse instead of
     reshape" rule would change behaviour — including where it would BREAK
     a key that is correct today (11-digit PDI item codes from sources that
     omit the check digit by convention).

Writes analysis/identifier-shadow/normalize_item_code_impact.json (gitignored).
"""

from __future__ import annotations

import ast
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.export_service import normalize_item_code  # noqa: E402  (called, never changed)
from app.services.product_master.identifiers import has_valid_gtin_check  # noqa: E402

OUT = ROOT / "analysis" / "identifier-shadow" / "normalize_item_code_impact.json"
PREVIEW = ROOT / "analysis" / "master-data" / "product_master_identifiers.csv"

ROLES = [
    ("app/services/export_service.py", "_pdi_item_code", "EDI_BYTES: the 11-char item code written into B records"),
    ("app/services/export_service.py", "items_with_unresolved_identity", "IDENTITY_GATE: None blocks export"),
    ("app/services/export_service.py", "unmapped_item_codes", "MAPPING_GATE: key looked up in units-per-case"),
    ("app/services/export_service.py", "_pdi_units_per_case", "MAPPING_LOOKUP: units-per-case for the EDI row"),
    ("app/services/export_service.py", None, "EXPORT_OTHER"),
    ("app/services/pdi_audit.py", None, "AUDIT: self-check of an exported file"),
    ("app/services/case_mapping_service.py", None, "MAPPING_KEY: case-mapping status/suggestions"),
    ("app/services/mapping_queue_service.py", None, "MAPPING_KEY: mapping queue grouping"),
    ("app/services/store_reference_service.py", None, "REFERENCE_KEY: store reference lookups"),
    ("app/services/store_reference_import.py", None, "IMPORT_KEY: store reference import (scan codes)"),
    ("app/services/beer_inventory_import.py", None, "IMPORT_KEY: beer inventory import"),
    ("app/api/v1/proposals.py", None, "PROPOSAL_KEY: proposal entity_key / product history"),
    ("app/api/v1/invoices.py", None, "API_KEY: invoice detail / case-mapping confirmation"),
    ("app/models/store_product_reference.py", None, "DOC: comment reference"),
    ("app/services/product_master/shadow_edi.py", None, "MAPPING_LOOKUP: Product Master shadow EDI (read-only comparison)"),
]


def call_sites() -> list[dict[str, Any]]:
    sites = []
    for base in ("app", "scripts"):
        for path in sorted((ROOT / base).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            parents: dict[ast.AST, ast.AST] = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) \
                        == "normalize_item_code":
                    enclosing: str | None = None
                    cursor: ast.AST = node
                    while cursor in parents:
                        cursor = parents[cursor]
                        if isinstance(cursor, ast.FunctionDef | ast.AsyncFunctionDef):
                            enclosing = cursor.name
                            break
                    role = next((r for f, fn, r in ROLES if f == rel and (fn is None or fn == enclosing)),
                                "SCRIPT" if base == "scripts" else "UNCLASSIFIED")
                    sites.append({"file": rel, "line": node.lineno, "function": enclosing, "role": role})
    return sites


def value_class(raw: str) -> str:
    text = raw.strip()
    digits = re.sub(r"\D", "", text)
    if not text:
        return "EMPTY"
    if re.fullmatch(r"\d+(?:\.\d+)?[Ee][+-]?\d+", text):
        return "SCIENTIFIC_NOTATION"
    if "," in text:
        return "MULTI_VALUE"
    if re.search(r"[A-Za-z]", text):
        return "LETTERS"
    if not digits.strip("0"):
        return "PLACEHOLDER_ZEROS"
    n = len(digits)
    if n == 12:
        return "GTIN12_VALID" if has_valid_gtin_check(digits) else "GTIN12_INVALID"
    if n == 13:
        return "GTIN13" if has_valid_gtin_check(digits) else "THIRTEEN_INVALID"
    if n == 14:
        return "GTIN14" if has_valid_gtin_check(digits) else "FOURTEEN_INVALID"
    if n == 11:
        return "ELEVEN_DIGITS"
    return "SHORT" if n < 11 else "LONG"


# What each class does today, and what a resolver-driven "refuse instead of reshape" rule would do.
EFFECT = {
    "GTIN12_VALID": ("drops the check digit (correct UPC-A convention)", "IDENTICAL: same 11-digit code, same key"),
    "GTIN12_INVALID": ("drops the last digit of an invalid barcode (KD7)", "REFUSE: invalid, reread/review"),
    "GTIN13": ("truncates to 11 digits, losing 2 (KD5)", "REFUSE: no PDI representation (D8)"),
    "GTIN14": ("truncates to 11 digits, losing 3 (KD6)", "REFUSE: no PDI representation (D8)"),
    "THIRTEEN_INVALID": ("truncates", "REFUSE"), "FOURTEEN_INVALID": ("truncates", "REFUSE"),
    "ELEVEN_DIGITS": ("passes through unchanged",
                      "PROFILE-DEPENDENT: correct PDI item code for check-digit-omitting sources "
                      "(Item Sales Summary); ambiguous elsewhere (KD4, D19). A blanket refusal BREAKS "
                      "correct keys."),
    "SHORT": ("returns the short digits; the EDI writer left-pads to 11 (KD1/KD2/KD3)", "REFUSE"),
    "LONG": ("truncates to 11", "REFUSE"),
    "LETTERS": ("strips letters, keeps digits (KD1)", "REFUSE: never a barcode"),
    "MULTI_VALUE": ("concatenates all digits, then truncates (KD8)", "REFUSE: split and resolve"),
    "SCIENTIFIC_NOTATION": ("reads the mantissa+exponent digits as one number: a wrong code",
                            "REFUSE here; identifiers.py expands it by source profile"),
    "PLACEHOLDER_ZEROS": ("None", "None (same)"), "EMPTY": ("None", "None (same)"),
}


def classify_dataset(values: list[tuple[str, str]]) -> dict[str, Any]:
    """(raw, label) pairs -> class counts, today's output samples, and identity check for UPC-A."""
    classes: Counter = Counter()
    by_label: dict[str, Counter] = defaultdict(Counter)
    samples: dict[str, list] = defaultdict(list)
    upc_identical = upc_total = 0
    for raw, label in values:
        cls = value_class(raw)
        classes[cls] += 1
        by_label[label][cls] += 1
        today = normalize_item_code(raw)
        if len(samples[cls]) < 4:
            samples[cls].append({"raw": raw, "normalize_item_code": today})
        if cls == "GTIN12_VALID":
            upc_total += 1
            upc_identical += today == re.sub(r"\D", "", raw)[:11]
    return {"values": len(values), "classes": dict(classes), "by_source": {k: dict(v) for k, v in by_label.items()},
            "samples": dict(samples), "upc_a_values": upc_total, "upc_a_identical_to_resolver_code": upc_identical}


def gold_values() -> list[tuple[str, str]]:
    out = []
    for path in sorted((ROOT / "analysis" / "identifier-shadow").glob("*/v11.json")):
        for item in json.loads(path.read_text())["parsed"]["line_items"]:
            if item.get("product_code"):
                out.append((item["product_code"], path.parent.name))
    return out


def preview_values() -> list[tuple[str, str]]:
    if not PREVIEW.exists():
        return []
    with PREVIEW.open(newline="") as handle:
        return [(row["raw_value"], f"{row['source_system']}|{row['identifier_type']}") for row in csv.DictReader(handle)]


def main() -> None:
    sites = call_sites()
    report: dict[str, Any] = {
        "function_changed": False,
        "call_sites": sites,
        "call_sites_by_role": dict(Counter(s["role"] for s in sites)),
        "effects": {k: {"today": v[0], "resolver_rule": v[1]} for k, v in EFFECT.items()},
        "datasets": {
            "gold_v11_product_codes": classify_dataset(gold_values()),
            "product_master_seed_preview_raw_values": classify_dataset(preview_values()),
        },
        "pending": ["product_case_mappings keys and store_product_reference keys need the database "
                    "(unreachable): the count of stored keys a refusal would orphan is not measured"],
    }
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({k: report[k] for k in ("call_sites_by_role",)}, indent=2))
    for name, data in report["datasets"].items():
        print(name, data["values"], data["classes"], "UPC-A identical:",
              f"{data['upc_a_identical_to_resolver_code']}/{data['upc_a_values']}")


if __name__ == "__main__":
    main()
