#!/usr/bin/env python
"""
Revalidate-only tool — DRY RUN by default. No OCR, no model call, no network beyond the database.

    # 1. Dry run (writes nothing; the database session itself is READ ONLY):
    DATABASE_URL=postgresql+asyncpg://...  python scripts/revalidate_invoices.py \\
        --invoice-number 48288200 --invoice-number 501315

    # 2. After reviewing the printed before/after and the plan fingerprints, and only with separate approval:
    python scripts/revalidate_invoices.py --invoice-number 48288200 --apply --actor <admin username> \\
        --expect-plan 48288200=<plan sha256 printed by the dry run>

Brings stored invoices under the completeness and financial checks by setting their status, confidence and
`blocking_checks` exactly as the deployed revalidation would, using the stored OCR text and the stored lines. It never
edits lines, totals, proposals, mappings, historical EDI exports or inventory records, and it refuses (before writing
anything): an invoice with a POSTED inventory receipt, one out of the active workflow, an ambiguous invoice number, an
invoice where a stored line would be rewritten, a non-ADMIN or unknown `--actor`, and an `--expect-plan` that no longer
matches the invoice. See docs/revalidation-runbook.md.

Run a dry run with a READ-ONLY database role where one exists; the dry run is read-only regardless.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.services import revalidation_tool as tool  # noqa: E402


def target_database() -> str:
    """Host and database name only — enough to see which database this is, never a credential."""
    url = make_url(get_settings().database_url)
    return f"{url.host}:{url.port or ''}/{url.database}"


def show(plan: tool.Plan, *, applying: bool = False) -> None:
    b, a = plan.before, plan.after
    print(f"\n=== invoice #{plan.invoice_number}  ({plan.vendor or 'vendor unknown'})  id {plan.invoice_id}")
    print(f"  invoice status          {b.status:<16} -> {a.status}")
    print(f"  composite confidence    {b.composite_confidence or '-':<16} -> {a.composite_confidence}")
    print(f"  blocking_checks         {b.blocking_checks!s:<16} -> {a.blocking_checks}")
    print(f"  document lifecycle      {b.document_status:<16} -> {a.document_status}")
    print(f"  completeness (stored OCR text {'present' if plan.stored_ocr_text else 'ABSENT'}): {plan.completeness}")
    if plan.missing_codes:
        shown = ", ".join(plan.missing_codes[:12]) + (f" … (+{len(plan.missing_codes) - 12})" if len(plan.missing_codes) > 12 else "")
        print(f"  printed codes with no line  {len(plan.missing_codes)}: {shown}")
    print(f"  failed checks           {', '.join(plan.failed_checks) or 'none'}")
    print(f"  around the invoice      historical EDI exports: {plan.historical_edi_exports} | POSTED receipts: "
          f"{plan.posted_receipts} | submitted receipts: {plan.submitted_receipts} | Retailed: {'yes' if plan.retailed else 'no'}")
    print(f"  stored lines that the deployed revalidation would rewrite: {plan.rows_that_would_be_rewritten}")
    for warning in plan.warnings:
        print(f"  note: {warning}")
    if plan.refusals:
        print(f"  REFUSED (would not be applied): {', '.join(plan.refusals)}")
    else:
        verb = "CHANGES the invoice row" if applying else "WOULD CHANGE the invoice row"
        print(f"  {verb if plan.changes else 'nothing to change (already current)'}   plan sha256 {plan.fingerprint}")


async def run(args: argparse.Namespace) -> int:
    expected = dict(item.split("=", 1) for item in args.expect_plan)
    factory = get_session_factory()
    exit_code = 0
    print(f"database: {target_database()}   mode: {'APPLY' if args.apply else 'DRY RUN (read-only)'}")
    results = []
    for number in args.invoice_number:
        async with factory() as session:
            try:
                if args.apply:
                    plan, wrote = await tool.apply(session, number, expect_plan=expected.get(number),
                                                   actor_username=args.actor)
                    await session.commit()
                    show(plan, applying=True)
                    print(f"  >>> {'WRITTEN' if wrote else 'no write needed'}")
                else:
                    plan = await tool.plan(session, number)
                    await session.rollback()
                    show(plan)
                    wrote = False
                    if plan.refusals:
                        exit_code = 2
                results.append({**plan.to_dict(), "wrote": wrote})
            except tool.ToolRefusal as refusal:
                await session.rollback()
                print(f"\n=== invoice #{number}\n  REFUSED: {refusal}")
                results.append({"invoice_number": number, "refused": str(refusal), "reasons": refusal.reasons,
                                "wrote": False})
                exit_code = 2
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--invoice-number", action="append", required=True, default=[],
                        help="an invoice to revalidate; repeat for several. There is no 'all'.")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run, which writes nothing)")
    parser.add_argument("--actor", help="username of the ACTIVE ADMIN account on whose behalf --apply is recorded")
    parser.add_argument("--expect-plan", action="append", default=[], metavar="NUMBER=SHA256",
                        help="the plan fingerprint the dry run printed for that invoice; required for each with --apply")
    parser.add_argument("--json", action="store_true", help="also print the plans as JSON")
    args = parser.parse_args(argv)
    if args.apply:
        missing = [n for n in args.invoice_number if not any(e.startswith(f"{n}=") for e in args.expect_plan)]
        if missing or not args.actor:
            parser.error("--apply needs --actor and an --expect-plan NUMBER=SHA256 for every --invoice-number "
                         f"(missing: {', '.join(missing) or 'actor'}). Nothing was written.")
    elif args.expect_plan or args.actor:
        parser.error("--actor and --expect-plan only apply with --apply. This is a dry run; nothing was written.")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
