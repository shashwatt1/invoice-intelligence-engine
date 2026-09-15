#!/usr/bin/env python
"""
Beer Inventory importer — scripts/import_beer_inventory.py

    python scripts/import_beer_inventory.py --dry-run "data/reference/store_47708760/Beer Inventory.xlsx"
    python scripts/import_beer_inventory.py "data/reference/store_47708760/Beer Inventory.xlsx"

Imports all eight sheets into product_pricing / product_identity /
product_identifier with full provenance. Idempotent: pricing rows are
keyed by (file, sheet, row) and update in place. The raw workbook is
never modified.

Conflicted source rows — where the sheet's own arithmetic disagrees
with itself — are imported FLAGGED so the disagreement is on record,
and are excluded from every evidence query.

Writes nothing to product_case_mappings. Never will.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.session import get_session_factory  # noqa: E402
from app.repositories.product_reference_repository import ProductReferenceRepository  # noqa: E402
from app.services.beer_inventory_import import parse_beer_inventory  # noqa: E402
from app.services.store_resolution import StoreResolutionError, resolve_explicit_store  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook")
    parser.add_argument("--store", required=True,
                        help="Store id or Item Sales store code — the workbook carries no store "
                             "preamble, so a person must say whose it is")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.workbook)
    if not path.is_file():
        raise SystemExit(f"Not found: {path}")
    report = parse_beer_inventory(path)
    async with get_session_factory()() as session:
        try:
            store = await resolve_explicit_store(session, args.store, path.name)
        except StoreResolutionError as exc:
            raise SystemExit(str(exc)) from exc
        store_id, store_label = store.id, store.label      # read while the session is open
    print(f"{path.name}  store {store_label} [{store_id}]\n")
    print(f"{'sheet':<20}{'rows':>6}{'with UPC':>10}{'stated':>8}{'package':>9}{'ratio':>7}{'conflicted':>12}")
    for sheet, count in report.per_sheet.items():
        rows = [r for r in report.records if r.sheet == sheet]
        print(f"{sheet:<20}{count:>6}{sum(1 for r in rows if r.item_code):>10}"
              f"{sum(1 for r in rows if r.items_per_case_stated):>8}"
              f"{sum(1 for r in rows if r.items_per_case_derivation == 'package'):>9}"
              f"{sum(1 for r in rows if r.items_per_case_derivation == 'ratio'):>7}"
              f"{sum(1 for r in rows if r.is_conflicted):>12}")
    codes = {r.item_code for r in report.records if r.item_code}
    print(f"\n  total rows {len(report.records)}   distinct UPCs {len(codes)}   "
          f"no usable UPC {report.skipped_no_upc}   conflicted {report.conflicted}")
    print(f"  pricing basis: {dict(Counter(r.pricing_basis for r in report.records))}")
    print(f"  dated rows   : {sum(1 for r in report.records if r.effective_from)}")

    if report.conflicted:
        print("\n  CONFLICTED (imported flagged, excluded from evidence):")
        for r in report.records:
            if r.is_conflicted:
                print(f"    {r.sheet} row {r.row:<5} {str(r.description)[:26]:<28} "
                      f"{r.conflict_detail.get('items_per_case')}")

    if args.dry_run:
        print("\nDRY RUN — nothing written.")
        return 0

    async with get_session_factory()() as session:
        counts = await ProductReferenceRepository(session).import_records(
            report.records, store_id=store_id, source_file=path.name,
            imported_at=datetime.now(UTC),
        )
        await session.commit()
    print("\nWROTE:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
