"""
One-time pilot seed — scripts/pilot_seed.py

Populates the Product Master tables on the pilot database, once.

    # plan — verifies, reads, computes every row it would write; writes none
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

`--dry-run` does not run the stages at all. It runs their planning logic in
memory, each stage fed the previous stage's planned rows, against what the
database already holds, read in a READ ONLY transaction (see pilot_seed_plan).
It issues no INSERT, UPDATE or DELETE, and reports every row that would make
the real seed fail. The target verification before it is identical.
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
        failure = stage_failure(outcome)
        if failure:
            not_run = [n for n, _, _ in STAGES][len(results):]
            print(f"\nSTOPPING: stage '{name}' failed — {failure}")
            print(f"Not run: {', '.join(not_run) or 'none'}")
            break
    return results


def stage_failure(outcome: dict) -> str | None:
    """
    The failure a stage reported, or None.

    Stages report failure in one of two shapes: `error` (a string) or
    `errors` (a list). Either, when non-empty, is a failed stage.
    """
    if outcome.get("error"):
        return str(outcome["error"])
    errors = outcome.get("errors")
    if errors:
        return " | ".join(str(error) for error in errors)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time Product Master seed for the verified pilot database.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan the whole seed in memory and report it. Writes nothing.")
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

    if args.dry_run:
        print("Mode: DRY RUN (plan only — no INSERT, UPDATE or DELETE is issued)\n")
        from scripts.pilot_seed_plan import plan_pipeline

        plan = asyncio.run(plan_pipeline(target, args.store))
        write_report({"dry_run": True, "database": target.database,
                      "revision": target.revision, "plan": plan, "stages": plan["stages"]})
        print_plan(plan)
        if not plan["ready_for_live_seed"]:
            sys.exit(1)
        return

    print("Mode: WRITE\n")

    results = asyncio.run(run_stages(target, args.dry_run, args.store))

    failed = next((name for name, outcome in results.items() if stage_failure(outcome)), None)
    write_report({"dry_run": False, "database": target.database,
                  "revision": target.revision, "stages": results,
                  "failed_stage": failed,
                  "not_run": [n for n, _, _ in STAGES if n not in results]})
    if failed:
        print(f"\nSEED FAILED at stage '{failed}'. Stages after it were not run.")
        sys.exit(1)
    print("\nSeed completed: every stage committed.")


def write_report(payload: dict) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nReport: {REPORT.relative_to(ROOT)}")


def print_plan(plan: dict) -> None:
    def section(title: str, values: dict) -> None:
        print(f"\n{title}")
        for key, value in values.items():
            print(f"  {key:<44} {value}")

    section("Source", plan["source"])
    section("Already in the database", plan["existing_rows"])
    section("Would create", plan["would_create"])
    section("Descriptions by role", plan["would_create_descriptions_by_role"])
    section("Would update (Product Master only)", plan["would_update"])
    section("Expected totals after the seed", plan["expected_totals_after"])
    section("Skipped", plan["skipped"])
    section("Commercial mappings", plan["commercial"])
    section("Source snapshot", plan["source_snapshot"])
    validation = plan["validation"]
    print("\nWould fail the real seed")
    for line in validation["would_fail"] or ["none"]:
        print(f"  {line}")
    print("\nWarnings")
    for line in validation["warnings"] or ["none"]:
        print(f"  {line}")
    print("\nBLOCKING" if validation["blocking"] else "\nBlocking issues: none")
    for line in validation["blocking"]:
        print(f"  - {line}")
    print(f"\nReady for the live seed: {'YES' if plan['ready_for_live_seed'] else 'NO'}")


if __name__ == "__main__":
    main()
