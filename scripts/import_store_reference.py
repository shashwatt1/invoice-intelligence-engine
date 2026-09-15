#!/usr/bin/env python
"""
Store reference importer — scripts/import_store_reference.py

Imports a store's Item Sales Summary exports into
store_product_references, as a UNION across every file given.

    python scripts/import_store_reference.py --dry-run data/reference/store_47708760/
    python scripts/import_store_reference.py data/reference/store_47708760/
    python scripts/import_store_reference.py --store 47708760 file.xlsx

Idempotent: rows are upserted on (store_id, item_code), so importing
the same export twice updates in place and leaves the row count
unchanged. Identical files under different names are read once. Two
files that disagree about a product are reported and, by default, that
product is left out rather than one reading silently chosen
(--on-conflict). A file naming a different store than --store is
refused. --dry-run parses and reports without opening a write
transaction.

An operational tool. No production code path imports it, and it never
touches product_case_mappings — confirmed units-per-case stays the
authority for what reaches the EDI.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.session import get_session_factory  # noqa: E402
from app.repositories.store_product_reference_repository import (  # noqa: E402
    StoreProductReferenceRepository,
)
from app.services.reference_workbooks import discover_workbooks  # noqa: E402
from app.services.store_reference_import import ParseResult, parse_item_sales_summary  # noqa: E402
from app.services.store_resolution import (  # noqa: E402
    StoreResolutionError,
    resolve_item_sales_store,
)


def _workbooks(targets: list[str]) -> list[Path]:
    """The catalogue exports to read: Item_Sales*.xlsx only, identical files once."""
    try:
        found = discover_workbooks(targets)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    for copy, original in found.duplicates:
        print(f"SKIP {copy.name}: identical content to {original.name}")
    if not found.selected:
        raise SystemExit("No .xlsx files found.")
    return found.selected


ON_CONFLICT = ("skip", "first", "last")


def build_union(
    parsed: list[tuple[str, ParseResult, uuid.UUID]],
    *,
    imported_at: datetime,
    on_conflict: str = "skip",
) -> tuple[list[dict], list[dict]]:
    """
    One catalogue row per (store, product) across the files of a run.

    Two files that say the same thing about a product merge silently.
    Two files that DISAGREE (description, Avg Cost or Avg Price) are a
    source-level difference, and by default neither is chosen: the
    product is left out of the catalogue and reported, while the per-row
    pricing table keeps both readings with their provenance. --on-conflict
    first|last picks one explicitly. Returns (rows, conflicts).
    """
    union: dict[tuple[uuid.UUID, str], dict] = {}
    conflicts: list[dict] = []
    for filename, result, store_id in parsed:
        for row in result.rows:
            key = (store_id, row.item_code)
            candidate = {
                "store_id": store_id, "item_code": row.item_code,
                "scan_code_raw": row.scan_code_raw, "description": row.description,
                "avg_cost": row.avg_cost, "avg_price": row.avg_price,
                "source_file": filename, "imported_at": imported_at,
            }
            current = union.get(key)
            if current is None:
                union[key] = candidate
                continue
            same = all(current[f] == candidate[f] for f in ("description", "avg_cost", "avg_price"))
            if same:
                continue
            conflicts.append({
                "item_code": row.item_code,
                "first": {"source_file": current["source_file"], "description": current["description"],
                          "avg_cost": current["avg_cost"], "avg_price": current["avg_price"]},
                "second": {"source_file": filename, "description": row.description,
                           "avg_cost": row.avg_cost, "avg_price": row.avg_price},
            })
            if on_conflict == "last":
                union[key] = candidate
            elif on_conflict == "skip":
                union[key] = {**current, "_skip": True}
            # "first": keep what is there
    rows = [r for r in union.values() if not r.pop("_skip", False)]
    return rows, conflicts


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", help="xlsx files or a directory of them")
    parser.add_argument("--store", help="Store id or Item Sales store code; must agree with each file")
    parser.add_argument("--create-store", action="store_true",
                        help="create an unresolved store for a store code the master does not know")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and report; write nothing")
    parser.add_argument("--on-conflict", choices=ON_CONFLICT, default="skip",
                        help="when two files disagree on a product: skip it (default), "
                             "or keep the first/last file's reading")
    args = parser.parse_args()

    paths = _workbooks(args.targets)
    imported_at = datetime.now(UTC)

    session_factory = get_session_factory()
    session = session_factory()
    parsed: list[tuple[str, ParseResult, uuid.UUID]] = []
    stores: dict[uuid.UUID, object] = {}
    totals = {"parsed": 0, "no_code": 0, "dup_in_file": 0, "zero_cost_nulled": 0}
    print(f"{'file':<46}{'store code':>11}{'rows':>7}{'costed':>8}{'0->NULL':>9}{'skipped':>9}")
    for path in paths:
        result = parse_item_sales_summary(path)
        try:
            resolved = await resolve_item_sales_store(
                session, source_code=result.store_number, requested=args.store,
                filename=path.name, create_missing=args.create_store and not args.dry_run,
            )
        except StoreResolutionError as exc:
            await session.rollback()
            await session.close()
            raise SystemExit(str(exc)) from exc
        stores[resolved.store.id] = resolved.store
        totals["parsed"] += len(result.rows)
        totals["no_code"] += result.skipped_no_code
        totals["dup_in_file"] += result.skipped_duplicate
        totals["zero_cost_nulled"] += result.zero_cost_nulled
        print(f"{path.name[:45]:<46}{str(result.store_number):>11}{len(result.rows):>7}"
              f"{result.costed:>8}{result.zero_cost_nulled:>9}"
              f"{result.skipped_no_code + result.skipped_duplicate:>9}"
              f"   → {resolved.store.label}{'  (store created)' if resolved.created else ''}")
        parsed.append((path.name, result, resolved.store.id))

    rows, conflicts = build_union(parsed, imported_at=imported_at, on_conflict=args.on_conflict)
    costed = sum(1 for r in rows if r["avg_cost"] is not None)
    print(f"\nUNION: {len(rows)} distinct products across {len(paths)} file(s)")
    print(f"  costed (avg_cost present)  : {costed}")
    print(f"  uncosted (avg_cost NULL)   : {len(rows) - costed}")
    print(f"  rows parsed before union   : {totals['parsed']}")
    print(f"  skipped, no usable code    : {totals['no_code']}")
    print(f"  skipped, duplicate in file : {totals['dup_in_file']}")
    print(f"  zero cost stored as NULL   : {totals['zero_cost_nulled']}")
    if conflicts:
        action = {"skip": "left out of the catalogue", "first": "first file kept",
                  "last": "last file kept"}[args.on_conflict]
        print(f"  cross-file disagreements   : {len(conflicts)} ({action}; "
              "both readings remain in product_pricing)")
        for c in conflicts[:10]:
            print(f"      {c['item_code']}  {c['first']['source_file']}: "
                  f"{c['first']['description']!r} cost={c['first']['avg_cost']} "
                  f"price={c['first']['avg_price']}")
            print(f"      {'':<11}  {c['second']['source_file']}: "
                  f"{c['second']['description']!r} cost={c['second']['avg_cost']} "
                  f"price={c['second']['avg_price']}")

    if args.dry_run:
        await session.rollback()
        await session.close()
        print("\nDRY RUN — nothing written.")
        return 0

    async with session:
        repository = StoreProductReferenceRepository(session)
        inserted, updated = await repository.upsert_many(rows)
        await session.commit()
        stats = await repository.stats(rows[0]["store_id"])

    print(f"\nWROTE: {inserted} inserted, {updated} updated")
    print(f"  store_product_references now: {stats['total']} rows "
          f"({stats['costed']} costed, {stats['uncosted']} uncosted, "
          f"{stats['distinct_item_codes']} distinct codes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
