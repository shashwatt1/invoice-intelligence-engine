#!/usr/bin/env python
"""
Product Master COMMERCIAL CANDIDATE seed — local development database only.

    python scripts/seed_product_master_commercial.py --dry-run
    python scripts/seed_product_master_commercial.py --verbose

Writes `master_commercial_mappings` and nothing else. Every row is created
as a **candidate awaiting review**, never as an approved or authoritative
mapping: `product_case_mappings` remains the only thing EDI reads, and this
phase does not touch it, the export service, extraction, proposals or any
legacy master table.

What the multiplier means, and why it is often absent: PDI computes
Case Retail = Item Retail x units_accounted_for. The number is a commercial
multiplier, not a count of package contents, so it is only recorded when a
distributor actually stated sellable units per case and nothing contradicts
it. Physical pack composition, package notation, description text and row
frequency are all inadmissible — see
app/services/product_master/commercial.py.

Safety is the same as the identity seed: a non-local database host stops
the run before a session opens and there is no override flag, and the whole
seed runs in one transaction that rolls back on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.models.product_case_mapping import ProductCaseMapping  # noqa: E402
from app.models.product_master import (  # noqa: E402
    COMMERCIAL_CONFLICT,
    COMMERCIAL_UNKNOWN,
    STATE_REVIEW_REQUIRED,
    MasterCommercialMapping,
    MasterProduct,
)
from app.services.product_master.candidates import build_candidates  # noqa: E402
from app.services.product_master.commercial import (  # noqa: E402
    decide_case_cost,
    decide_commercial_unit,
)
from app.services.product_master.stores import (  # noqa: E402
    resolve_commercial_candidates,
)
from scripts.analyze_product_master import load_raw_records  # noqa: E402
from scripts.pilot_seed_guard import seed_target_host  # noqa: E402
from scripts.preview_product_master_seed import _load_store_index, to_master_rows  # noqa: E402

# Re-exported so this command's guard is verifiably the identity seed's guard.
from scripts.seed_product_master_identity import assert_local_database  # noqa: E402,F401

OUTPUT_DIR = ROOT / "analysis" / "master-data"
REVIEW_CSV = OUTPUT_DIR / "product_master_commercial_review.csv"
REPORT_JSON = OUTPUT_DIR / "product_master_commercial_seed_report.json"

REVIEW_COLUMNS = [
    "master_product_id", "store_id", "source_store_identifier", "canonical_identifier",
    "pdi_item_code", "unit_identifier", "units_accounted_for", "commercial_unit_basis",
    "case_cost", "cost_basis", "approval_state", "evidence_state",
    "physical_store_identity_status", "source_file", "source_sheet", "source_row",
    "supporting_source_rows", "notes",
]


async def run_seed(args) -> tuple[dict, list[dict]]:
    from app.core.config import get_settings
    from app.database.session import get_session_factory

    settings = get_settings()
    host = seed_target_host(settings.database_url, args)
    print(f"Database host: {host}  — local development, seeding permitted\n")

    records = load_raw_records()
    rows = to_master_rows(records, args.store)
    graph = build_candidates(rows)

    store_index, store_error = _load_store_index()
    store_tally = resolve_commercial_candidates(graph.commercial_candidates, store_index)

    # One mapping per (product, store). Cost and description never key it;
    # several source rows supporting one mapping contribute evidence to it.
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    unattached = 0
    for candidate in graph.commercial_candidates:
        if not candidate.get("source_store_resolved") or not candidate.get("store_id"):
            unattached += 1
            continue
        grouped[(candidate["canonical_key"], candidate["store_id"])].append(candidate)

    report = {
        "dry_run": bool(args.dry_run),
        "database_host": host,
        "candidates_examined": len(graph.commercial_candidates),
        "store_resolution": store_tally,
        "store_lookup_error": store_error,
        "distinct_product_store_pairs": len(grouped),
        "skipped_store_unresolved": unattached,
        "skipped_no_master_product": 0,
        "mappings_created": 0,
        "already_present": 0,
        "review_required": 0,
        "approved": 0,
        "basis_counts": {},
        "with_resolved_units": 0,
        "without_resolved_units": 0,
        "cost_recorded": 0,
        "cost_unresolved": 0,
        "cost_basis_counts": {},
        "legacy_case_mappings_read": 0,
        "errors": [],
    }
    review: list[dict] = []

    factory = get_session_factory()
    async with factory() as session:
        try:
            products = {
                key: (pid, upc)
                for key, pid, upc in (
                    await session.execute(select(
                        MasterProduct.canonical_key, MasterProduct.id,
                        MasterProduct.canonical_upc,
                    ))
                ).all()
            }

            # Governed values are read to DISSENT only — never to supply a
            # multiplier. product_case_mappings is not written to here.
            governed: dict[str, set[int]] = defaultdict(set)
            for item_code, units in (
                await session.execute(select(
                    ProductCaseMapping.item_code, ProductCaseMapping.units_per_case,
                ))
            ).all():
                governed[item_code].add(units)
            report["legacy_case_mappings_read"] = sum(len(v) for v in governed.values())

            existing = {
                (str(pid), str(sid))
                for pid, sid in (
                    await session.execute(select(
                        MasterCommercialMapping.product_id,
                        MasterCommercialMapping.store_id,
                    ))
                ).all()
            }

            basis_counter: Counter = Counter()
            cost_counter: Counter = Counter()

            for (canonical_key, store_id) in sorted(grouped):
                statements = grouped[(canonical_key, store_id)]
                product = products.get(canonical_key)
                if product is None:
                    # Identity never resolved, so there is no product to carry
                    # a commercial fact. Reported, not invented.
                    report["skipped_no_master_product"] += 1
                    continue
                product_id, canonical_upc = product
                pdi_item_code = canonical_upc[:-1] if canonical_upc else None

                decision = decide_commercial_unit(
                    statements,
                    governed_units=governed.get(pdi_item_code) if pdi_item_code else None,
                )
                case_cost, cost_basis = decide_case_cost(statements)

                basis_counter[decision.commercial_unit_basis] += 1
                cost_counter[cost_basis] += 1
                if decision.units_accounted_for is None:
                    report["without_resolved_units"] += 1
                else:
                    report["with_resolved_units"] += 1
                if case_cost is None:
                    report["cost_unresolved"] += 1
                else:
                    report["cost_recorded"] += 1

                first = statements[0]
                review.append({
                    "master_product_id": str(product_id),
                    "store_id": store_id,
                    "source_store_identifier": first.get("store_context"),
                    "canonical_identifier": canonical_upc,
                    "pdi_item_code": pdi_item_code,
                    "unit_identifier": None,
                    "units_accounted_for": decision.units_accounted_for,
                    "commercial_unit_basis": decision.commercial_unit_basis,
                    "case_cost": str(case_cost) if case_cost is not None else None,
                    "cost_basis": cost_basis,
                    "approval_state": decision.approval_state,
                    "evidence_state": STATE_REVIEW_REQUIRED,
                    "physical_store_identity_status": first.get("store_identity_status"),
                    "source_file": first.get("source_file"),
                    "source_sheet": first.get("source_sheet"),
                    "source_row": first.get("source_row"),
                    "supporting_source_rows": " || ".join(
                        f"{s.get('source_sheet')}:{s.get('source_row')}" for s in statements
                    ),
                    "notes": decision.notes,
                })

                if (str(product_id), str(store_id)) in existing:
                    report["already_present"] += 1
                    continue

                session.add(MasterCommercialMapping(
                    product_id=product_id,
                    store_id=store_id,
                    pdi_item_code=pdi_item_code or "",
                    commercial_unit_basis=decision.commercial_unit_basis,
                    units_accounted_for=decision.units_accounted_for,
                    case_cost=case_cost,
                    # Cost status is reviewed independently of the commercial
                    # unit, so it is a column, not an evidence key.
                    cost_basis=cost_basis,
                    approval_state=decision.approval_state,
                    evidence={
                        **decision.evidence,
                        "notes": decision.notes,
                        "cost_basis": cost_basis,
                        "source_store_identifier": first.get("store_context"),
                        "physical_store_identity_status": first.get("store_identity_status"),
                        "supporting_rows": [
                            {"source_file": s.get("source_file"),
                             "source_sheet": s.get("source_sheet"),
                             "source_row": s.get("source_row"),
                             "raw_value": (s.get("evidence") or {}).get("raw_value")
                             if isinstance(s.get("evidence"), dict) else None}
                            for s in statements
                        ],
                    },
                    source_system=first.get("source_system", "distributor_price_sheet"),
                    source_file=first.get("source_file"),
                    source_sheet=first.get("source_sheet"),
                    source_row=first.get("source_row"),
                ))
                report["mappings_created"] += 1

            report["basis_counts"] = dict(basis_counter)
            report["cost_basis_counts"] = dict(cost_counter)
            report["review_required"] = sum(
                1 for r in review if r["approval_state"] == STATE_REVIEW_REQUIRED
            )
            report["approved"] = sum(
                1 for r in review if r["approval_state"] == "APPROVED"
            )
            report["unknown_basis"] = basis_counter.get(COMMERCIAL_UNKNOWN, 0)
            report["conflict_basis"] = basis_counter.get(COMMERCIAL_CONFLICT, 0)

            await session.flush()
            if args.dry_run:
                await session.rollback()
                print("DRY RUN — transaction rolled back, nothing was written.\n")
            else:
                await session.commit()
                print("Committed.\n")
        except Exception as exc:  # noqa: BLE001 — report and leave the DB untouched
            await session.rollback()
            report["errors"].append(f"{type(exc).__name__}: {exc}")
            print(f"FAILED — rolled back, database unchanged: {type(exc).__name__}: {exc}")
    return report, review


def write_outputs(report: dict, review: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    with REVIEW_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(review, key=lambda r: (r["commercial_unit_basis"],
                                                       str(r["canonical_identifier"]))))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed commercial CANDIDATES into the Product Master. Local DB only.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--store", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    report, review = asyncio.run(run_seed(args))
    write_outputs(report, review)

    print(f"  candidates examined ............. {report['candidates_examined']}")
    print(f"  distinct (product, store) pairs .. {report['distinct_product_store_pairs']}")
    print(f"  skipped — no master product ..... {report['skipped_no_master_product']}")
    print(f"  skipped — store unresolved ...... {report['skipped_store_unresolved']}")
    print(f"  mappings created ................ {report['mappings_created']}")
    print(f"  already present ................. {report['already_present']}")
    print(f"  REVIEW_REQUIRED ................. {report['review_required']}")
    print(f"  APPROVED ........................ {report['approved']}   (must be 0)")
    print(f"  with resolved multiplier ........ {report['with_resolved_units']}")
    print(f"  without resolved multiplier ..... {report['without_resolved_units']}")
    print(f"  commercial_unit_basis ........... {report['basis_counts']}")
    print(f"  cost recorded / unresolved ...... {report['cost_recorded']} / "
          f"{report['cost_unresolved']}")
    if args.verbose:
        print(f"  cost basis ...................... {report['cost_basis_counts']}")
        print(f"  legacy mappings read (dissent) .. {report['legacy_case_mappings_read']}")
    if report["errors"]:
        print(f"  ERRORS .......................... {report['errors']}")
    print(f"\nWrote {REVIEW_CSV.relative_to(ROOT)}")
    print(f"Wrote {REPORT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
