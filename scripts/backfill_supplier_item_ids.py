"""
Backfill invoice_items.supplier_item_id from stored model responses.

    python scripts/backfill_supplier_item_ids.py              # dry run: counts only, writes nothing
    python scripts/backfill_supplier_item_ids.py --apply      # writes (local database)
    python scripts/backfill_supplier_item_ids.py --apply --allow-remote   # a non-local database: needs approval

Migration 0038 added the column; invoices extracted before it keep the vendor's
item number only inside `raw_extraction_json`. This copies it onto the line with
the same fail-closed matching the review UI already uses
(app/services/supplier_item_context.py): a row gets a number only when it was
extracted (not added by hand), sits at its extracted position and still carries
its extracted description. A row that already has a value is never changed.

Prints counts per vendor, never descriptions, prices or the numbers themselves.
The database is the one DATABASE_URL points at.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.services.supplier_item_context import supplier_item_ids  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


async def run(apply: bool) -> int:
    filled: Counter[str] = Counter()
    seen = 0
    async with get_session_factory()() as session:
        invoices = (await session.execute(
            select(Invoice).options(selectinload(Invoice.items), selectinload(Invoice.vendor))
        )).scalars().all()
        for invoice in invoices:
            found = supplier_item_ids(invoice)
            for item in invoice.items:
                seen += 1
                if item.supplier_item_id or item.sort_order not in found:
                    continue
                item.supplier_item_id = found[item.sort_order][:64]
                filled[(invoice.vendor.name if invoice.vendor else "(no vendor)")[:40]] += 1
        if apply:
            await session.commit()
        else:
            await session.rollback()
    print(("APPLIED" if apply else "DRY RUN — nothing was written") + f": {sum(filled.values())} of {seen} lines")
    for vendor, count in filled.most_common():
        print(f"  {count:5}  {vendor}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="Write the values (default: dry run).")
    parser.add_argument("--allow-remote", action="store_true",
                        help="Permit a non-local database. Production runs need explicit approval.")
    args = parser.parse_args()
    host = make_url(get_settings().database_url).host or ""
    if args.apply and host not in LOCAL_HOSTS and not args.allow_remote:
        print(f"Refusing to write to non-local host {host!r} without --allow-remote.", file=sys.stderr)
        return 2
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
