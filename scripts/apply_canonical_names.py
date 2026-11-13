"""
Canonical names from an owner-sanctioned list — scripts/apply_canonical_names.py — DRY RUN by default.

    python scripts/apply_canonical_names.py --names canonical-names.json \
        --source item_master:xo_liquor_boulder --source-file <list file name> [--demote-others] [--apply] [--allow-remote]

Makes each listed name the product's CANONICAL description: a CANONICAL-role description row (evidence CONFIRMED,
provenance = the given source and file) plus master_products.canonical_description. With --demote-others, any other
CANONICAL description the product holds becomes a HISTORICAL_ALIAS (kept for audit, never deleted), so the review
screens show the listed name. Then it checks, with the same resolve_display_name the review screens use, that every
listed product now shows its listed name. One transaction: nothing is committed unless --apply is given and every
listed product exists and shows its name. A second run changes nothing.

Owner decision 2026-11-13 (docs/product-master-description-policy.md, section 7): for the products matched in
docs/reference-data-2026-11-13.md the XO Liquor Boulder list's names are canonical. This tool only applies a list a
person decided on; it never chooses a name itself. Input: [{"upc": "<12-digit UPC-A>", "name": "<name>"}, ...]
(scripts/match_pdi_items.py writes it).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.product_master import MasterProduct, MasterProductDescription  # noqa: E402
from app.services.product_master.canonical_names import set_canonical_name  # noqa: E402
from app.services.product_master.display_name import resolve_display_name  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class Outcome:
    listed: int = 0
    set: int = 0
    unchanged: int = 0
    missing: int = 0
    demoted: int = 0
    shown_correctly: int = 0

    @property
    def complete(self) -> bool:
        return self.missing == 0 and self.shown_correctly == self.listed


async def apply_canonical_names(session: AsyncSession, items: list[dict], *, source_system: str, source_file: str,
                                demote_others: bool) -> Outcome:
    """Flushes; the caller commits or rolls back. The naming itself is set_canonical_name's."""
    out = Outcome(listed=len(items))
    for item in items:
        product = (await session.execute(select(MasterProduct).where(
            MasterProduct.canonical_upc == item["upc"]))).scalars().first()
        if product is None:
            out.missing += 1
            continue
        change = await set_canonical_name(session, product, item["name"], source_system=source_system,
                                          source_file=source_file,
                                          demote="other_sources" if demote_others else "none")
        out.set += change.changed
        out.unchanged += not change.changed
        out.demoted += change.demoted
    await session.flush()
    for item in items:
        name = " ".join(str(item["name"]).split())
        product = (await session.execute(select(MasterProduct).where(
            MasterProduct.canonical_upc == item["upc"]))).scalars().first()
        if product is None:
            continue
        rows = (await session.execute(select(MasterProductDescription).where(
            MasterProductDescription.product_id == product.id))).scalars().all()
        if resolve_display_name(rows).name == name and product.canonical_description == name:
            out.shown_correctly += 1
    return out


async def run(args) -> int:
    from app.database.session import get_session_factory

    items = json.loads(Path(args.names).read_text())
    async with get_session_factory()() as session:
        out = await apply_canonical_names(session, items, source_system=args.source,
                                          source_file=args.source_file, demote_others=args.demote_others)
        print(f"listed: {out.listed} | name set: {out.set} | already set: {out.unchanged} | product not found: "
              f"{out.missing} | other canonical names demoted: {out.demoted} | shown correctly: "
              f"{out.shown_correctly} of {out.listed}")
        if args.apply and out.complete:
            await session.commit()
            print("COMMITTED")
            return 0
        await session.rollback()
        print("ROLLED BACK - " + ("dry run (add --apply to write)" if not args.apply else "not every product exists and shows its name"))
        return 0 if not args.apply else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--names", required=True)
    parser.add_argument("--source", required=True, help="source_system recorded on the CANONICAL rows")
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--demote-others", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-remote", action="store_true",
                        help="Permit a non-local database. Production changes need explicit approval.")
    args = parser.parse_args()
    host = make_url(get_settings().database_url).host or ""
    if args.apply and host not in LOCAL_HOSTS and not args.allow_remote:
        print(f"Refusing to write to non-local host {host!r} without --allow-remote.", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
