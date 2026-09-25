#!/usr/bin/env python
"""
Product Master IDENTITY-ONLY seed — local development database only.

    python scripts/seed_product_master_identity.py --dry-run
    python scripts/seed_product_master_identity.py
    python scripts/seed_product_master_identity.py --store 47708760 --verbose

Writes four tables and only those four:

    master_products
    master_product_identifiers
    master_product_descriptions
    master_pack_compositions

`master_commercial_mappings` is left empty on purpose. Commercial data is
store-scoped, needs a `commercial_unit_basis` that no existing row carries,
and is the subject of its own phase. Nothing here migrates
`product_case_mappings`, `StoreProductReference` or `product_data_proposals`,
and no existing application table is written to at all.

Safety, in order of what it refuses:

  * a database host that is not local — a Supabase or any other remote host
    stops the run before a session is opened, and there is deliberately no
    override flag;
  * a candidate whose identity did not resolve — unresolved stays
    unresolved and is reported, never promoted;
  * a partial write — everything happens in one transaction and any failure
    rolls the whole seed back.

Re-running is safe. Rows are matched on the same natural keys the table
constraints use, so a second run creates nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.models.product_master import (  # noqa: E402
    STATE_AUTO_MATCHED,
    STATE_CONFLICT,
    STATE_REVIEW_REQUIRED,
    STATE_UNRESOLVED,
    MasterPackComposition,
    MasterProduct,
    MasterProductDescription,
    MasterProductIdentifier,
)
from app.services.product_master.candidates import build_candidates  # noqa: E402
from scripts.analyze_product_master import load_raw_records  # noqa: E402
from scripts.preview_product_master_seed import to_master_rows  # noqa: E402

OUTPUT_DIR = ROOT / "analysis" / "master-data"
REPORT_JSON = OUTPUT_DIR / "product_master_identity_seed_report.json"
REPORT_CSV = OUTPUT_DIR / "product_master_identity_seed_report.csv"

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
# Substrings that mean a managed/remote database whatever the host spelling.
REMOTE_MARKERS = ("supabase", "amazonaws", "render.com", "neon.tech", "azure", "rds.")


def assert_local_database(url: str) -> str:
    """
    Refuse anything that is not a local development database.

    There is no override. A remote host is a stop, not a prompt — this
    command exists to populate a developer's own Postgres and nothing else.
    """
    host = (urlparse(url.replace("postgresql+asyncpg://", "postgresql://")
                     .replace("postgresql+psycopg2://", "postgresql://")).hostname or "")
    lowered = host.lower()
    if any(marker in lowered for marker in REMOTE_MARKERS):
        sys.exit(
            f"REFUSING TO SEED: database host {host!r} looks like a remote/managed "
            "environment. This command seeds local development databases only, and "
            "has no override."
        )
    if lowered not in LOCAL_HOSTS:
        sys.exit(
            f"REFUSING TO SEED: database host {host!r} is not local "
            f"({', '.join(sorted(LOCAL_HOSTS))}). This command seeds local development "
            "databases only, and has no override."
        )
    return host


def seedable(product) -> bool:
    """
    Whether a candidate carries enough identity evidence to become a row.

    Only what the resolver already decided: a product grounded in a
    canonical identifier. Unresolved and conflicting candidates stay in the
    preview and out of the master.
    """
    return product.identity_state == STATE_AUTO_MATCHED


async def run_seed(args) -> dict:
    from app.core.config import get_settings
    from app.database.session import get_session_factory

    settings = get_settings()
    from scripts.pilot_seed_guard import seed_target_host

    host = seed_target_host(settings.database_url, args)
    print(f"Database host: {host}  — local development, seeding permitted\n")

    records = load_raw_records()
    rows = to_master_rows(records, args.store)
    graph = build_candidates(rows)

    products = {k: p for k, p in graph.products.items() if seedable(p)}
    skipped_unresolved = sum(
        1 for p in graph.products.values() if p.identity_state == STATE_UNRESOLVED
    )
    skipped_conflict = sum(
        1 for p in graph.products.values() if p.identity_state == STATE_CONFLICT
    )

    report = {
        "dry_run": bool(args.dry_run),
        "database_host": host,
        "store_filter": args.store,
        "candidates_total": len(graph.products),
        "candidates_seedable": len(products),
        "skipped_unresolved_candidates": skipped_unresolved,
        "skipped_conflicts": skipped_conflict,
        "products_created": 0,
        "identifiers_created": 0,
        "descriptions_created": 0,
        "pack_compositions_created": 0,
        "commercial_mappings_created": 0,
        "duplicate_candidates_collapsed": 0,
        "already_present": {"products": 0, "identifiers": 0, "descriptions": 0,
                            "pack_compositions": 0},
        "source_provenance": dict(Counter(
            identifier["source_system"]
            for product in products.values() for identifier in product.identifiers
        )),
        "errors": [],
    }

    factory = get_session_factory()
    async with factory() as session:
        try:
            # One transaction: any failure below leaves the database exactly
            # as it was.
            existing_products = dict(
                (
                    await session.execute(
                        select(MasterProduct.canonical_key, MasterProduct.id)
                    )
                ).all()
            )
            report["already_present"]["products"] = len(
                set(existing_products) & set(products)
            )

            product_ids: dict[str, object] = dict(existing_products)
            for key in sorted(products):
                if key in product_ids:
                    continue
                candidate = products[key]
                row = MasterProduct(
                    canonical_key=key,
                    canonical_upc=candidate.canonical_upc,
                    identity_basis=candidate.identity_basis,
                    identity_state=candidate.identity_state,
                    # Left unset: no rule for choosing one is sanctioned.
                    canonical_description=None,
                )
                session.add(row)
                await session.flush()
                product_ids[key] = row.id
                report["products_created"] += 1

            report.update(await _seed_identifiers(session, products, product_ids, report))
            report.update(await _seed_descriptions(session, products, product_ids, report))
            report.update(
                await _seed_pack_compositions(session, graph, product_ids, report)
            )

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
    return report


async def _seed_identifiers(session, products, product_ids, report) -> dict:
    """Raw source values are stored as they were written, never replaced."""
    existing = {
        (str(pid), itype, raw, system)
        for pid, itype, raw, system in (
            await session.execute(select(
                MasterProductIdentifier.product_id,
                MasterProductIdentifier.identifier_type,
                MasterProductIdentifier.raw_value,
                MasterProductIdentifier.source_system,
            ))
        ).all()
    }
    created = present = collapsed = 0
    seen: set[tuple] = set()
    for key in sorted(products):
        product_id = product_ids[key]
        for identifier in products[key].identifiers:
            natural = (str(product_id), identifier["identifier_type"],
                       identifier["raw_value"], identifier["source_system"])
            if natural in existing:
                present += 1
                continue
            if natural in seen:
                # The same source system asserting the same raw value twice
                # is one fact, not two.
                collapsed += 1
                continue
            seen.add(natural)
            session.add(MasterProductIdentifier(
                product_id=product_id,
                raw_value=identifier["raw_value"][:128],
                normalized_value=identifier["normalized_value"],
                identifier_type=identifier["identifier_type"],
                derivation=identifier["derivation"],
                derivation_detail=identifier["derivation_detail"],
                evidence_state=identifier["evidence_state"],
                source_system=identifier["source_system"],
                source_distributor=identifier["source_distributor"],
                source_file=identifier["source_file"],
                source_sheet=identifier["source_sheet"],
                source_row=identifier["source_row"],
            ))
            created += 1
    await session.flush()
    report["already_present"]["identifiers"] = present
    return {"identifiers_created": created,
            "duplicate_candidates_collapsed": report["duplicate_candidates_collapsed"] + collapsed}


async def _seed_descriptions(session, products, product_ids, report) -> dict:
    """Every source description is kept; none is promoted to canonical."""
    existing = {
        (str(pid), normalized, role, system)
        for pid, normalized, role, system in (
            await session.execute(select(
                MasterProductDescription.product_id,
                MasterProductDescription.normalized_description,
                MasterProductDescription.role,
                MasterProductDescription.source_system,
            ))
        ).all()
    }
    created = present = 0
    seen: set[tuple] = set()
    for key in sorted(products):
        product_id = product_ids[key]
        for description in products[key].descriptions:
            natural = (str(product_id), description["normalized_description"],
                       description["role"], description["source_system"])
            if natural in existing or natural in seen:
                present += natural in existing
                continue
            seen.add(natural)
            session.add(MasterProductDescription(
                product_id=product_id,
                description=description["description"][:255],
                normalized_description=description["normalized_description"][:255],
                role=description["role"],
                evidence_state=description["evidence_state"],
                source_system=description["source_system"],
                source_file=description["source_file"],
                source_sheet=description["source_sheet"],
                source_row=description["source_row"],
                observed_count=description["observed_count"],
            ))
            created += 1
    await session.flush()
    report["already_present"]["descriptions"] = present
    return {"descriptions_created": created}


async def _seed_pack_compositions(session, graph, product_ids, report) -> dict:
    """
    Only compositions the preview already evidenced: a parent barcode, a
    child barcode and an explicit package notation. Nothing is parsed from a
    description here, and units-per-case is never used as a substitute.
    """
    existing = {
        (str(parent), str(child) if child else None, quantity, system)
        for parent, child, quantity, system in (
            await session.execute(select(
                MasterPackComposition.parent_product_id,
                MasterPackComposition.child_product_id,
                MasterPackComposition.child_quantity,
                MasterPackComposition.source_system,
            ))
        ).all()
    }
    created = present = skipped = 0
    seen: set[tuple] = set()
    for composition in sorted(
        graph.pack_compositions,
        key=lambda c: (c["parent_canonical_key"], c["child_canonical_key"],
                       c["source_file"], c["source_row"]),
    ):
        parent_id = product_ids.get(composition["parent_canonical_key"])
        child_id = product_ids.get(composition["child_canonical_key"])
        if parent_id is None or child_id is None:
            # A composition whose ends are not both seedable products is not
            # recorded against a half-known relationship.
            skipped += 1
            continue
        natural = (str(parent_id), str(child_id), composition["child_quantity"],
                   composition["source_system"])
        if natural in existing:
            present += 1
            continue
        if natural in seen:
            continue
        seen.add(natural)
        session.add(MasterPackComposition(
            parent_product_id=parent_id,
            child_product_id=child_id,
            child_quantity=composition["child_quantity"],
            composition_basis=composition["composition_basis"],
            evidence_state=STATE_REVIEW_REQUIRED,
            evidence=composition["evidence"],
            source_system=composition["source_system"],
            source_file=composition["source_file"],
            source_sheet=composition["source_sheet"],
            source_row=composition["source_row"],
        ))
        created += 1
    await session.flush()
    report["already_present"]["pack_compositions"] = present
    report["pack_compositions_skipped_unseedable_end"] = skipped
    return {"pack_compositions_created": created}


def write_reports(report: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    flat = {k: v for k, v in report.items() if not isinstance(v, (dict, list))}
    for key, value in report.get("already_present", {}).items():
        flat[f"already_present_{key}"] = value
    for key, value in report.get("source_provenance", {}).items():
        flat[f"provenance_{key}"] = value
    flat["errors"] = " || ".join(report.get("errors", []))
    with REPORT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(flat))
        writer.writeheader()
        writer.writerow(flat)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed identity-level Product Master data. Local database only.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Do everything, then roll back. Reports what would be created.")
    parser.add_argument("--store", default=None, help="Limit to one source store code.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(run_seed(args))
    write_reports(report)

    print(f"  candidates total ................ {report['candidates_total']}")
    print(f"  candidates seedable ............. {report['candidates_seedable']}")
    print(f"  skipped unresolved .............. {report['skipped_unresolved_candidates']}")
    print(f"  skipped conflicts ............... {report['skipped_conflicts']}")
    print(f"  products created ................ {report['products_created']}")
    print(f"  identifiers created ............. {report['identifiers_created']}")
    print(f"  descriptions created ............ {report['descriptions_created']}")
    print(f"  pack compositions created ....... {report['pack_compositions_created']}")
    print(f"  commercial mappings created ..... {report['commercial_mappings_created']}"
          "   (must be 0)")
    print(f"  already present ................. {report['already_present']}")
    if args.verbose:
        print(f"  source provenance ............... {report['source_provenance']}")
    if report["errors"]:
        print(f"  ERRORS .......................... {report['errors']}")
    print(f"\nWrote {REPORT_JSON.relative_to(ROOT)} and {REPORT_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
