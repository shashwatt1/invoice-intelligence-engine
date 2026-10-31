#!/usr/bin/env python
"""
Merge two vendor rows that are one supplier — DRY RUN by default. No OCR, no model call, no network beyond the database.

    # 0. Find candidates (read-only): vendors whose names match each other.
    DATABASE_URL=postgresql+asyncpg://...  python scripts/merge_vendors.py --list-duplicates

    # 1. Dry run for a named pair (writes nothing; the database session itself is READ ONLY):
    python scripts/merge_vendors.py --winner <vendor id> --loser <vendor id>

    # 2. After reviewing the printed plan, and only with separate approval:
    python scripts/merge_vendors.py --winner <id> --loser <id> --apply --actor <admin username> \\
        --basis "why these are the same supplier" --expect-plan <plan sha256> --expect-database <host/db>

The WINNER stays; the LOSER is kept as a tombstone pointing at it (nothing is deleted). Invoices, proposals, documents' chosen
vendor, vendor requests and the vendor item crosswalk move to the winner. It refuses (before writing anything): a database other
than --expect-database, a non-ADMIN or unknown --actor, a missing --basis, a plan that changed since it was reviewed, a vendor
already merged, a confirmed vendor merged into an unconfirmed one, two confirmed vendors (--allow-both-confirmed), different tax
ids (--allow-different-tax-ids), names with nothing in common (--allow-different-names), an invoice with a POSTED receipt
(--allow-received-invoices) and a vendor item code the two map to different item codes. See docs/vendor-and-delivery-date.md.

Run a dry run with a READ-ONLY database role where one exists; the dry run is read-only regardless.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select, text  # noqa: E402

from app.database.session import get_session_factory  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.models.vendor import Vendor  # noqa: E402
from app.services import vendor_merge_service as merge  # noqa: E402
from app.services.db_target import actual_target, target_mismatch  # noqa: E402
from app.services.vendor_matching import names_match, tax_ids_match  # noqa: E402


def _listed(values: list[str]) -> str:
    shown = ", ".join(values[: merge.MAX_LISTED])
    return shown + (f" … (+{len(values) - merge.MAX_LISTED})" if len(values) > merge.MAX_LISTED else "")


def show(plan: merge.MergePlan, *, applying: bool = False) -> None:
    print(f"\n=== merge  {plan.loser}\n        into  {plan.winner}")
    print(f"  invoices that move            {len(plan.invoice_ids)}  "
          f"(Retailed: {plan.retailed_invoices} | with a POSTED receipt: {plan.posted_receipt_invoices})")
    if plan.invoice_numbers:
        print(f"    invoice numbers             {_listed(plan.invoice_numbers)}")
    print(f"  product proposals that move   {len(plan.proposal_ids)}  {plan.proposals_by_status or ''}")
    print(f"  documents' chosen vendor      {len(plan.document_ids)}")
    print(f"  vendor requests               {len(plan.request_ids)}")
    print(f"  crosswalk links               move {len(plan.crosswalk_move)} | retired as redundant {len(plan.crosswalk_retire)}")
    print(f"  identity reviews              {plan.identity_reviews_left_on_loser} stay on the merged-away vendor (audit)")
    for warning in plan.warnings:
        print(f"  note: {warning}")
    if plan.refusals:
        print(f"  REFUSED (would not be applied): {'; '.join(plan.refusals)}")
    else:
        print(f"  {'MERGES' if applying else 'WOULD MERGE'}   plan sha256 {plan.fingerprint}")


async def list_duplicates(factory) -> int:
    async with factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        vendors = (await session.execute(select(Vendor).where(Vendor.merged_into_id.is_(None)).order_by(Vendor.name))).scalars().all()
        counts = dict((await session.execute(select(Invoice.vendor_id, func.count()).group_by(Invoice.vendor_id))).all())
        # Read everything needed while the rows are loaded; the transaction ends only after.
        rows = [{"id": v.id, "label": v.label, "status": v.identity_status, "tax_id": v.tax_id,
                 "names": [n for n in (v.display_name, v.name) if n], "invoices": counts.get(v.id, 0)} for v in vendors]
        await session.rollback()
    groups: list[list[dict]] = []
    for row in rows:
        for group in groups:
            if any(names_match(a, b) or tax_ids_match(row["tax_id"], other["tax_id"])
                   for other in group for a in row["names"] for b in other["names"]):
                group.append(row)
                break
        else:
            groups.append([row])
    duplicates = [g for g in groups if len(g) > 1]
    print(f"{len(rows)} vendors; {len(duplicates)} group(s) that look like one supplier:")
    for group in duplicates:
        print()
        for r in group:
            print(f"  {r['id']}  {r['label']!r:45} {r['status']:10} tax_id={r['tax_id'] or '-':14} invoices={r['invoices']}")
    if duplicates:
        print("\nPick the vendor that stays (prefer the confirmed one) and run a dry run with --winner and --loser.")
    return 0


async def run(args: argparse.Namespace) -> int:
    factory = get_session_factory()
    async with factory() as probe:                       # the target, before anything is read for a plan
        target = await actual_target(probe)
        await probe.rollback()
    print(f"TARGET DATABASE: {target.describe()}")
    print(f"mode: {'APPLY' if args.apply else 'DRY RUN (read-only)'}   (to apply here: --expect-database {target.expect_flag})")
    if args.apply or args.expect_database:
        wrong = target_mismatch(args.expect_database, target)
        if wrong:
            print(f"\nREFUSED: {wrong} Nothing was read or written.")
            return 2
    if args.list_duplicates:
        return await list_duplicates(factory)

    winner, loser = uuid.UUID(args.winner), uuid.UUID(args.loser)
    options = merge.MergeOptions(allow_both_confirmed=args.allow_both_confirmed,
                                 allow_different_tax_ids=args.allow_different_tax_ids,
                                 allow_different_names=args.allow_different_names,
                                 allow_received_invoices=args.allow_received_invoices)
    async with factory() as session:
        try:
            if args.apply:
                plan, _ = await merge.apply(session, winner, loser, actor_username=args.actor, basis=args.basis,
                                            expect_plan=args.expect_plan, options=options)
                await session.commit()
                show(plan, applying=True)
                print("  >>> WRITTEN")
                code = 0
            else:
                plan = await merge.plan(session, winner, loser, options)
                await session.rollback()
                show(plan)
                code = 2 if plan.refusals else 0
        except merge.VendorMergeRefusal as refusal:
            await session.rollback()
            print(f"\n  REFUSED: {refusal}")
            return 2
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, default=str))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-duplicates", action="store_true", help="read-only: groups of vendors that look like one supplier")
    parser.add_argument("--winner", help="id of the vendor that stays")
    parser.add_argument("--loser", help="id of the duplicate to merge away")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run, which writes nothing)")
    parser.add_argument("--actor", help="username of the ACTIVE ADMIN account on whose behalf --apply is recorded")
    parser.add_argument("--basis", help="why these are the same supplier; recorded with the merge (required with --apply)")
    parser.add_argument("--expect-plan", metavar="SHA256", help="the plan fingerprint the dry run printed; required with --apply")
    parser.add_argument("--expect-database", metavar="HOST/DB",
                        help="the database you mean to write to, as the dry run prints it; required with --apply")
    parser.add_argument("--allow-both-confirmed", action="store_true")
    parser.add_argument("--allow-different-tax-ids", action="store_true")
    parser.add_argument("--allow-different-names", action="store_true")
    parser.add_argument("--allow-received-invoices", action="store_true",
                        help="include invoices that have a POSTED inventory receipt (only their vendor moves)")
    parser.add_argument("--json", action="store_true", help="also print the plan as JSON")
    args = parser.parse_args(argv)
    if args.list_duplicates:
        if args.apply or args.winner or args.loser:
            parser.error("--list-duplicates is read-only and takes no other action. Nothing was written.")
    else:
        if not args.winner or not args.loser:
            parser.error("--winner and --loser are required (or --list-duplicates).")
        try:
            uuid.UUID(args.winner), uuid.UUID(args.loser)
        except ValueError:
            parser.error("--winner and --loser must be vendor ids (--list-duplicates prints them).")
        if args.apply:
            if not (args.actor and args.basis and args.expect_plan and args.expect_database):
                parser.error("--apply needs --actor, --basis, --expect-plan SHA256 and --expect-database HOST/DB. "
                             "Nothing was written.")
        elif args.actor or args.expect_plan or args.basis:
            parser.error("--actor, --basis and --expect-plan only apply with --apply. This is a dry run; nothing was written.")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
