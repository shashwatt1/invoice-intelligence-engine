"""
Import a product list into the Product Master — preview first, always.

    python scripts/import_item_master.py FILE --mapping scripts/item_master_mappings/<name>.json
        [--sheet NAME] [--header-row N] [--output preview.json]           # PREVIEW: writes nothing
    python scripts/import_item_master.py FILE --mapping ... --apply        # writes (local database)
    python scripts/import_item_master.py FILE --mapping ... --apply --allow-remote   # needs approval

The preview classifies every row NEW / EXISTING / POSSIBLE_UPDATE / DUPLICATE /
CONFLICT / INVALID (see app/services/product_master/item_master_import.py) and
prints the counts and reasons. --apply writes exactly what the preview listed,
in one transaction, then re-runs the preview to show that a second run would
write nothing. Units-per-case is only ever written as a REVIEW_REQUIRED shared
candidate for Product Master Approvals.

Output never contains descriptions or prices; --output writes the per-row
detail (which does include the source descriptions) to the file you name.
The database is the one DATABASE_URL points at.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.services.product_master import item_master_import as importer  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _print(summary: dict, title: str) -> None:
    print(title)
    print(f"  source: {summary['source_file']}" + (f" [{summary['sheet']}]" if summary["sheet"] else "")
          + f"  as {summary['source_system']}  rows: {summary['rows']}")
    for category, count in summary["counts"].items():
        print(f"  {category:16} {count}")
    print(f"  units-per-case candidates for review: {summary['units_candidates']}")
    for reason, count in list(summary["reasons"].items())[:15]:
        print(f"    {count:6}  {reason}")


async def run(args) -> int:
    mapping = importer.ColumnMapping.from_file(args.mapping)
    overrides = {k: v for k, v in (("sheet", args.sheet), ("header_row", args.header_row)) if v is not None}
    if overrides:
        mapping = dataclasses.replace(mapping, **overrides)
    async with get_session_factory()() as session:
        plan = await importer.preview(session, args.file, mapping)
        if args.output:
            Path(args.output).write_text(json.dumps(plan.to_dict(), indent=2, default=str))
        if not args.apply:
            _print(plan.summary(), "PREVIEW — nothing was written")
            await session.rollback()
            return 0
        written = await importer.apply(session, plan, mapping)
        await session.commit()
        _print(plan.summary(), "APPLIED")
        print("  written:", json.dumps(written))
        again = await importer.preview(session, args.file, mapping)
        pending = sum(1 for r in again.rows if r.writes)
        print(f"  a second run would write: {pending} row(s)")
        await session.rollback()
        return 0 if pending == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file")
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--sheet")
    parser.add_argument("--header-row", type=int)
    parser.add_argument("--output")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-remote", action="store_true",
                        help="Permit a non-local database. Production imports need explicit approval.")
    args = parser.parse_args()
    host = make_url(get_settings().database_url).host or ""
    if args.apply and host not in LOCAL_HOSTS and not args.allow_remote:
        print(f"Refusing to write to non-local host {host!r} without --allow-remote.", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
