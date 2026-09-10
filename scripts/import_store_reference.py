#!/usr/bin/env python
"""
Store reference importer — scripts/import_store_reference.py

Imports a store's Item Sales Summary exports into
store_product_references, as a UNION across every file given.

    python scripts/import_store_reference.py --dry-run data/reference/store_47708760/
    python scripts/import_store_reference.py data/reference/store_47708760/
    python scripts/import_store_reference.py --store 47708760 file.xlsx

Idempotent: rows are upserted on (store_number, item_code), so importing
the same export twice updates in place and leaves the row count
unchanged. --dry-run parses and reports without opening a write
transaction.

An operational tool. No production code path imports it, and it never
touches product_case_mappings — confirmed units-per-case stays the
authority for what reaches the EDI.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.session import get_session_factory  # noqa: E402
from app.repositories.store_product_reference_repository import (  # noqa: E402
    StoreProductReferenceRepository,
)
from app.services.store_reference_import import parse_item_sales_summary  # noqa: E402


def _workbooks(targets: list[str]) -> list[Path]:
    paths: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            paths.extend(sorted(p for p in path.glob("*.xlsx") if not p.name.startswith("~$")))
        elif path.is_file():
            paths.append(path)
        else:
            raise SystemExit(f"Not found: {target}")
    if not paths:
        raise SystemExit("No .xlsx files found.")
    return paths


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", help="xlsx files or a directory of them")
    parser.add_argument("--store", help="store number (default: read from the file preamble)")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and report; write nothing")
    args = parser.parse_args()

    paths = _workbooks(args.targets)
    imported_at = datetime.now(UTC)

    # Union across files, keyed by normalized code. Later files win on a
    # collision, which is reported rather than silently resolved.
    union: dict[tuple[str, str], dict] = {}
    collisions: list[tuple[str, str, str]] = []
    totals = {"parsed": 0, "no_code": 0, "dup_in_file": 0, "zero_cost_nulled": 0}

    print(f"{'file':<46}{'store':>10}{'rows':>7}{'costed':>8}{'0->NULL':>9}{'skipped':>9}")
    for path in paths:
        result = parse_item_sales_summary(path)
        store = args.store or result.store_number
        if not store:
            raise SystemExit(f"{path.name}: no store number in the file; pass --store.")
        totals["parsed"] += len(result.rows)
        totals["no_code"] += result.skipped_no_code
        totals["dup_in_file"] += result.skipped_duplicate
        totals["zero_cost_nulled"] += result.zero_cost_nulled
        print(f"{path.name[:45]:<46}{store:>10}{len(result.rows):>7}"
              f"{result.costed:>8}{result.zero_cost_nulled:>9}"
              f"{result.skipped_no_code + result.skipped_duplicate:>9}")

        for row in result.rows:
            key = (store, row.item_code)
            if key in union:
                collisions.append((row.item_code, union[key]["source_file"], path.name))
            union[key] = {
                "store_number": store,
                "item_code": row.item_code,
                "scan_code_raw": row.scan_code_raw,
                "description": row.description,
                "avg_cost": row.avg_cost,
                "avg_price": row.avg_price,
                "source_file": path.name,
                "imported_at": imported_at,
            }

    rows = list(union.values())
    costed = sum(1 for r in rows if r["avg_cost"] is not None)
    print(f"\nUNION: {len(rows)} distinct products across {len(paths)} file(s)")
    print(f"  costed (avg_cost present)  : {costed}")
    print(f"  uncosted (avg_cost NULL)   : {len(rows) - costed}")
    print(f"  rows parsed before union   : {totals['parsed']}")
    print(f"  skipped, no usable code    : {totals['no_code']}")
    print(f"  skipped, duplicate in file : {totals['dup_in_file']}")
    print(f"  zero cost stored as NULL   : {totals['zero_cost_nulled']}")
    if collisions:
        print(f"  cross-file collisions      : {len(collisions)} (later file wins)")
        for code, first, second in collisions[:10]:
            print(f"      {code}  {first} -> {second}")

    if args.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    session_factory = get_session_factory()
    async with session_factory() as session:
        repository = StoreProductReferenceRepository(session)
        inserted, updated = await repository.upsert_many(rows)
        await session.commit()
        stats = await repository.stats(rows[0]["store_number"])

    print(f"\nWROTE: {inserted} inserted, {updated} updated")
    print(f"  store_product_references now: {stats['total']} rows "
          f"({stats['costed']} costed, {stats['uncosted']} uncosted, "
          f"{stats['distinct_item_codes']} distinct codes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
