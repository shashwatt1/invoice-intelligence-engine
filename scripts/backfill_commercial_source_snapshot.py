#!/usr/bin/env python
"""
Source-evidence snapshot backfill — local development only.

    python scripts/backfill_commercial_source_snapshot.py --dry-run
    python scripts/backfill_commercial_source_snapshot.py

Captures the raw source values behind each commercial candidate into
`master_commercial_mappings.source_snapshot`, so the deployed dashboard can
show a reviewer the actual evidence without reading the reference
workbooks — which are gitignored source data and are not present in the
Render container.

Only values the source actually carried are written. A field the sheet did
not have is left absent; nothing is inferred to fill a gap, and no derived
number is recorded as though it were printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.models.product_master import MasterCommercialMapping, MasterProduct  # noqa: E402
from app.services.product_master.candidates import build_candidates  # noqa: E402
from scripts.analyze_product_master import load_raw_records  # noqa: E402
from scripts.pilot_seed_guard import seed_target_host  # noqa: E402
from scripts.preview_product_master_seed import to_master_rows  # noqa: E402

REPORT = ROOT / "analysis" / "master-data" / "commercial_source_snapshot_report.json"


def snapshot_for(statements: list[dict], rows_by_reference: dict) -> dict:
    """
    The raw fields behind one candidate. Absent keys mean the source had
    no such column — never a zero or a guess.
    """
    snapshot: dict = {"source_rows": []}
    for statement in statements:
        reference = (statement.get("source_file"), statement.get("source_sheet"),
                     statement.get("source_row"))
        raw = rows_by_reference.get(reference)
        entry: dict = {
            "source_file": statement.get("source_file"),
            "source_sheet": statement.get("source_sheet"),
            "source_row": statement.get("source_row"),
            "raw_items_case": (statement.get("evidence") or {}).get("raw_value")
            if isinstance(statement.get("evidence"), dict) else None,
            "statement": statement.get("statement"),
        }
        if raw is not None:
            for key, value in (
                ("raw_identifier", raw.raw_identifier),
                ("raw_unit_identifier", raw.raw_unit_identifier),
                ("raw_description", raw.description),
                ("raw_package", raw.package_notation),
                ("raw_items_case", raw.commercial_units_statement),
                ("raw_case_cost", raw.case_cost),
                ("raw_unit_cost", raw.unit_cost),
                ("raw_divisor_evidence", raw.commercial_statement_evidence),
                ("source_store_identifier", raw.store_context),
            ):
                if value not in (None, ""):
                    entry[key] = str(value)
        snapshot["source_rows"].append({k: v for k, v in entry.items() if v is not None})
    return snapshot


async def run(args) -> dict:
    from app.core.config import get_settings
    from app.database.session import get_session_factory

    host = seed_target_host(get_settings().database_url, args)
    print(f"Database host: {host}  — local development\n")

    graph = build_candidates(to_master_rows(load_raw_records(), None))
    rows_by_reference = {}
    for row in to_master_rows(load_raw_records(), None):
        rows_by_reference[(row.source_file, row.source_sheet, row.source_row)] = row

    by_key: dict[str, list[dict]] = {}
    for candidate in graph.commercial_candidates:
        by_key.setdefault(candidate["canonical_key"], []).append(candidate)

    report = {"dry_run": bool(args.dry_run), "examined": 0, "written": 0,
              "already_present": 0, "no_source_found": 0, "fields": {}}
    fields: Counter = Counter()

    async with get_session_factory()() as session:
        try:
            rows = (await session.execute(
                select(MasterCommercialMapping, MasterProduct)
                .join(MasterProduct, MasterProduct.id == MasterCommercialMapping.product_id)
            )).all()
            for mapping, product in rows:
                report["examined"] += 1
                if mapping.source_snapshot:
                    report["already_present"] += 1
                    continue
                candidates = by_key.get(product.canonical_key, [])
                if not candidates:
                    report["no_source_found"] += 1
                    continue
                statements = [
                    {**{k: c.get(k) for k in
                        ("source_file", "source_sheet", "source_row", "evidence")},
                     "statement": (c.get("evidence") or {}).get("statement")}
                    for c in candidates
                ]
                snapshot = snapshot_for(statements, rows_by_reference)
                mapping.source_snapshot = snapshot
                report["written"] += 1
                for entry in snapshot["source_rows"]:
                    for key in entry:
                        fields[key] += 1

            report["fields"] = dict(fields)
            await session.flush()
            if args.dry_run:
                await session.rollback()
                print("DRY RUN — rolled back.\n")
            else:
                await session.commit()
                print("Committed.\n")
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            report["error"] = f"{type(exc).__name__}: {exc}"
            print(f"FAILED — rolled back: {exc}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill commercial source snapshots.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(run(args))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    for key in ("examined", "written", "already_present", "no_source_found"):
        print(f"  {key:<22} {report[key]}")
    print(f"  fields captured        {report['fields']}")
    print(f"\nWrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
