#!/usr/bin/env python
"""
Overlap between the Excel candidate corpus and the master data the
application already holds — READ-ONLY.

    python scripts/analyze_product_master.py     # must be run first
    python scripts/compare_master_with_existing.py

The connection is opened with SET SESSION CHARACTERISTICS AS TRANSACTION
READ ONLY, so the database rejects a write even if one were attempted.
Nothing here inserts, updates, deletes or migrates; the existing
product_case_mappings, product_identity and product_identifier rows are
authoritative and are only counted, never touched.

This is kept separate from analyze_product_master.py on purpose: that
script must stay runnable with no database at all.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analyze_product_master import upc_a_check_digit  # noqa: E402

CANDIDATES = ROOT / "analysis" / "master-data" / "product_master_candidates.csv"
OUTPUT = ROOT / "analysis" / "master-data" / "existing_master_overlap.json"


def main() -> None:
    if not CANDIDATES.exists():
        sys.exit(f"Run scripts/analyze_product_master.py first — {CANDIDATES} is missing.")

    try:
        import psycopg2
    except ImportError:
        sys.exit("psycopg2 is required for this read-only comparison.")

    from app.core.config import get_settings

    settings = get_settings()
    dsn = settings.database_url_sync.replace("postgresql+psycopg2://", "postgresql://")

    connection = psycopg2.connect(dsn)
    # Enforced at the server: any write in this session is refused.
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT item_code, units_per_case FROM product_case_mappings")
        mappings = dict(cursor.fetchall())
        cursor.execute("SELECT DISTINCT item_code FROM product_identity")
        identity_codes = {row[0] for row in cursor.fetchall()}
        cursor.execute("SELECT kind, value FROM product_identifier")
        identifiers = cursor.fetchall()
    finally:
        connection.close()

    rows = list(csv.DictReader(CANDIDATES.open()))
    id11 = {r["identity_value"] for r in rows if r["identity_namespace"] == "id11"}
    upc12 = {r["identity_value"] for r in rows if r["identity_namespace"] == "upc12"}
    packs = {
        r["identity_value"]: [v for v in r["units_per_case_values"].split(" || ") if v]
        for r in rows
        if r["identity_namespace"] == "upc12" and r["units_per_case_values"]
    }
    identifier_values = {value for _, value in identifiers}

    # What the 11-to-12 digit bridge would connect, if it were ever approved.
    bridged = {"would_gain_pack_evidence": 0, "agrees_with_governed_units": 0,
               "disagrees_with_governed_units": 0, "disagreements": []}
    for code, governed_units in mappings.items():
        if len(code) != 11:
            continue
        derived = code + upc_a_check_digit(code)
        observed = packs.get(derived)
        if not observed:
            continue
        bridged["would_gain_pack_evidence"] += 1
        if str(governed_units) in observed:
            bridged["agrees_with_governed_units"] += 1
        else:
            bridged["disagrees_with_governed_units"] += 1
            bridged["disagreements"].append(
                {"item_code": code, "derived_upc12": derived,
                 "governed_units_per_case": governed_units, "corpus_units_per_case": observed}
            )

    report = {
        "read_only": True,
        "existing_master_data": {
            "product_case_mappings_distinct_item_codes": len(mappings),
            "product_identity_distinct_item_codes": len(identity_codes),
            "product_identifier_rows": len(identifiers),
            "product_identifier_kinds": dict(Counter(kind for kind, _ in identifiers)),
            "case_mapping_item_code_lengths": dict(Counter(len(c) for c in mappings)),
        },
        "overlap_with_excel_corpus": {
            "case_mappings_found_in_id11_candidates": len(set(mappings) & id11),
            "case_mappings_found_in_upc12_candidates": len(set(mappings) & upc12),
            "product_identity_found_in_id11_candidates": len(identity_codes & id11),
            "product_identifier_values_in_upc12_candidates": len(identifier_values & upc12),
            "product_identifier_values_in_id11_candidates": len(identifier_values & id11),
        },
        "namespace_finding": (
            "The application's canonical item_code lives in the 11-digit space; none of "
            "the governed case mappings match a 12-digit UPC directly. Pack-size evidence "
            "in the corpus is keyed by 12-digit UPC. The two therefore cannot be joined "
            "without resolving the 11-to-12 digit relationship."
        ),
        "if_check_digit_bridge_were_applied": bridged,
        "bridge_status": "NOT APPLIED. Reported as evidence for human review only.",
    }

    OUTPUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nWrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
