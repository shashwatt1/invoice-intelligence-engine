#!/usr/bin/env python
"""
Revalidate-only tool — DRY RUN by default. No OCR, no model call, no network beyond the database.

    # 1. Dry run (writes nothing; the database session itself is READ ONLY):
    DATABASE_URL=postgresql+asyncpg://...  python scripts/revalidate_invoices.py \\
        --invoice-number 48288200 --invoice-number 501315

    # 2. After reviewing the printed before/after and the plan fingerprints, and only with separate approval:
    python scripts/revalidate_invoices.py --invoice-number 48288200 --apply --actor <admin username> \\
        --expect-plan 48288200=<plan sha256 printed by the dry run> --expect-database <host/db printed by the dry run>

Brings stored invoices under the completeness and financial checks by setting their status, confidence and
`blocking_checks` exactly as the deployed revalidation would, using the stored OCR text and the stored lines. It never
edits lines, totals, proposals, mappings, historical EDI exports or inventory records, and it refuses (before writing
anything): an invoice with a POSTED inventory receipt, one out of the active workflow, an ambiguous invoice number, an
invoice where a stored line would be rewritten, a non-ADMIN or unknown `--actor`, an `--expect-plan` that no longer
matches the invoice, and an `--expect-database` that does not match the database actually connected to (checked before
anything is read for an apply). `--apply` requires `--expect-database host/db`; a dry run accepts it too. See docs/revalidation-runbook.md.

Run a dry run with a READ-ONLY database role where one exists; the dry run is read-only regardless.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.database.session import get_session_factory  # noqa: E402
from app.services import revalidation_tool as tool  # noqa: E402


@dataclass(frozen=True)
class Target:
    host: str
    port: int | None
    database: str            # from the connection URL
    connected_database: str  # what the server says it is (select current_database())

    @property
    def expect_flag(self) -> str:
        return f"{self.host}/{self.database}"

    def describe(self) -> str:
        port = f":{self.port}" if self.port else ""
        return f"host={self.host}{port}  database={self.database}  (server reports: {self.connected_database})"


async def actual_target(session) -> Target:
    """
    The database this session is really connected to: host and name from the engine's URL (never the password) and
    the name the server itself reports. Both come from the connection in use, not from a settings file.
    """
    url = session.get_bind().url
    connected = (await session.execute(text("select current_database()"))).scalar_one()
    return Target(host=(url.host or ""), port=url.port, database=(url.database or ""), connected_database=connected)


def target_mismatch(expected: str | None, target: Target) -> str | None:
    """Why `expected` (host/db, or host:port/db) is not this target; None when it is. Pure."""
    if not expected or "/" not in expected:
        return "--expect-database must look like host/db (the dry run prints it)."
    where, name = expected.rsplit("/", 1)
    host, _, port = where.partition(":")
    if not host or not name:
        return "--expect-database must look like host/db (the dry run prints it)."
    problems = []
    if host.lower() != target.host.lower():
        problems.append(f"host {target.host!r}")
    if port and port != str(target.port):
        problems.append(f"port {target.port}")
    if name != target.database or name != target.connected_database:
        problems.append(f"database {target.database!r} (server reports {target.connected_database!r})")
    return None if not problems else f"The connected database is not the one expected; it differs in: {', '.join(problems)}."


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
    parser.add_argument("--expect-database", metavar="HOST/DB",
                        help="the database you mean to write to, as the dry run prints it; required with --apply and "
                             "compared with the database actually connected to before anything is read")
    parser.add_argument("--json", action="store_true", help="also print the plans as JSON")
    args = parser.parse_args(argv)
    if args.apply:
        missing = [n for n in args.invoice_number if not any(e.startswith(f"{n}=") for e in args.expect_plan)]
        if missing or not args.actor or not args.expect_database:
            parser.error("--apply needs --actor, --expect-database HOST/DB and an --expect-plan NUMBER=SHA256 for "
                         f"every --invoice-number (missing: {', '.join(missing) or ('actor' if not args.actor else 'expect-database')}). "
                         "Nothing was written.")
    elif args.expect_plan or args.actor:
        parser.error("--actor and --expect-plan only apply with --apply. This is a dry run; nothing was written.")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
