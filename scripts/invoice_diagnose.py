#!/usr/bin/env python
"""
One invoice, diagnosed end to end — READ-ONLY.

    python scripts/invoice_diagnose.py --invoice 508655

Answers, from the stored records and the application's own rules, why an
invoice is or is not EDI-ready:

  * every line's arithmetic against each identity the validator accepts
    (qty x price, with discount, with deposit, with both), and which holds;
  * the invoice's sums exactly as check_subtotal / check_grand_total_math
    compute them, the gaps against the printed subtotal, deposit and grand
    totals, and which single line, row class or header figure would explain
    each gap exactly — never a guess, only an arithmetic match;
  * the validation report the app would produce now (revalidation's
    build_report), with every failed and warning check;
  * each line's commercial resolution path (physical override, global,
    legacy, requires mapping, conflict), the invoice's proposals and any
    Product Master guard on them;
  * the export gate, and — when it allows — the PDI file built in memory
    and audited, written only to analysis/diagnostics/ (never the export
    endpoint, so no EDI export ledger row can be created);
  * the evidence a correction would rest on: the original extracted lines,
    each source page's OCR text, the store's identifiers and the store /
    correction / lifecycle history, and each proposal's evidence. With
    --save-pages, the receipt pages themselves are copied from storage (read
    only) into analysis/diagnostics/.

Runs in a READ ONLY database transaction and always rolls back: a write
anywhere in it would fail rather than happen. Nothing is corrected,
approved or exported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.models.document_page import DocumentPage  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.models.processing_log import PipelineStage, ProcessingLog  # noqa: E402
from app.models.product_data_proposal import ProductDataProposal  # noqa: E402
from app.models.store import Store  # noqa: E402

CENT = Decimal("0.01")
OUT_DIR = ROOT / "analysis" / "diagnostics"
IDENTITIES = ("qty_x_price", "qty_x_net", "qty_x_price_plus_deposit", "qty_x_net_plus_deposit")


def _d(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def line_identities(qty, price, discount, deposit) -> dict[str, Decimal | None]:
    """What a line total would be under each layout the validator recognises."""
    qty, price = _d(qty), _d(price)
    if qty is None or price is None:
        return dict.fromkeys(IDENTITIES)
    disc, dep = _d(discount) or Decimal("0"), _d(deposit) or Decimal("0")
    return {
        "qty_x_price": _q(qty * price),
        "qty_x_net": _q(qty * (price - disc)),
        "qty_x_price_plus_deposit": _q(qty * (price + dep)),
        "qty_x_net_plus_deposit": _q(qty * (price - disc + dep)),
    }


def financial_breakdown(lines: list[dict[str, Any]], header: dict[str, Any],
                        tolerance: Decimal = CENT) -> dict[str, Any]:
    """
    The invoice's arithmetic, row by row and in total, with each gap
    attributed only where one figure explains it exactly.

    `lines`: sort_order, line_type, description, product_sku, quantity,
    unit_price, discount, deposit, line_total. `header`: subtotal,
    deposit_total, discount_amount, fuel_surcharge, tax_amount, grand_total.
    Pure; no database.
    """
    rows: list[dict[str, Any]] = []
    for line in sorted(lines, key=lambda r: r["sort_order"]):
        ids = line_identities(line["quantity"], line["unit_price"], line.get("discount"), line.get("deposit"))
        total = _d(line["line_total"])
        holds = [name for name, value in ids.items() if value is not None and total is not None
                 and abs(value - total) <= tolerance]
        best = ids["qty_x_net_plus_deposit"]
        rows.append({
            **{k: line.get(k) for k in ("sort_order", "line_type", "description", "product_sku", "quantity",
                                        "unit_price", "discount", "deposit", "line_total")},
            "identities": {k: (None if v is None else str(v)) for k, v in ids.items()},
            "identity_holds": holds,
            # How far the printed total is from the fullest identity: the
            # amount this line would contribute to a gap if it were misread.
            "shortfall_vs_full_identity": None if best is None or total is None else str(_q(best - total)),
        })

    product = [r for r in rows if (r["line_type"] or "product") == "product"]
    charges = [r for r in rows if r["line_type"] == "charge"]
    excluded = [r for r in rows if r["line_type"] in ("duplicate", "voided")]
    total_of = lambda rs: _q(sum((_d(r["line_total"]) or Decimal("0") for r in rs), Decimal("0")))  # noqa: E731
    products_sum, charges_sum, excluded_sum = total_of(product), total_of(charges), total_of(excluded)
    zero = Decimal("0")
    deposits_sum = _q(sum(((_d(r["quantity"]) or zero) * (_d(r["deposit"]) or zero) for r in product), zero))
    discounts_sum = _q(sum(((_d(r["quantity"]) or zero) * (_d(r["discount"]) or zero) for r in product), zero))

    subtotal, deposit_total = _d(header.get("subtotal")), _d(header.get("deposit_total"))
    discount_amount, grand_total = _d(header.get("discount_amount")), _d(header.get("grand_total"))

    def explain(gap: Decimal) -> list[str]:
        """Figures that equal the gap to the cent — candidates, never conclusions."""
        found: list[str] = []
        if gap == 0:
            return found
        for name, value in (("header discount_amount", discount_amount), ("sum qty x discount", discounts_sum),
                            ("charge rows", charges_sum), ("duplicate/voided rows", excluded_sum),
                            ("header deposit_total", deposit_total), ("sum qty x deposit", deposits_sum)):
            if value is not None and value != 0 and abs(abs(value) - abs(gap)) <= tolerance:
                found.append(f"{name} = {value}")
        for r in rows:
            shortfall = _d(r["shortfall_vs_full_identity"])
            if shortfall is not None and shortfall != 0 and abs(abs(shortfall) - abs(gap)) <= tolerance:
                found.append(f"line {r['sort_order']} ({r['description']}): printed {r['line_total']} vs "
                             f"qty x (price - disc + dep) {r['identities']['qty_x_net_plus_deposit']}")
            total = _d(r["line_total"])
            if r in excluded and total is not None and abs(abs(total) - abs(gap)) <= tolerance:
                found.append(f"line {r['sort_order']} ({r['description']}) is {r['line_type']} and excluded: {total}")
        return found

    gaps = {}
    if subtotal is not None:
        gap = _q(subtotal - products_sum)
        gaps["subtotal_minus_product_lines"] = {"gap": str(gap), "explained_by": explain(gap)}
    if deposit_total is not None:
        gap = _q(deposit_total - deposits_sum)
        gaps["deposit_total_minus_sum_qty_x_deposit"] = {"gap": str(gap), "explained_by": explain(gap)}
    if grand_total is not None and subtotal is not None:
        gap = _q(grand_total - subtotal - charges_sum)
        gaps["grand_total_minus_subtotal_minus_charges"] = {"gap": str(gap), "explained_by": explain(gap)}

    return {
        "rows": rows,
        "sums": {"product_line_totals": str(products_sum), "charge_line_totals": str(charges_sum),
                 "excluded_line_totals": str(excluded_sum), "sum_qty_x_deposit": str(deposits_sum),
                 "sum_qty_x_discount": str(discounts_sum)},
        "header": {k: (None if header.get(k) is None else str(header.get(k))) for k in
                   ("subtotal", "deposit_total", "discount_amount", "fuel_surcharge", "tax_amount", "grand_total")},
        "gaps": gaps,
        "lines_where_no_identity_holds": [r["sort_order"] for r in product
                                          if r["quantity"] not in (None, 0) and not r["identity_holds"]],
    }


def invoice_query(number: str):
    """
    The invoice with every relationship this diagnosis reads loaded up front.

    An async session cannot lazy-load: touching an unloaded relationship
    raises MissingGreenlet. revalidation's build_report reads invoice.items
    and invoice.vendor (normalized_from_persisted), so both are loaded here —
    the same pair InvoiceRepository.get_detail loads for the API.
    """
    return (select(Invoice)
            .options(selectinload(Invoice.items), selectinload(Invoice.vendor))
            .where(Invoice.invoice_number == number))


HISTORY_STAGES = (PipelineStage.STORE_IDENTIFICATION, PipelineStage.MANUAL_CORRECTION, PipelineStage.LIFECYCLE)


async def diagnose(number: str, save_pages: bool = False) -> dict[str, Any]:
    from app.api.v1.proposals import _product_master_blocks
    from app.database.session import get_session_factory
    from app.services.case_mapping_service import invoice_commercial_resolution
    from app.services.export_service import (
        build_pdi_export,
        normalize_item_code,
        pdi_items,
        persisted_pdi_export_eligibility,
        unmapped_item_codes,
    )
    from app.services.pdi_audit import audit_pdi_export
    from app.services.revalidation_service import build_report
    from app.services.validation.report import CheckStatus

    report: dict[str, Any] = {"invoice_number": number, "read_only": True}
    async with get_session_factory()() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))   # a write anywhere below fails, never happens
        try:
            found = (await session.execute(invoice_query(number))).scalars().all()
            report["copies"] = len(found)
            if not found:
                report["status"] = "NOT_FOUND"
                return report
            invoice = found[0]
            store = await session.get(Store, invoice.store_id) if invoice.store_id else None
            report.update({
                "invoice_id": str(invoice.id), "document_id": str(invoice.document_id), "status": invoice.status,
                "vendor_name": invoice.vendor_name, "invoice_date": str(invoice.invoice_date),
                "store": None if store is None else {"id": str(store.id), "label": store.label, "kind": store.kind,
                                                     "identity_status": store.identity_status},
                "invoice_corrections": invoice.correction_history or [],
            })
            if store is not None:
                report["store"]["identifiers"] = [f"{i.source_system}:{i.identifier_type}={i.identifier_value}"
                                                  for i in store.identifiers]
            report["original_extraction_line_items"] = (invoice.raw_extraction_json or {}).get("line_items")
            report["original_extraction_totals"] = {
                k: (invoice.raw_extraction_json or {}).get(k)
                for k in ("subtotal", "discount_amount", "deposit_total", "fuel_surcharge", "tax_amount", "grand_total")
            }
            history = (await session.execute(
                select(ProcessingLog).where(ProcessingLog.document_id == invoice.document_id,
                                            ProcessingLog.stage.in_(HISTORY_STAGES))
                .order_by(ProcessingLog.created_at)
            )).scalars().all()
            report["history"] = [{"at": str(h.created_at), "stage": str(h.stage), "message": h.message,
                                  "payload": h.payload} for h in history]
            pages = (await session.execute(
                select(DocumentPage).where(DocumentPage.document_id == invoice.document_id)
                .order_by(DocumentPage.page_number)
            )).scalars().all()
            report["pages"] = [{"page": pg.page_number, "filename": pg.filename, "mime_type": pg.mime_type,
                                "sha256": pg.file_hash, "ocr_text": pg.raw_ocr_text} for pg in pages]
            if save_pages and pages:
                from app.services.storage_service import get_storage_service

                storage = get_storage_service()
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                for pg in pages:
                    target = OUT_DIR / f"invoice_{number}_page{pg.page_number}{Path(pg.filename).suffix or '.bin'}"
                    target.write_bytes(await storage.read(pg.file_path))   # storage read only
                    next(x for x in report["pages"] if x["page"] == pg.page_number)["saved_to"] = \
                        str(target.relative_to(ROOT))
            items = sorted(invoice.items, key=lambda i: i.sort_order)
            report["line_corrections"] = {i.sort_order: i.correction_history for i in items if i.correction_history}
            report["finance"] = financial_breakdown(
                [{"sort_order": i.sort_order, "line_type": i.line_type, "description": i.description,
                  "product_sku": i.product_sku, "quantity": i.quantity, "unit_price": i.unit_price,
                  "discount": i.discount, "deposit": i.deposit, "line_total": i.line_total} for i in items],
                {"subtotal": invoice.subtotal, "deposit_total": invoice.deposit_total,
                 "discount_amount": invoice.discount_amount, "fuel_surcharge": invoice.fuel_surcharge,
                 "tax_amount": invoice.tax_amount, "grand_total": invoice.grand_total},
            )

            # The validator as revalidation would run it now.
            latest = (await session.execute(
                select(ProcessingLog).where(ProcessingLog.document_id == invoice.document_id,
                                            ProcessingLog.stage == PipelineStage.VALIDATION)
                .order_by(ProcessingLog.created_at.desc()).limit(1)
            )).scalars().first()
            confidence = ((latest.payload or {}).get("confidence") or {}) if latest else {}
            now = build_report(invoice, ocr_confidence=confidence.get("ocr_confidence"),
                               ai_confidence=confidence.get("ai_confidence"))
            report["validation_now"] = {
                "decision": str(now.decision),
                "not_passed": [{"name": c.name, "status": str(c.status), "field": c.field, "message": c.message,
                                "expected": c.expected, "actual": c.actual}
                               for c in now.checks if c.status not in (CheckStatus.PASSED, CheckStatus.SKIPPED)],
                "stored_report_at": None if latest is None else str(latest.created_at),
            }

            # Commercial resolution, proposals and the guard on them.
            resolution = await invoice_commercial_resolution(session, invoice)
            report["resolution"] = {
                "enabled": resolution.enabled, "paths": resolution.paths(),
                "lines": [{"sort_order": i.sort_order, "description": i.description, "product_sku": i.product_sku,
                           "item_code": (code := normalize_item_code(i.product_sku)),
                           **({} if code is None or code not in resolution.lines else {
                               "path": resolution.lines[code].path, "units": resolution.lines[code].units_per_case,
                               "scope": resolution.lines[code].scope_label,
                               "mapping_id": resolution.lines[code].mapping_id,
                               "notes": list(resolution.lines[code].notes)})}
                          for i in pdi_items(invoice)],
            }
            proposals = (await session.execute(
                select(ProductDataProposal).where(ProductDataProposal.invoice_id == invoice.id)
            )).scalars().all()
            blocks = await _product_master_blocks(session, list(proposals))
            report["proposals"] = [{"id": str(p.id), "item_code": p.entity_key, "proposed": p.proposed_value,
                                    "status": p.status, "source": p.source, "evidence": p.evidence,
                                    "proposed_by": p.proposed_by, "reviewed_by": p.reviewed_by,
                                    "product_master_block": blocks.get(p.id)} for p in proposals]

            # The export gate, and the file it would allow — in memory only.
            units = resolution.units
            eligibility = persisted_pdi_export_eligibility(invoice, units)
            report["export"] = {"allowed": eligibility.allowed, "blocked_reason": eligibility.blocked_reason,
                                "requires_confirmation": eligibility.requires_confirmation,
                                "unmapped_item_codes": unmapped_item_codes(invoice, units)}
            if eligibility.allowed:
                content = build_pdi_export(invoice, units)
                audit = audit_pdi_export(content)
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                path = OUT_DIR / f"invoice_{number}_pdi_preview.txt"
                path.write_bytes(content.encode("utf-8"))
                report["export"].update({
                    "preview_file": str(path.relative_to(ROOT)),
                    "structural_failures": [f.name for f in audit.structural_failures],
                    "b_records": sum(1 for line in content.split("\r\n") if line.startswith("B")),
                })
        finally:
            await session.rollback()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only end-to-end diagnosis of one invoice.")
    parser.add_argument("--invoice", required=True, metavar="NUMBER", help="the invoice's printed number")
    parser.add_argument("--save-pages", action="store_true",
                        help="also copy the receipt pages from storage (read only) into analysis/diagnostics/")
    args = parser.parse_args()
    report = asyncio.run(diagnose(args.invoice, save_pages=args.save_pages))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"invoice_{args.invoice}_diagnosis.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    finance = report.get("finance", {})
    print(f"Invoice {args.invoice}: {report.get('status')}  copies={report.get('copies')}  "
          f"store={(report.get('store') or {}).get('label')}")
    for name, gap in (finance.get("gaps") or {}).items():
        print(f"  {name}: {gap['gap']}  explained by: {gap['explained_by'] or 'nothing exact'}")
    print(f"  lines where no identity holds: {finance.get('lines_where_no_identity_holds')}")
    print(f"  validation now: {(report.get('validation_now') or {}).get('decision')}  "
          f"not passed: {[c['name'] for c in (report.get('validation_now') or {}).get('not_passed', [])]}")
    print(f"  resolution: {(report.get('resolution') or {}).get('paths')}")
    print(f"  proposals: {len(report.get('proposals', []))}  "
          f"guarded: {sum(1 for p in report.get('proposals', []) if p['product_master_block'])}")
    print(f"  export: {report.get('export')}")
    print(f"  pages: {[(p['page'], p.get('saved_to')) for p in report.get('pages', [])]}")
    print(f"\nWrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
