#!/usr/bin/env python
"""
Data review CLI — scripts/review_proposals.py

The data team's gate. A proposal reaches authoritative master data only
through `approve` here; nothing in the frontend can do it.

    python scripts/review_proposals.py list [--status PENDING] [--source reference_derived]
    python scripts/review_proposals.py show <id>
    python scripts/review_proposals.py approve <id> --by "name" [--note "..."]
    python scripts/review_proposals.py reject  <id> --by "name" [--note "..."]
    python scripts/review_proposals.py approve-batch --source reference_derived --by "name"
    python scripts/review_proposals.py reject-batch  --invoice <uuid> --by "name" --note "..."

Every approval is one transaction: the proposal is frozen APPROVED and
the authoritative row is written and linked, or neither happens. A
reviewed proposal is immutable — `approve` on an APPROVED or REJECTED
row is refused, and a changed value is a new proposal.

--by is required on every decision. There is no login yet; the name is
recorded as given, which is a weaker guarantee than a session but a
much stronger one than the previous state, where nothing was recorded
at all.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.session import get_session_factory  # noqa: E402
from app.models.product_data_proposal import (  # noqa: E402
    STATUS_PENDING,
    VALID_SOURCES,
    VALID_STATUSES,
)
from app.repositories.product_data_proposal_repository import (  # noqa: E402
    ProductDataProposalRepository,
    ProposalImmutableError,
)
from app.services import proposal_service  # noqa: E402


def _short(value, width):
    text = "" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def _row(p) -> str:
    cur = "—" if p.current_value is None else str(p.current_value)
    return (
        f"{str(p.id)[:8]}  {p.status:<9} {p.entity_key:<12} {p.field:<15} "
        f"{str(p.proposed_value):>5}  (was {cur:>3})  {p.source:<26} "
        f"{_short(p.proposed_by, 22):<22} {p.created_at:%m-%d %H:%M}"
    )


async def cmd_list(args) -> int:
    async with get_session_factory()() as session:
        repo = ProductDataProposalRepository(session)
        rows = await repo.list(
            status=args.status, source=args.source,
            invoice_id=uuid.UUID(args.invoice) if args.invoice else None,
            entity_key=args.key,
        )
    if not rows:
        print("No proposals match.")
        return 0
    print(f"{'id':<9} {'status':<9} {'item code':<12} {'field':<15} {'value':>5}  "
          f"{'current':>9}  {'source':<26} {'proposed by':<22} created")
    for p in rows:
        print(_row(p))
    print(f"\n{len(rows)} proposal(s)")
    return 0


async def cmd_show(args) -> int:
    async with get_session_factory()() as session:
        p = await ProductDataProposalRepository(session).get(uuid.UUID(args.id))
    if p is None:
        print("Not found.")
        return 1
    fields = [
        ("id", p.id), ("status", p.status), ("store", p.store_id),
        ("entity", f"{p.entity_type}:{p.entity_key}"), ("field", p.field),
        ("proposed value", p.proposed_value), ("current value", p.current_value),
        ("source", p.source), ("source file", p.source_file),
        ("source sheet", p.source_sheet), ("source row", p.source_row),
        ("invoice", p.invoice_id), ("reason", p.reason),
        ("proposed by", p.proposed_by), ("created", p.created_at),
        ("reviewed by", p.reviewed_by), ("reviewed at", p.reviewed_at),
        ("review note", p.review_note),
    ]
    for label, value in fields:
        print(f"  {label:<16} {'' if value is None else value}")
    if p.evidence:
        print("  evidence")
        for k, v in p.evidence.items():
            print(f"      {k:<26} {v}")
    return 0


async def _decide(session, proposal, *, approve: bool, by: str, note: str | None) -> str:
    if approve:
        result = await proposal_service.approve(session, proposal, reviewed_by=by, note=note)
        was = "new" if result.previous_value is None else f"was {result.previous_value}"
        return f"APPROVED  {proposal.entity_key} {proposal.field} = {proposal.proposed_value}  ({was}) -> {result.applied_to}"
    await proposal_service.reject(session, proposal, reviewed_by=by, note=note)
    return f"REJECTED  {proposal.entity_key} {proposal.field} = {proposal.proposed_value}  (master data untouched)"


async def cmd_decide(args, approve: bool) -> int:
    async with get_session_factory()() as session:
        p = await ProductDataProposalRepository(session).get(uuid.UUID(args.id))
        if p is None:
            print("Not found.")
            return 1
        try:
            line = await _decide(session, p, approve=approve, by=args.by, note=args.note)
        except ProposalImmutableError as exc:
            print(f"REFUSED  {exc}")
            return 1
        await session.commit()
    print(line)
    return 0


async def cmd_decide_batch(args, approve: bool) -> int:
    if not (args.source or args.invoice or args.ids):
        print("Refusing to act on every pending proposal. Narrow with --source, --invoice or --ids.")
        return 2
    async with get_session_factory()() as session:
        repo = ProductDataProposalRepository(session)
        if args.ids:
            rows = [r for r in [await repo.get(uuid.UUID(i)) for i in args.ids] if r]
        else:
            rows = await repo.list(
                status=STATUS_PENDING, source=args.source,
                invoice_id=uuid.UUID(args.invoice) if args.invoice else None,
            )
        rows = [r for r in rows if r.status == STATUS_PENDING]
        if not rows:
            print("Nothing pending matches.")
            return 0
        verb = "approve" if approve else "reject"
        print(f"About to {verb} {len(rows)} proposal(s):")
        for p in rows:
            print("  " + _row(p))
        if not args.yes:
            answer = input(f"Type {verb.upper()} to continue: ").strip()
            if answer != verb.upper():
                print("Aborted. Nothing changed.")
                return 1
        lines = []
        try:
            for p in rows:
                lines.append(await _decide(session, p, approve=approve, by=args.by, note=args.note))
        except ProposalImmutableError as exc:
            await session.rollback()
            print(f"REFUSED, batch rolled back: {exc}")
            return 1
        await session.commit()
    for line in lines:
        print(line)
    print(f"\n{len(lines)} {verb}d in one transaction.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list")
    ls.add_argument("--status", choices=sorted(VALID_STATUSES), default=STATUS_PENDING)
    ls.add_argument("--all", action="store_true", help="every status")
    ls.add_argument("--source", choices=sorted(VALID_SOURCES))
    ls.add_argument("--invoice")
    ls.add_argument("--key", help="item code")

    sh = sub.add_parser("show")
    sh.add_argument("id")

    for name in ("approve", "reject"):
        d = sub.add_parser(name)
        d.add_argument("id")
        d.add_argument("--by", required=True)
        d.add_argument("--note")

    for name in ("approve-batch", "reject-batch"):
        b = sub.add_parser(name)
        b.add_argument("--source", choices=sorted(VALID_SOURCES))
        b.add_argument("--invoice")
        b.add_argument("--ids", nargs="*")
        b.add_argument("--by", required=True)
        b.add_argument("--note")
        b.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    args = parser.parse_args()
    if args.cmd == "list":
        if args.all:
            args.status = None
        return asyncio.run(cmd_list(args))
    if args.cmd == "show":
        return asyncio.run(cmd_show(args))
    if args.cmd in ("approve", "reject"):
        return asyncio.run(cmd_decide(args, approve=args.cmd == "approve"))
    return asyncio.run(cmd_decide_batch(args, approve=args.cmd == "approve-batch"))


if __name__ == "__main__":
    raise SystemExit(main())
