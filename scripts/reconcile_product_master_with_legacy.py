#!/usr/bin/env python
"""
Legacy <-> Product Master reconciliation — READ-ONLY.

    python scripts/reconcile_product_master_with_legacy.py

Compares `master_commercial_mappings` against `product_case_mappings` and
explains every difference. Neither side is modified: the legacy table
remains the authority EDI reads, and nothing here proposes changing that.

The join key is the PDI item code both sides already use, so a difference
in wording is never treated as a difference in identity — description
mismatch is reported on its own axis and never escalates to a conflict.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT_CSV = ROOT / "analysis" / "master-data" / "product_master_legacy_reconciliation.csv"
OUTPUT_JSON = ROOT / "analysis" / "master-data" / "product_master_legacy_reconciliation.json"

# One row may carry several findings; identity and commercial differences are
# separate axes and are reported separately.
LEGACY_MATCH = "LEGACY_MATCH"
MASTER_ONLY = "MASTER_ONLY"
LEGACY_ONLY = "LEGACY_ONLY"
CONFLICT = "CONFLICT"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
COMMERCIAL_UNIT_MISMATCH = "COMMERCIAL_UNIT_MISMATCH"
COST_MISMATCH = "COST_MISMATCH"
DESCRIPTION_MISMATCH = "DESCRIPTION_MISMATCH"


def load(dsn: str) -> tuple[list, list, dict]:
    import psycopg2

    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute("""
            SELECT m.id, m.pdi_item_code, m.commercial_unit_basis, m.units_accounted_for,
                   m.case_cost, m.cost_basis, m.approval_state,
                   p.canonical_upc, p.canonical_description, m.store_id,
                   s.identity_status
            FROM master_commercial_mappings m
            JOIN master_products p ON p.id = m.product_id
            JOIN stores s ON s.id = m.store_id
        """)
        master = cursor.fetchall()
        cursor.execute("""
            SELECT item_code, units_per_case, description, source, store_id
            FROM product_case_mappings
        """)
        legacy = cursor.fetchall()
        cursor.execute("SELECT DISTINCT item_code FROM product_identity")
        legacy_identities = {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()
    return master, legacy, legacy_identities


def reconcile(master, legacy, legacy_identities) -> tuple[list[dict], dict]:
    legacy_by_code: dict[str, list] = defaultdict(list)
    for item_code, units, description, source, store_id in legacy:
        legacy_by_code[item_code].append(
            {"units_per_case": units, "description": description,
             "source": source, "store_id": str(store_id) if store_id else None}
        )

    rows: list[dict] = []
    seen_codes: set[str] = set()

    for (_mapping_id, code, basis, units, cost, cost_basis, approval_state, upc,
         canonical_description, store_id, store_identity) in master:
        seen_codes.add(code)
        counterparts = legacy_by_code.get(code, [])
        findings: list[str] = []
        notes: list[str] = []

        if not counterparts:
            findings.append(MASTER_ONLY)
            # Does the product exist in the legacy system at all, just
            # without a commercial mapping? That distinction decides whether
            # this is new reference data or an unmapped known product.
            if code in legacy_identities:
                notes.append("known to legacy product_identity but has no case mapping")
            else:
                notes.append("not present in legacy master data at all")
        else:
            legacy_units = {c["units_per_case"] for c in counterparts}
            if units is None:
                findings.append(CONFLICT)
                notes.append(
                    "Product Master withholds a multiplier; legacy holds "
                    + ", ".join(str(u) for u in sorted(legacy_units))
                )
            elif legacy_units == {units}:
                findings.append(LEGACY_MATCH)
            else:
                findings.append(COMMERCIAL_UNIT_MISMATCH)
                notes.append(
                    f"master {units} vs legacy {sorted(legacy_units)}"
                )

            legacy_descriptions = {
                (c["description"] or "").strip().upper() for c in counterparts
                if c["description"]
            }
            if canonical_description and legacy_descriptions and (
                canonical_description.strip().upper() not in legacy_descriptions
            ):
                # Reported on its own axis. Wording never implies a different
                # product — both sides are keyed on the same item code.
                findings.append(DESCRIPTION_MISMATCH)

        if cost_basis == "CONFLICTING_SOURCES":
            findings.append(COST_MISMATCH)
            notes.append("reference sources disagree on case cost")

        if upc and code and not upc.startswith(code):
            findings.append(IDENTITY_MISMATCH)
            notes.append(f"item code {code} is not a prefix of canonical UPC {upc}")

        rows.append({
            "pdi_item_code": code,
            "canonical_upc": upc,
            "store_id": str(store_id),
            "store_identity_status": store_identity,
            "master_commercial_unit_basis": basis,
            "master_units_accounted_for": units,
            "master_case_cost": str(cost) if cost is not None else None,
            "master_cost_basis": cost_basis,
            "master_approval_state": approval_state,
            "legacy_units_per_case": " || ".join(
                str(c["units_per_case"]) for c in counterparts) or None,
            "legacy_sources": " || ".join(c["source"] for c in counterparts) or None,
            "canonical_description": canonical_description,
            "findings": " || ".join(findings),
            "explanation": " ; ".join(notes),
        })

    for code, counterparts in sorted(legacy_by_code.items()):
        if code in seen_codes:
            continue
        rows.append({
            "pdi_item_code": code,
            "canonical_upc": None,
            "store_id": None,
            "store_identity_status": None,
            "master_commercial_unit_basis": None,
            "master_units_accounted_for": None,
            "master_case_cost": None,
            "master_cost_basis": None,
            "master_approval_state": None,
            "legacy_units_per_case": " || ".join(
                str(c["units_per_case"]) for c in counterparts),
            "legacy_sources": " || ".join(c["source"] for c in counterparts),
            "canonical_description": None,
            "findings": LEGACY_ONLY,
            "explanation": (
                "EDI maps this item today; the reference corpus produced no "
                "commercial candidate for it"
            ),
        })

    counts: Counter = Counter()
    for row in rows:
        for finding in row["findings"].split(" || "):
            counts[finding] += 1
    return rows, dict(counts)


def main() -> None:
    from app.core.config import get_settings

    dsn = get_settings().database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    master, legacy, legacy_identities = load(dsn)
    rows, counts = reconcile(master, legacy, legacy_identities)

    master_only = [r for r in rows if MASTER_ONLY in r["findings"]]
    summary = {
        "read_only": True,
        "master_commercial_mappings": len(master),
        "legacy_case_mappings": len(legacy),
        "rows": len(rows),
        "findings": counts,
        "master_only_breakdown": dict(Counter(
            r["explanation"].split(" ; ")[0] for r in master_only
        )),
        "edi_authority": "product_case_mappings — unchanged by this report",
    }

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["findings"], r["pdi_item_code"])))
    OUTPUT_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print("Legacy <-> Product Master reconciliation (read-only)\n")
    print(f"  master commercial mappings ...... {summary['master_commercial_mappings']}")
    print(f"  legacy case mappings ............ {summary['legacy_case_mappings']}")
    for finding, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"     {finding:<26} {count}")
    print("\n  MASTER_ONLY breakdown:")
    for reason, count in summary["master_only_breakdown"].items():
        print(f"     {count:>4}  {reason}")
    print(f"\nWrote {OUTPUT_CSV.relative_to(ROOT)} and {OUTPUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
