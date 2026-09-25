#!/usr/bin/env python
"""
Shadow EDI comparison over the golden invoice corpus — READ-ONLY.

    python scripts/shadow_edi_compare.py

Builds each invoice's EDI twice — once from the legacy case mappings that
are authoritative today, once from APPROVED Product Master commercial
mappings — and reports every difference. Nothing is persisted, no invoice
is modified, and the existing exporter is reused unchanged.
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.models.invoice import Invoice  # noqa: E402
from app.models.product_master import (  # noqa: E402
    STATE_APPROVED,
    MasterCommercialMapping,
)
from app.services.case_mapping_service import invoice_units_by_item_code  # noqa: E402
from app.services.product_master.shadow_edi import compare_invoice  # noqa: E402

OUTPUT_CSV = ROOT / "analysis" / "master-data" / "product_master_shadow_edi_comparison.csv"
OUTPUT_JSON = ROOT / "analysis" / "master-data" / "product_master_shadow_edi_summary.json"

# The regression corpus named for this batch.
GOLDEN = ["3376587", "1000540", "101497", "1012818", "2035546957", "2310090549", "000007174"]


async def main() -> None:
    from app.database.session import get_session_factory

    async with get_session_factory()() as session:
        approved = dict(
            (
                await session.execute(
                    select(
                        MasterCommercialMapping.pdi_item_code,
                        MasterCommercialMapping.units_accounted_for,
                    ).where(
                        MasterCommercialMapping.approval_state == STATE_APPROVED,
                        MasterCommercialMapping.units_accounted_for.isnot(None),
                    )
                )
            ).all()
        )

        invoices = (await session.execute(
            select(Invoice).options(selectinload(Invoice.items))
        )).scalars().all()

        rows: list[dict] = []
        for invoice in invoices:
            if invoice.invoice_number not in GOLDEN:
                continue
            legacy_units = await invoice_units_by_item_code(session, invoice)
            comparison = compare_invoice(invoice, legacy_units, approved)
            record = asdict(comparison)
            record["blockers"] = " || ".join(comparison.blockers)
            record["differences"] = " || ".join(
                f"{d['field']}:{d['kind']}" for d in comparison.differences
            )
            record["difference_codes"] = " || ".join(comparison.difference_codes)
            rows.append(record)

    classifications = Counter(r["classification"] for r in rows)
    # Phase 3E: only explicit human approvals may feed the shadow. With none
    # recorded, nothing is fabricated — the result is BLOCKED, honestly.
    explicit_approvals = len(approved)
    summary = {
        "read_only": True,
        "edi_authority": "product_case_mappings — unchanged",
        "explicit_human_approvals_found": explicit_approvals,
        "approved_master_mappings_available": len(approved),
        "invoices_compared": len(rows),
        "classifications": dict(classifications),
        "financial_invariant_everywhere": all(r["financial_invariant"] for r in rows),
        "note": (
            "The shadow uses only APPROVED Product Master mappings. With none "
            "approved, every invoice is SHADOW_BLOCKED, which is the honest "
            "state rather than a fallback to legacy values."
        ),
    }

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    OUTPUT_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print("Shadow EDI comparison (read-only)\n")
    print(f"  approved master mappings ........ {len(approved)}")
    print(f"  golden invoices compared ........ {len(rows)}")
    for name, count in classifications.most_common():
        print(f"     {name:<28} {count}")
    print(f"  financial invariant everywhere .. {summary['financial_invariant_everywhere']}")
    for row in rows:
        print(f"     {str(row['invoice_number']):<12} {row['classification']:<18} "
              f"B={row['legacy_b_record_count']:<3} {row['blockers'][:60]}")
    print(f"\nWrote {OUTPUT_CSV.relative_to(ROOT)} and {OUTPUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
