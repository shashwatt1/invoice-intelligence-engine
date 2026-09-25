"""
One-time pilot seed — scripts/pilot_seed.py

Populates the Product Master tables on the pilot database, once.

    # rehearsal — verifies, runs every stage, rolls each one back
    python scripts/pilot_seed.py --pilot-seed \
        --expect-database postgres --expect-host-contains supabase --dry-run

    # the real thing
    python scripts/pilot_seed.py --pilot-seed \
        --expect-database postgres --expect-host-contains supabase

The local-only guard on each individual seed command is untouched and still
has no override. This command is the one verified way past it, and it earns
that by proving, before it writes anything, that it is connected to the
database the operator named, at the Alembic revision this seed was written
for, with the Product Master schema present (see pilot_seed_guard).

What it writes: master_products, master_product_identifiers,
master_product_descriptions, master_pack_compositions and
master_commercial_mappings — the last in `REVIEW_REQUIRED`, carrying the
raw source evidence behind every candidate.

What it does not write, and has no code path to write: product_case_mappings
or any other legacy table, invoices, invoice_items, documents, processing
logs, stores, store_identifiers. It approves nothing, rejects nothing,
proposes nothing and creates no review rows — every seeded candidate arrives
awaiting a human. Re-running it adds nothing it has already added.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pilot_seed_guard import (  # noqa: E402
    TARGET_PILOT,
    add_pilot_arguments,
    resolve_seed_target,
)

REPORT = ROOT / "analysis" / "master-data" / "pilot_seed_report.json"

# Order matters: identity creates the products the later stages attach to.
STAGES = (
    ("identity", "scripts.seed_product_master_identity", "run_seed"),
    ("commercial", "scripts.seed_product_master_commercial", "run_seed"),
    ("descriptions", "scripts.seed_product_master_descriptions", "run"),
    ("source_snapshot", "scripts.backfill_commercial_source_snapshot", "run"),
)


async def run_stages(target, dry_run: bool, store: str | None) -> dict:
    from importlib import import_module

    results: dict = {}
    for name, module_path, function_name in STAGES:
        print(f"\n=== {name} " + "=" * (60 - len(name)))
        args = SimpleNamespace(
            dry_run=dry_run, store=store, verbose=False, pilot_target=target)
        outcome = await getattr(import_module(module_path), function_name)(args)
        # A stage that rolled back must not be followed by stages that
        # assume its rows exist.
        if isinstance(outcome, tuple):
            outcome = outcome[0]
        results[name] = outcome
        if outcome.get("error"):
            print(f"\nSTOPPING: {name} failed — {outcome['error']}")
            break
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time Product Master seed for the verified pilot database.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run every stage and roll each one back.")
    parser.add_argument("--store", default=None, help="Limit to one source store code.")
    add_pilot_arguments(parser)
    args = parser.parse_args()

    from app.core.config import get_settings

    settings = get_settings()
    target = resolve_seed_target(settings.database_url, settings.database_url_sync, args)
    if target.target != TARGET_PILOT:
        sys.exit(
            "This command seeds the pilot. Pass --pilot-seed with the expected "
            "database identity, or run the individual seed commands for local "
            "development."
        )

    print(f"Verified pilot target: database {target.database!r} on a host matching "
          f"{args.expect_host_contains!r}, Alembic revision {target.revision}.")
    print("Mode: DRY RUN (every stage rolls back)\n" if args.dry_run else "Mode: WRITE\n")

    results = asyncio.run(run_stages(target, args.dry_run, args.store))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(
        {"dry_run": bool(args.dry_run), "database": target.database,
         "revision": target.revision, "stages": results}, indent=2, default=str))
    print(f"\nReport: {REPORT.relative_to(ROOT)}")
    if any(r.get("error") for r in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
