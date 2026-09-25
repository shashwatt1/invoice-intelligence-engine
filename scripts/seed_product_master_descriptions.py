#!/usr/bin/env python
"""
Canonical description seed — local development database only.

    python scripts/seed_product_master_descriptions.py --dry-run
    python scripts/seed_product_master_descriptions.py --verbose

Applies docs/product-master-description-policy.md: a description becomes
CANONICAL only when a distributor product sheet states exactly one for the
product and it fits the 25-character PDI field. Everything else keeps no
canonical description — the corpus contains no product-master description
source at meaningful coverage, and inventing one would be the very
frequency/length/recency guess the policy rejects.

Source and invoice aliases are never modified or removed; a canonical row
is added alongside them. Idempotent, transactional, and refused against any
non-local database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.models.product_master import (  # noqa: E402
    DESC_CANONICAL,
    STATE_AUTO_MATCHED,
    MasterProduct,
    MasterProductDescription,
)
from app.services.product_master.descriptions import (  # noqa: E402
    OUTCOME_CANONICAL,
    decide_canonical_description,
)
from scripts.pilot_seed_guard import seed_target_host  # noqa: E402

REPORT = ROOT / "analysis" / "master-data" / "product_master_description_seed_report.json"
SYSTEM_ATTRIBUTION = "system:description-policy-v1"


async def run(args) -> dict:
    from app.core.config import get_settings
    from app.database.session import get_session_factory

    host = seed_target_host(get_settings().database_url, args)
    print(f"Database host: {host}  — local development, seeding permitted\n")

    report = {
        "dry_run": bool(args.dry_run),
        "database_host": host,
        "policy": "docs/product-master-description-policy.md",
        "products_examined": 0,
        "canonical_set": 0,
        "already_canonical": 0,
        "unresolved": 0,
        "outcomes": {},
        "source_distribution": {},
        "examples": [],
        "errors": [],
    }

    async with get_session_factory()() as session:
        try:
            products = (await session.execute(select(MasterProduct))).scalars().all()
            rows = (await session.execute(select(MasterProductDescription))).scalars().all()

            by_product: dict = {}
            existing_canonical: set = set()
            for row in rows:
                by_product.setdefault(row.product_id, []).append({
                    "description": row.description, "source_sheet": row.source_sheet,
                    "source_file": row.source_file, "source_row": row.source_row,
                    "role": row.role, "source_system": row.source_system,
                })
                if row.role == DESC_CANONICAL:
                    existing_canonical.add(row.product_id)

            outcomes: Counter = Counter()
            sources: Counter = Counter()
            report["products_examined"] = len(products)

            for product in products:
                observed = [d for d in by_product.get(product.id, [])
                            if d["role"] != DESC_CANONICAL]
                decision = decide_canonical_description(observed)
                outcomes[decision.outcome] += 1

                if decision.outcome != OUTCOME_CANONICAL:
                    report["unresolved"] += 1
                    continue
                if product.id in existing_canonical:
                    report["already_canonical"] += 1
                    continue

                sources[decision.source_sheet or "?"] += 1
                session.add(MasterProductDescription(
                    product_id=product.id,
                    description=decision.description,
                    normalized_description=decision.description.upper(),
                    role=DESC_CANONICAL,
                    evidence_state=STATE_AUTO_MATCHED,
                    # Attribution: derived by policy, not entered by a person.
                    source_system=SYSTEM_ATTRIBUTION,
                    source_file=decision.source_file,
                    source_sheet=decision.source_sheet,
                    source_row=decision.source_row,
                    observed_count=1,
                ))
                # The canonical description is mirrored onto the product for
                # fast reads; the alias rows remain untouched.
                product.canonical_description = decision.description
                report["canonical_set"] += 1
                if len(report["examples"]) < 8:
                    report["examples"].append({
                        "canonical_upc": product.canonical_upc,
                        "description": decision.description,
                        "length": len(decision.description),
                        "source_sheet": decision.source_sheet,
                        "reason": decision.reason,
                    })

            report["outcomes"] = dict(outcomes)
            report["source_distribution"] = dict(sources)
            await session.flush()
            if args.dry_run:
                await session.rollback()
                print("DRY RUN — rolled back, nothing written.\n")
            else:
                await session.commit()
                print("Committed.\n")
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            report["errors"].append(f"{type(exc).__name__}: {exc}")
            print(f"FAILED — rolled back: {exc}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the canonical description policy.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(run(args))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(f"  products examined ............... {report['products_examined']}")
    print(f"  canonical descriptions set ...... {report['canonical_set']}")
    print(f"  already canonical ............... {report['already_canonical']}")
    print(f"  left without one ................ {report['unresolved']}")
    print(f"  outcomes ........................ {report['outcomes']}")
    if args.verbose:
        for example in report["examples"]:
            print(f"     {example['canonical_upc']} [{example['length']:>2}ch] "
                  f"{example['description']!r}")
    print(f"\nWrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
