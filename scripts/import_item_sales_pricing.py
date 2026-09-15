#!/usr/bin/env python
"""
Import Item Sales Summary exports as dated, source-aware pricing rows.

    python scripts/import_item_sales_pricing.py --dry-run data/reference/store_86357232/
    python scripts/import_item_sales_pricing.py data/reference/store_86357232/
    python scripts/import_item_sales_pricing.py "data/reference/store_47708760/Mckinley-07-24_to_07-26.xlsx"

Writes product_pricing — one row per source row, keyed by
(store, file, sheet, row), with the report period as the effective
range — so several exports for the same store, the same period, even
the same UPC, coexist with their provenance instead of overwriting one
another. The catalogue importer (scripts/import_store_reference.py) has
the opposite contract, one row per product.

Refuses, rather than guesses:
  * a file whose preamble names a store code attached to a different
    store than --store (a Store id or store code);
  * a file whose store code is attached to no store — pass --create-store
    to create an unresolved store for it, to be named by a person later;
  * a file with no store code and no --store;
  * a copy of a workbook already imported for the store under another
    filename (matched by content, not name) — pass
    --allow-duplicate-content to import it anyway.
A directory contributes only Item_Sales*.xlsx files, and identical files
in one run are read once.

Writes nothing to product_case_mappings.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.session import get_session_factory  # noqa: E402
from app.repositories.product_reference_repository import (  # noqa: E402
    ProductReferenceRepository,
    records_fingerprint,
)
from app.services.item_sales_pricing_import import parse_item_sales_pricing  # noqa: E402
from app.services.reference_workbooks import discover_workbooks  # noqa: E402
from app.services.store_resolution import (  # noqa: E402
    StoreResolutionError,
    resolve_item_sales_store,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+", help="xlsx files or directories of Item_Sales*.xlsx")
    parser.add_argument("--store", help="Store id or Item Sales store code; must agree with the file")
    parser.add_argument("--create-store", action="store_true",
                        help="create an unresolved store for a store code the master does not know")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-duplicate-content", action="store_true",
                        help="import a file whose content is already present under another name")
    args = parser.parse_args()

    try:
        found = discover_workbooks(args.targets)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    for copy, original in found.duplicates:
        print(f"SKIP {copy.name}: identical content to {original.name}")
    if not found.selected:
        raise SystemExit("No workbooks to import.")

    async with get_session_factory()() as session:
        reports = []
        for path in found.selected:
            report = parse_item_sales_pricing(path)
            try:
                resolved = await resolve_item_sales_store(
                    session, source_code=report.store_number, requested=args.store,
                    filename=path.name, create_missing=args.create_store and not args.dry_run,
                )
            except StoreResolutionError as exc:
                await session.rollback()
                raise SystemExit(str(exc)) from exc
            dup_in_file = len(report.records) - len({r.item_code for r in report.records})
            print(f"{path.name}  store code {report.store_number} → {resolved.store.label} "
                  f"[{resolved.store.id}]{'  (store created)' if resolved.created else ''}  "
                  f"period {report.period_start} → {report.period_end}")
            print(f"  source rows {len(report.records)}   distinct UPCs {len(report.records) - dup_in_file}   "
                  f"repeated-in-file {dup_in_file}   with retail {report.with_retail}   "
                  f"with cost {report.with_cost}   skipped {report.skipped}")
            reports.append((path, resolved.store, report))

        if args.dry_run:
            await session.rollback()
            print("DRY RUN — nothing written.")
            return 0

        repository = ProductReferenceRepository(session)
        for path, store, report in reports:
            fingerprints = await repository.content_fingerprints(store.id)
            mine = records_fingerprint(report.records)
            twins = [f for f, fp in fingerprints.items() if f != path.name and fp == mine]
            if twins and not args.allow_duplicate_content:
                await session.rollback()
                raise SystemExit(
                    f"{path.name}: {store.label} already has this exact content as "
                    f"{twins[0]!r}. A second name for the same rows would count as a second "
                    "agreeing source. Nothing written; pass --allow-duplicate-content to override."
                )
            counts = await repository.import_records(
                report.records, store_id=store.id, source_file=path.name,
                imported_at=datetime.now(UTC),
            )
            print(f"WROTE {path.name}: {counts}")
        await session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
