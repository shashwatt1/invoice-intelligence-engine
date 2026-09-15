#!/usr/bin/env python
"""
Propose units-per-case from reference evidence — scripts/propose_from_reference.py

    python scripts/propose_from_reference.py <invoice-id> [--dry-run] [--min-strength package]

For every product on the invoice that has NO approved mapping, looks at
the best reference evidence and, if it is strong enough, writes a
PENDING product_data_proposal carrying the evidence and its provenance.

Strength, strongest first:

  explicit  a typed items/case cell            -> beer_inventory_explicit
  package   a two-fraction package string      -> beer_inventory_package
  ratio     a source's own case/unit cost      -> reference_derived

Anything weaker — a description hint, an ambiguous pack, a sibling
pattern — is NOT proposed here; those need a person.

Writes proposals only. Nothing reaches product_case_mappings until a
reviewer approves through scripts/review_proposals.py.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.database.session import get_session_factory  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.models.product_data_proposal import (  # noqa: E402
    ENTITY_CASE_MAPPING,
    FIELD_UNITS_PER_CASE,
    SOURCE_BEER_INVENTORY_EXPLICIT,
    SOURCE_BEER_INVENTORY_PACKAGE,
    SOURCE_REFERENCE_DERIVED,
)
from app.repositories.product_case_mapping_repository import (
    ProductCaseMappingRepository,  # noqa: E402
)
from app.repositories.product_data_proposal_repository import (  # noqa: E402
    ProductDataProposalRepository,
)
from app.services.case_mapping_service import invoice_units_by_item_code  # noqa: E402
from app.services.export_service import normalize_item_code  # noqa: E402
from app.services.store_reference_service import (  # noqa: E402
    EVIDENCE_EXPLICIT,
    EVIDENCE_PACKAGE,
    EVIDENCE_RATIO,
    EVIDENCE_RETAIL,
    match_invoice_against_reference,
)

STRENGTH = {EVIDENCE_EXPLICIT: 4, EVIDENCE_PACKAGE: 3, EVIDENCE_RATIO: 2, EVIDENCE_RETAIL: 1}
PROPOSAL_SOURCE = {
    EVIDENCE_EXPLICIT: SOURCE_BEER_INVENTORY_EXPLICIT,
    EVIDENCE_PACKAGE: SOURCE_BEER_INVENTORY_PACKAGE,
    EVIDENCE_RATIO: SOURCE_REFERENCE_DERIVED,
    EVIDENCE_RETAIL: SOURCE_REFERENCE_DERIVED,
}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("invoice_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-strength", choices=["explicit", "package", "ratio", "retail"],
                        default="ratio", help="weakest evidence kind to propose from (default: ratio)")
    args = parser.parse_args()
    minimum = {"explicit": 4, "package": 3, "ratio": 2, "retail": 1}[args.min_strength]

    async with get_session_factory()() as session:
        invoice = (await session.execute(
            select(Invoice).options(selectinload(Invoice.items), selectinload(Invoice.vendor))
            .where(Invoice.id == uuid.UUID(args.invoice_id))
        )).scalar_one_or_none()
        if invoice is None:
            print("Invoice not found.")
            return 1

        # The invoice's store, never a global setting: it decides which
        # reference rows are evidence and which review queue this feeds.
        store = invoice.store_id
        approved = await invoice_units_by_item_code(session, invoice)
        matches = await match_invoice_against_reference(session, invoice)
        proposals = ProductDataProposalRepository(session)
        mappings = ProductCaseMappingRepository(session)

        print(f"{invoice.vendor.name if invoice.vendor else '—'}  invoice {invoice.invoice_number}\n")
        print(f"{'UPC':<12} {'description':<23} {'evidence':<19} {'value':>5}  {'from':<38} action")
        print("-" * 112)

        proposed = skipped_weak = skipped_none = 0
        for item in sorted(invoice.items, key=lambda i: i.sort_order):
            code = normalize_item_code(item.product_sku)
            if not code or code in approved:
                continue
            match = matches.get(code)
            best = match.best_evidence if match else None
            desc = (item.description or "")[:22]
            if best is None:
                print(f"{code:<12} {desc:<23} {'—':<19} {'—':>5}  {'':<38} needs a person")
                skipped_none += 1
                continue
            origin = f"{best.source_sheet or best.source_file}" + (f" row {best.source_row}" if best.source_row else "")
            if STRENGTH[best.kind] < minimum:
                print(f"{code:<12} {desc:<23} {best.kind:<19} {best.units_per_case:>5}  {origin[:38]:<38} below --min-strength")
                skipped_weak += 1
                continue

            current = await mappings.get(store, code)
            agree = [e for e in match.all_evidence if e.units_per_case == best.units_per_case]
            dissent = [e for e in match.all_evidence if e.units_per_case != best.units_per_case]
            evidence = {
                "invoice_description": item.description,
                "invoice_case_cost": float(item.unit_price) if item.unit_price is not None else None,
                "best": {"kind": best.kind, "units_per_case": best.units_per_case, **best.detail},
                "agreeing_sources": len(agree),
                "dissenting": [
                    {"kind": e.kind, "units_per_case": e.units_per_case,
                     "source_sheet": e.source_sheet, "source_row": e.source_row}
                    for e in dissent
                ],
            }
            strength_note = (
                f" [{best.detail['strength']}, margin {best.detail.get('margin', 0)*100:.1f}%]"
                if best.kind == EVIDENCE_RETAIL else ""
            )
            reason = (
                f"{best.kind}{strength_note}: {best.units_per_case} units/case from "
                f"{best.source_sheet or best.source_file}"
                + (f" row {best.source_row}" if best.source_row else "")
                + (f"; {len(agree)} source(s) agree" if len(agree) > 1 else "")
                + (f"; {len(dissent)} DISSENT" if dissent else "")
            )
            action = "PROPOSE" + (f"  ({len(dissent)} dissenting)" if dissent else "")
            print(f"{code:<12} {desc:<23} {best.kind:<19} {best.units_per_case:>5}  {origin[:38]:<38} {action}")
            if not args.dry_run:
                await proposals.create(
                    store_id=store, entity_type=ENTITY_CASE_MAPPING, entity_key=code,
                    field=FIELD_UNITS_PER_CASE, proposed_value=best.units_per_case,
                    current_value=current.units_per_case if current else None,
                    source=PROPOSAL_SOURCE[best.kind], proposed_by="system:propose-from-reference",
                    invoice_id=invoice.id, evidence=evidence, reason=reason,
                    source_file=best.source_file, source_sheet=best.source_sheet,
                    source_row=best.source_row,
                )
            proposed += 1

        if not args.dry_run:
            await session.commit()

    print(f"\n{'would propose' if args.dry_run else 'proposed'}: {proposed}   "
          f"below threshold: {skipped_weak}   no reference evidence: {skipped_none}")
    if args.dry_run:
        print("DRY RUN — nothing written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
