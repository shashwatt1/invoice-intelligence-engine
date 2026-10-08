#!/usr/bin/env python
"""
Shadow EDI comparison over the golden invoice corpus — READ-ONLY.

    python scripts/shadow_edi_compare.py
    python scripts/shadow_edi_compare.py --invoices 1000540 3376587 [--pdi-descriptions pdi.csv]

Builds each invoice's EDI twice — once from the legacy case mappings that
are authoritative today, once from APPROVED Product Master commercial
mappings — and reports every difference. Nothing is persisted, no invoice
is modified, and the existing exporter is reused unchanged.

With --invoices it instead traces the named invoices line by line
(app/services/product_master/shadow_trace.py): store-scoped like the live
lookup, with the nomenclature comparison. It runs in a READ ONLY database
transaction, so a write anywhere in it would fail rather than happen.
--pdi-descriptions is an optional CSV of `item_code,description` taken
from PDI itself; the application holds no PDI description of its own.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.models.invoice import Invoice  # noqa: E402
from app.models.product_case_mapping import ProductCaseMapping  # noqa: E402
from app.models.product_master import (  # noqa: E402
    DESC_CANONICAL,
    STATE_APPROVED,
    MasterCommercialMapping,
    MasterProduct,
    MasterProductDescription,
)
from app.models.store import (  # noqa: E402
    KIND_SOURCE_IDENTITY,
    SOURCE_ITEM_SALES,
    TYPE_STORE_CODE,
    Store,
)
from app.services.case_mapping_service import invoice_units_by_item_code  # noqa: E402
from app.services.export_service import normalize_item_code, pdi_items  # noqa: E402
from app.services.product_master.commercial_resolution import (  # noqa: E402
    global_scope_label,
    upc12_for,
)
from app.services.product_master.shadow_edi import compare_invoice  # noqa: E402
from app.services.product_master.shadow_trace import (  # noqa: E402
    CommercialMappingRef,
    LegacyMapping,
    MasterProductRef,
    TraceInputs,
    trace_invoice,
)

OUTPUT_CSV = ROOT / "analysis" / "master-data" / "product_master_shadow_edi_comparison.csv"
OUTPUT_JSON = ROOT / "analysis" / "master-data" / "product_master_shadow_edi_summary.json"
TRACE_CSV = ROOT / "analysis" / "master-data" / "product_master_shadow_trace.csv"
TRACE_JSON = ROOT / "analysis" / "master-data" / "product_master_shadow_trace_summary.json"

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


# ---- line-by-line trace (--invoices) -------------------------------------------

def load_pdi_descriptions(path: Path | None) -> dict[str, str]:
    """`item_code,description` rows from PDI, keyed by the same normalized code EDI emits."""
    if path is None:
        return {}
    found: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            code = normalize_item_code(row.get("item_code"))
            if code and (row.get("description") or "").strip():
                found[code] = row["description"].strip()
    return found


def _store_view(store: Store | None) -> tuple[str | None, str | None]:
    return (store.label, store.kind) if store is not None else (None, None)


async def _trace_inputs(session, invoice, codes: list[str], pdi: dict[str, str],
                        source_code: str | None = None) -> TraceInputs:
    stores = {s.id: s for s in (await session.execute(
        select(Store).options(selectinload(Store.identifiers))
    )).scalars().all()}
    label, kind = _store_view(stores.get(invoice.store_id))
    # The Item Sales location the global mappings are held under (provenance,
    # not the scope): an exact store code, and only while that record is not physical.
    source = next((s for s in stores.values() if source_code and s.kind == KIND_SOURCE_IDENTITY
                   and source_code in s.identifier_values(SOURCE_ITEM_SALES, TYPE_STORE_CODE)), None)

    legacy: dict[str, LegacyMapping] = {}
    if invoice.store_id is not None and codes:
        for row in (await session.execute(
            select(ProductCaseMapping).where(ProductCaseMapping.store_id == invoice.store_id,
                                             ProductCaseMapping.item_code.in_(codes))
        )).scalars().all():
            legacy[row.item_code] = LegacyMapping(row.units_per_case, row.description, row.source)

    mapping_rows = (await session.execute(
        select(MasterCommercialMapping).where(MasterCommercialMapping.pdi_item_code.in_(codes))
    )).scalars().all() if codes else []
    upcs = {u for u in (upc12_for(c) for c in codes) if u}
    product_ids = {m.product_id for m in mapping_rows}
    products = (await session.execute(
        select(MasterProduct).where(
            (MasterProduct.canonical_upc.in_(upcs)) | (MasterProduct.id.in_(product_ids))
        )
    )).scalars().all() if (upcs or product_ids) else []
    canonical_rows = (await session.execute(
        select(MasterProductDescription).where(
            MasterProductDescription.product_id.in_([p.id for p in products]),
            MasterProductDescription.role == DESC_CANONICAL,
        )
    )).scalars().all() if products else []
    canonical_role: dict = {}
    for row in canonical_rows:
        canonical_role.setdefault(row.product_id, set()).add(row.description)

    def product_ref(p: MasterProduct) -> MasterProductRef:
        role_rows = canonical_role.get(p.id, set())
        text_: str | None
        source: str | None
        if p.canonical_description:
            text_, source = p.canonical_description, "master_products.canonical_description"
            if role_rows and role_rows != {p.canonical_description}:
                source += " (differs from master_product_descriptions CANONICAL)"
        elif len(role_rows) == 1:
            text_, source = next(iter(role_rows)), "master_product_descriptions CANONICAL"
        else:
            text_, source = None, ("several CANONICAL rows disagree" if role_rows else None)
        return MasterProductRef(str(p.id), p.canonical_key, p.canonical_upc, text_, source)

    by_code: dict[str, list[MasterProductRef]] = {}
    mapped_code = {m.product_id: m.pdi_item_code for m in mapping_rows}
    for p in products:
        codes_for_product = {normalize_item_code(p.canonical_upc)} | {mapped_code.get(p.id)}
        for code in codes_for_product - {None}:
            if code in codes and all(r.product_id != str(p.id) for r in by_code.get(code, [])):
                by_code.setdefault(code, []).append(product_ref(p))

    mappings: dict[str, list[CommercialMappingRef]] = {}
    for m in mapping_rows:
        m_label, m_kind = _store_view(stores.get(m.store_id))
        mappings.setdefault(m.pdi_item_code, []).append(CommercialMappingRef(
            str(m.id), str(m.product_id), str(m.store_id), m_label or str(m.store_id), m_kind or "unknown",
            m.approval_state, m.commercial_unit_basis, m.units_accounted_for, m.pdi_item_code))

    return TraceInputs(
        store_id=str(invoice.store_id) if invoice.store_id else None, store_label=label, store_kind=kind,
        legacy=legacy, products=by_code, mappings=mappings, pdi_descriptions=pdi,
        global_holder_id=str(source.id) if source else None,
        global_scope_label=global_scope_label(source_code) if source and source_code else None,
    )


async def trace_main(numbers: list[str], pdi_path: Path | None, source_code: str | None = None) -> None:
    from app.core.config import get_settings
    from app.database.session import get_session_factory

    source_code = source_code if source_code is not None else get_settings().commercial_source_identity.strip() or None

    pdi = load_pdi_descriptions(pdi_path)
    lines: list[dict] = []
    invoices_report: list[dict] = []
    async with get_session_factory()() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))   # a write anywhere below fails, never happens
        try:
            for number in numbers:
                found = (await session.execute(
                    select(Invoice).options(selectinload(Invoice.items)).where(Invoice.invoice_number == number)
                )).scalars().all()
                if not found:
                    invoices_report.append({"invoice_number": number, "status": "NOT_FOUND"})
                    continue
                for invoice in found:
                    codes = sorted({c for c in (normalize_item_code(i.product_sku) for i in pdi_items(invoice)) if c})
                    traces = trace_invoice(invoice, await _trace_inputs(session, invoice, codes, pdi, source_code))
                    invoices_report.append({
                        "invoice_number": number, "invoice_id": str(invoice.id), "status": "TRACED",
                        "store": traces[0].store_label if traces else None,
                        "store_kind": traces[0].store_kind if traces else None,
                        "lines": len(traces),
                        "comparison": dict(Counter(t.comparison for t in traces)),
                        "resolution": dict(Counter(t.resolution_path or "NO_ITEM_CODE" for t in traces)),
                        "nomenclature": dict(Counter(t.nomenclature for t in traces)),
                    })
                    for t in traces:
                        record = asdict(t)
                        record["invoice_id"] = str(invoice.id)
                        record["differences"] = " || ".join(
                            f"{d['field']}:{d['legacy']}->{d['shadow']}" for d in t.differences)
                        record["blocked_reasons"] = " || ".join(t.blocked_reasons)
                        record["master_b_fields"] = json.dumps(t.master_b_fields) if t.master_b_fields else ""
                        lines.append(record)
        finally:
            await session.rollback()

    summary = {
        "read_only": True, "edi_authority": "product_case_mappings — unchanged",
        "store_scoped": True, "pdi_descriptions_supplied": len(pdi), "global_mappings_held_under_item_sales": source_code,
        "invoices": invoices_report,
        "comparison": dict(Counter(r["comparison"] for r in lines)),
        "nomenclature": dict(Counter(r["nomenclature"] for r in lines)),
    }
    TRACE_CSV.parent.mkdir(parents=True, exist_ok=True)
    if lines:
        with TRACE_CSV.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(lines[0].keys()))
            writer.writeheader()
            writer.writerows(lines)
    TRACE_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print("Product Master shadow trace (read-only, store-scoped)\n")
    for inv in invoices_report:
        if inv["status"] != "TRACED":
            print(f"  {inv['invoice_number']:<14} NOT FOUND in this database")
            continue
        print(f"  {inv['invoice_number']:<14} {inv['lines']:>3} lines  store={inv['store']} ({inv['store_kind']})")
        print(f"      resolution   {inv['resolution']}")
        print(f"      comparison   {inv['comparison']}")
        print(f"      nomenclature {inv['nomenclature']}")
    print(f"\nWrote {TRACE_CSV.relative_to(ROOT)} and {TRACE_JSON.relative_to(ROOT)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Product Master shadow EDI comparison.")
    parser.add_argument("--invoices", nargs="+", metavar="NUMBER",
                        help="trace these invoice numbers line by line instead of comparing the golden set")
    parser.add_argument("--pdi-descriptions", type=Path, metavar="CSV",
                        help="optional item_code,description export from PDI (trace mode only)")
    parser.add_argument("--source-identity", metavar="STORE_CODE",
                        help="Item Sales store code the global commercial mappings are held under — "
                             "provenance, not the scope (default: COMMERCIAL_SOURCE_IDENTITY; trace mode "
                             "only, nothing is switched on)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    if args.invoices:
        asyncio.run(trace_main(args.invoices, args.pdi_descriptions, args.source_identity))
    else:
        asyncio.run(main())
