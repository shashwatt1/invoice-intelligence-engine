#!/usr/bin/env python
"""
Commercial candidate review aid — READ-ONLY.

    python scripts/review_product_master_candidates.py
    python scripts/review_product_master_candidates.py --conflicts-only --format table
    python scripts/review_product_master_candidates.py --status REVIEW_REQUIRED --limit 50 --format csv

Prints the commercial candidates a reviewer has to work through, grouped by
what the evidence did or did not settle, with the legacy mapping beside
each one.

**It never mutates the database.** It opens a READ ONLY session, approves
nothing, rejects nothing, and resolves nothing. The CSV it writes is named
a review INPUT precisely so it cannot be mistaken for an approval file.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.master_commercial_review_service import (  # noqa: E402
    LEGACY_ABSENT,
    LEGACY_AGREES,
    LEGACY_DISSENTS,
    LEGACY_UNCOMPARABLE,
)

OUTPUT_CSV = ROOT / "analysis" / "master-data" / "product_master_commercial_REVIEW_INPUT.csv"

# The groups a reviewer works in. A candidate appears in exactly one.
GROUP_EVIDENCE_BACKED = "A_EVIDENCE_BACKED"
GROUP_CONFLICT = "B_CONFLICT"
GROUP_MISSING_MULTIPLIER = "C_MISSING_MULTIPLIER"
GROUP_REJECTED = "D_REJECTED"
GROUP_APPROVED = "E_APPROVED"

COLUMNS = [
    "group", "review_status", "canonical_key", "canonical_upc", "pdi_item_code",
    "store_id", "store_identity_status", "commercial_unit_basis", "units_accounted_for",
    "cost_basis", "case_cost", "evidence_source", "source_file", "source_sheet",
    "source_row", "legacy_units_per_case", "legacy_agreement", "approval_state",
    "reviewed_by", "explanation",
]


def group_for(review_status: str) -> str:
    return {
        "APPROVED": GROUP_APPROVED,
        "REJECTED": GROUP_REJECTED,
        "CONFLICT": GROUP_CONFLICT,
        "NO_MULTIPLIER": GROUP_MISSING_MULTIPLIER,
        "READY_FOR_REVIEW": GROUP_EVIDENCE_BACKED,
    }[review_status]


def derive_review_status(approval_state: str, basis: str, units) -> str:
    """Mirrors master_commercial_review_service.review_status()."""
    if approval_state == "APPROVED":
        return "APPROVED"
    if approval_state == "REJECTED":
        return "REJECTED"
    if basis == "CONFLICT":
        return "CONFLICT"
    if units is None:
        return "NO_MULTIPLIER"
    return "READY_FOR_REVIEW"


def legacy_agreement(units, legacy_units: set[int]) -> str:
    if not legacy_units:
        return LEGACY_ABSENT
    if units is None:
        return LEGACY_UNCOMPARABLE
    return LEGACY_AGREES if legacy_units == {units} else LEGACY_DISSENTS


def load(dsn: str) -> list[dict]:
    import psycopg2

    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT item_code, units_per_case FROM product_case_mappings")
        legacy: dict[str, set[int]] = defaultdict(set)
        for item_code, units in cursor.fetchall():
            legacy[item_code].add(units)

        cursor.execute("""
            SELECT p.canonical_key, p.canonical_upc, m.pdi_item_code, m.store_id,
                   s.identity_status, m.commercial_unit_basis, m.units_accounted_for,
                   m.cost_basis, m.case_cost, m.approval_state, m.reviewed_by,
                   m.source_file, m.source_sheet, m.source_row, m.evidence
            FROM master_commercial_mappings m
            JOIN master_products p ON p.id = m.product_id
            JOIN stores s ON s.id = m.store_id
        """)
        rows = []
        for (key, upc, code, store_id, identity_status, basis, units, cost_basis,
             cost, approval_state, reviewed_by, source_file, source_sheet,
             source_row, evidence) in cursor.fetchall():
            status = derive_review_status(approval_state, basis, units)
            legacy_units = legacy.get(code, set())
            statements = (evidence or {}).get("source_statements") or []
            rows.append({
                "group": group_for(status),
                "review_status": status,
                "canonical_key": key,
                "canonical_upc": upc,
                "pdi_item_code": code,
                "store_id": str(store_id),
                "store_identity_status": identity_status,
                "commercial_unit_basis": basis,
                "units_accounted_for": units,
                "cost_basis": cost_basis,
                "case_cost": str(cost) if cost is not None else None,
                "evidence_source": " || ".join(
                    str(s.get("statement")) for s in statements if s.get("statement")
                ),
                "source_file": source_file,
                "source_sheet": source_sheet,
                "source_row": source_row,
                "legacy_units_per_case": " || ".join(
                    str(u) for u in sorted(legacy_units)) or None,
                "legacy_agreement": legacy_agreement(units, legacy_units),
                "approval_state": approval_state,
                "reviewed_by": reviewed_by,
                "explanation": (evidence or {}).get("notes"),
            })
    finally:
        connection.close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review aid for Product Master commercial candidates. Never mutates.",
    )
    parser.add_argument("--status", default=None,
                        help="Filter by approval state, e.g. REVIEW_REQUIRED.")
    parser.add_argument("--conflicts-only", action="store_true")
    parser.add_argument("--store", default=None, help="Filter by store id.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--format", choices=["table", "csv", "json"], default="table")
    args = parser.parse_args()

    from app.core.config import get_settings

    dsn = get_settings().database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    rows = load(dsn)

    if args.status:
        rows = [r for r in rows if r["approval_state"] == args.status]
    if args.conflicts_only:
        rows = [r for r in rows if r["review_status"] == "CONFLICT"]
    if args.store:
        rows = [r for r in rows if r["store_id"] == args.store]

    # Deterministic: conflicts first, then by identity.
    rows.sort(key=lambda r: (r["group"], str(r["canonical_upc"]), str(r["pdi_item_code"])))
    if args.limit:
        rows = rows[: args.limit]

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    if args.format == "json":
        print(json.dumps(rows, indent=2, sort_keys=True, default=str))
    elif args.format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    else:
        groups = Counter(r["group"] for r in rows)
        print("Product Master commercial candidates — REVIEW AID (read-only)\n")
        for group in sorted(groups):
            print(f"  {group:<24} {groups[group]}")
        print()
        print(f"  {'status':<17}{'upc':<14}{'basis':<24}{'x':>3}  "
              f"{'legacy':<12}{'cost basis':<22}")
        for row in rows[:40]:
            print(f"  {row['review_status']:<17}{str(row['canonical_upc']):<14}"
                  f"{row['commercial_unit_basis']:<24}"
                  f"{str(row['units_accounted_for'] or '-'):>3}  "
                  f"{row['legacy_agreement'][:11]:<12}{str(row['cost_basis'])[:21]:<22}")
        if len(rows) > 40:
            print(f"  … {len(rows) - 40} more (see the CSV)")

    print(f"\nWrote {OUTPUT_CSV.relative_to(ROOT)}  (review INPUT — approves nothing)")


if __name__ == "__main__":
    main()
