#!/usr/bin/env python
"""
Import ONE Item Sales Summary export as dated pricing rows.

    python scripts/import_item_sales_pricing.py --dry-run "data/reference/store_47708760/Mckinley-07-24_to_07-26.xlsx"
    python scripts/import_item_sales_pricing.py "data/reference/store_47708760/Mckinley-07-24_to_07-26.xlsx"

Takes an explicit file, never a directory: the catalogue importer
(scripts/import_store_reference.py) globs a folder and must not pick
this up, because a second period would overwrite the first in its
one-row-per-product table. This path writes product_pricing instead —
one row per source row, with the report period as the effective range —
so two periods coexist.

Writes nothing to product_case_mappings.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.repositories.product_reference_repository import ProductReferenceRepository  # noqa: E402
from app.services.item_sales_pricing_import import parse_item_sales_pricing  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", help="one .xlsx file (not a directory)")
    parser.add_argument("--store")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = Path(args.workbook)
    if not path.is_file():
        raise SystemExit(f"Not a file: {path}")

    report = parse_item_sales_pricing(path)
    store = args.store or report.store_number or get_settings().store_number
    print(f"{path.name}  store {store}  period {report.period_start} → {report.period_end}")
    print(f"  rows {len(report.records)}   with retail {report.with_retail}   "
          f"with cost {report.with_cost}   skipped {report.skipped}")
    if args.dry_run:
        print("DRY RUN — nothing written.")
        return 0
    async with get_session_factory()() as session:
        counts = await ProductReferenceRepository(session).import_records(
            report.records, store_number=store, source_file=path.name,
            imported_at=datetime.now(UTC),
        )
        await session.commit()
    print("WROTE:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
