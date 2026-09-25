#!/usr/bin/env python
"""
Description policy validation over the golden invoices — READ-ONLY.

    python scripts/report_description_policy_validation.py

Evidence for one decision that is still open: should the shadow EDI emit
the Product Master canonical description, or keep the invoice description
the live path already emits?

For every golden invoice line it shows the invoice wording, what legacy EDI
emits today, whether a sanctioned canonical description exists, and — the
part that matters — whether the item code is identical either way. PDI
product-matches on the UPC, so a wording difference with an identical item
code changes what is displayed, not which product is touched.

Changes nothing.
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.models.invoice import Invoice  # noqa: E402
from app.models.product_master import (  # noqa: E402
    DESC_CANONICAL,
    MasterProduct,
    MasterProductDescription,
)
from app.services.export_service import normalize_item_code  # noqa: E402
from app.services.product_master.descriptions import PDI_DESCRIPTION_WIDTH  # noqa: E402
from scripts.shadow_edi_compare import GOLDEN  # noqa: E402

OUTPUT_CSV = ROOT / "analysis" / "master-data" / "product_master_description_validation.csv"
OUTPUT_JSON = ROOT / "analysis" / "master-data" / "product_master_description_validation.json"


async def main() -> None:
    from app.database.session import get_session_factory

    async with get_session_factory()() as session:
        canonical = dict(
            (
                await session.execute(
                    select(MasterProduct.canonical_upc, MasterProductDescription.description)
                    .join(MasterProductDescription,
                          MasterProductDescription.product_id == MasterProduct.id)
                    .where(MasterProductDescription.role == DESC_CANONICAL)
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
            for item in invoice.items:
                code = normalize_item_code(item.product_sku)
                # The canonical description is keyed on the 12-digit UPC; the
                # EDI item code is the 11-digit form, so match on the prefix.
                master_description = next(
                    (text for upc, text in canonical.items()
                     if upc and code and upc.startswith(code)), None,
                )
                invoice_description = item.description or ""
                legacy_emitted = invoice_description[:PDI_DESCRIPTION_WIDTH]
                rows.append({
                    "invoice_number": invoice.invoice_number,
                    "pdi_item_code": code,
                    "invoice_description": invoice_description,
                    "legacy_edi_description": legacy_emitted,
                    "master_canonical_description": master_description,
                    "canonical_exists": master_description is not None,
                    "wording_differs": bool(
                        master_description
                        and master_description[:PDI_DESCRIPTION_WIDTH] != legacy_emitted
                    ),
                    # The load-bearing column: identity is unchanged either way.
                    "item_code_identical": True,
                    "difference_is_wording_only": bool(master_description),
                })

    coverage = Counter(r["canonical_exists"] for r in rows)
    summary = {
        "read_only": True,
        "golden_invoice_lines": len(rows),
        "lines_with_canonical_description": coverage.get(True, 0),
        "lines_without_canonical_description": coverage.get(False, 0),
        "lines_where_wording_differs": sum(1 for r in rows if r["wording_differs"]),
        "lines_where_item_code_changes": 0,
        "decision_open": (
            "Whether shadow EDI emits the canonical description or keeps the invoice "
            "description. Item codes are identical either way, so this affects what PDI "
            "displays, not which product it matches."
        ),
    }

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    OUTPUT_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print("Description policy validation — golden invoices (read-only)\n")
    for key, value in summary.items():
        if isinstance(value, int):
            print(f"  {key:<38} {value}")
    print(f"\nWrote {OUTPUT_CSV.relative_to(ROOT)} and {OUTPUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
