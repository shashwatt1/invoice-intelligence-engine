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
  * a file whose preamble names a different store than --store;
  * a file with no store number and no --store;
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


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+", help="xlsx files or directories of Item_Sales*.xlsx")
    parser.add_argument("--store", help="must match the store named in each file's preamble")
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

    reports = []
    for path in found.selected:
        report = parse_item_sales_pricing(path)
        store = report.store_number
        if store is None and args.store is None:
            raise SystemExit(f"{path.name}: no store number in the preamble; pass --store.")
        if store is None:
            store = args.store
        elif args.store is not None and args.store != store:
            raise SystemExit(
                f"{path.name}: the file says store {store} but --store {args.store} was given. "
                "Refusing to file one store's data under another."
            )
        dup_in_file = len(report.records) - len({r.item_code for r in report.records})
        print(f"{path.name}  store {store}  period {report.period_start} → {report.period_end}")
        print(f"  source rows {len(report.records)}   distinct UPCs {len(report.records) - dup_in_file}   "
              f"repeated-in-file {dup_in_file}   with retail {report.with_retail}   "
              f"with cost {report.with_cost}   skipped {report.skipped}")
        reports.append((path, store, report))

    if args.dry_run:
        print("DRY RUN — nothing written.")
        return 0

    async with get_session_factory()() as session:
        repository = ProductReferenceRepository(session)
        for path, store, report in reports:
            fingerprints = await repository.content_fingerprints(store)
            mine = records_fingerprint(report.records)
            twins = [f for f, fp in fingerprints.items() if f != path.name and fp == mine]
            if twins and not args.allow_duplicate_content:
                raise SystemExit(
                    f"{path.name}: store {store} already has this exact content as "
                    f"{twins[0]!r}. A second name for the same rows would count as a second "
                    "agreeing source. Nothing written; pass --allow-duplicate-content to override."
                )
            counts = await repository.import_records(
                report.records, store_number=store, source_file=path.name,
                imported_at=datetime.now(UTC),
            )
            print(f"WROTE {path.name}: {counts}")
        await session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
