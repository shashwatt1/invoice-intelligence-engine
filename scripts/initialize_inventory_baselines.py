#!/usr/bin/env python
"""
Store inventory baseline initialization (admin) — scripts/initialize_inventory_baselines.py

    # Dry run (the default): reports what would happen, writes nothing.
    python scripts/initialize_inventory_baselines.py --actor ADMIN --initialization-date 2026-11-02 \\
        --reason "Inventory go-live: start every store at zero"

    # Apply, then the same command again to confirm every store is skipped.
    python scripts/initialize_inventory_baselines.py ... --apply

    # Only some stores.
    python scripts/initialize_inventory_baselines.py ... --store UUID --store UUID

Gives every physical store a zero start (app.services.inventory_baseline):
the arrival date of its earliest POSTED receipt, or the initialization date
when it has none. Stores that already have a baseline and source identities
are skipped; existing product-level opening counts and every movement are
left untouched. Each store is its own transaction; the report lists every
store as INITIALIZED (WOULD INITIALIZE in a dry run), SKIPPED or FAILED with
the reason, then the totals. Exit status 1 when any store failed.

--actor must be an existing, active ADMIN account; it is recorded on every
baseline with --reason. --initialization-date is always explicit: there is no
default, so the business date is a decision, not the server's clock.

The database is the one DATABASE_URL points at. Production is initialized
only by following docs/inventory-baseline-initialization.md, with approval.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.exceptions import InvoiceBaseException  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.models.user import normalize_username  # noqa: E402
from app.services.inventory_baseline import FAILED, INITIALIZED, SKIPPED, run_initialization  # noqa: E402


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' is not a date (YYYY-MM-DD)") from exc


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' is not a store id (UUID)") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize store inventory baselines (dry run by default).")
    parser.add_argument("--actor", required=True, help="An active ADMIN username; recorded on every baseline.")
    parser.add_argument("--reason", required=True, help="Why; kept with every baseline.")
    parser.add_argument("--initialization-date", required=True, type=_date,
                        help="The business date of this run (YYYY-MM-DD). Stores without receipts start on it.")
    parser.add_argument("--store", action="append", type=_uuid, default=[],
                        help="Only this store (repeatable). Default: every store.")
    parser.add_argument("--apply", action="store_true", help="Write the baselines. Without it nothing is written.")
    return parser.parse_args(argv)


def report(results, *, applied: bool) -> str:
    label = {INITIALIZED: "INITIALIZED" if applied else "WOULD INITIALIZE", SKIPPED: "SKIPPED", FAILED: "FAILED"}
    lines = [f"{'DRY RUN — nothing was written' if not applied else 'APPLIED'}", ""]
    for r in results:
        lines.append(f"{label[r.outcome]:<17} {r.store_label} [{r.store_id}] — {r.detail}")
    counts = {k: sum(1 for r in results if r.outcome == k) for k in (INITIALIZED, SKIPPED, FAILED)}
    lines += ["", f"{'Initialized' if applied else 'Would initialize'}: {counts[INITIALIZED]}  "
                  f"Skipped: {counts[SKIPPED]}  Failed: {counts[FAILED]}  Total: {len(results)}"]
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        results = await run_initialization(
            get_session_factory(), actor_username=normalize_username(args.actor), reason=args.reason,
            initialization_date=args.initialization_date, store_ids=args.store or None, apply=args.apply)
    except InvoiceBaseException as exc:
        print(exc.message, file=sys.stderr)
        return 2
    print(report(results, applied=args.apply))
    return 1 if any(r.outcome == FAILED for r in results) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
