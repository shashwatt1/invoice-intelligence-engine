#!/usr/bin/env python
"""
MVP test harness — scripts/invoice_audit.py

Records, for every real invoice processed through the pipeline, exactly
what the system understood and exactly what it would send to PDI. This
is the instrument for the multi-vendor test set; it is a developer tool
and no production code path imports it.

    python scripts/invoice_audit.py list
    python scripts/invoice_audit.py audit 3376587
    python scripts/invoice_audit.py audit all --json out/audit.json
    python scripts/invoice_audit.py audit all --edi-dir out/edi

Reads only. The one exception is --assume-suggested, which fills
units-per-case from the on-document suggestion so a file can be
inspected before an operator has confirmed anything; files produced that
way are stamped DRY RUN and must never be imported into PDI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.database.session import get_session_factory  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.services.case_mapping_service import (  # noqa: E402
    build_case_mapping_status,
    invoice_units_by_item_code,
)
from app.services.export_service import (  # noqa: E402
    build_pdi_export,
    normalize_item_code,
    pdi_export_eligibility,
    unmapped_item_codes,
)
from app.services.pdi_audit import audit_pdi_export  # noqa: E402

BAR = "=" * 78


def _f(value) -> str:
    return "—" if value is None else f"{float(value):,.2f}"


async def _load(session, selector: str) -> list[Invoice]:
    query = select(Invoice).options(
        selectinload(Invoice.items), selectinload(Invoice.vendor)
    )
    if selector != "all":
        query = query.where(Invoice.invoice_number == selector)
    invoices = list((await session.execute(query.order_by(Invoice.created_at))).scalars())
    if selector != "all" and not invoices:
        import uuid as _uuid

        try:
            one = (await session.execute(
                select(Invoice).options(
                    selectinload(Invoice.items), selectinload(Invoice.vendor)
                ).where(Invoice.id == _uuid.UUID(selector))
            )).scalar_one_or_none()
        except ValueError:
            one = None
        invoices = [one] if one else []
    return invoices


async def _record(session, invoice: Invoice, assume: bool) -> dict:
    units = await invoice_units_by_item_code(session, invoice)
    statuses = build_case_mapping_status(invoice, units)
    missing = unmapped_item_codes(invoice, units)
    eligibility = pdi_export_eligibility(invoice, units)

    effective, dry_run = dict(units), False
    if assume and missing:
        for status in statuses:
            if status.item_code in missing and status.suggested_units_per_case:
                effective[status.item_code] = status.suggested_units_per_case
        dry_run = not unmapped_item_codes(invoice, effective)

    edi, audit = None, None
    if not missing or dry_run:
        edi = build_pdi_export(invoice, effective)
        audit = audit_pdi_export(edi, invoice)

    return {
        "invoice_id": str(invoice.id),
        "vendor": invoice.vendor.name if invoice.vendor else None,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "line_item_count": len(invoice.items),
        "currency": invoice.currency,
        "subtotal": _f(invoice.subtotal),
        "discount_amount": _f(invoice.discount_amount),
        "deposit_total": _f(getattr(invoice, "deposit_total", None)),
        "fuel_surcharge": _f(getattr(invoice, "fuel_surcharge", None)),
        "tax_amount": _f(invoice.tax_amount),
        "grand_total": _f(invoice.grand_total),
        "validation_status": invoice.status,
        "pdi_export_allowed": eligibility.allowed,
        "pdi_export_blocked_reason": eligibility.blocked_reason,
        "unmapped_item_codes": missing,
        "dry_run": dry_run,
        "line_items": [
            {
                "position": item.sort_order + 1,
                "description": item.description,
                "product_code_raw": item.product_sku,
                "item_code_normalized": normalize_item_code(item.product_sku),
                "pack_size": item.pack_size,
                "quantity": _f(item.quantity),
                "unit_price": _f(item.unit_price),
                "line_total": _f(item.line_total),
            }
            for item in sorted(invoice.items, key=lambda i: i.sort_order)
        ],
        "case_mappings": [
            {
                "item_code": s.item_code,
                "description": s.description,
                "pack_size": s.pack_size,
                "units_per_case": s.units_per_case,
                "suggested_units_per_case": s.suggested_units_per_case,
                "suggestion_source": s.suggestion_source,
                "suggestion_candidates": s.suggestion_candidates,
                "mapped": s.mapped,
            }
            for s in statuses
        ],
        "edi": edi,
        "edi_audit": audit.to_dict() if audit else None,
    }


def _print(record: dict) -> None:
    print(f"\n{BAR}\n{record['vendor'] or '(vendor not extracted)'}  "
          f"invoice {record['invoice_number']}  {record['invoice_date']}\n{BAR}")
    print(f"  status={record['validation_status']}  items={record['line_item_count']}  "
          f"export_allowed={record['pdi_export_allowed']}")
    if record["pdi_export_blocked_reason"]:
        print(f"  BLOCKED: {record['pdi_export_blocked_reason']}")
    print(f"  subtotal={record['subtotal']}  discount={record['discount_amount']}  "
          f"deposit={record['deposit_total']}  fuel={record['fuel_surcharge']}  "
          f"tax={record['tax_amount']}  GRAND={record['grand_total']}")

    print(f"\n  {'#':<3}{'description':<24}{'item code':<13}{'pack':<9}"
          f"{'qty':>6}{'unit cost':>11}{'line total':>12}")
    for row in record["line_items"]:
        print(f"  {row['position']:<3}{(row['description'] or '')[:23]:<24}"
              f"{str(row['item_code_normalized'] or '-'):<13}{str(row['pack_size'] or '-'):<9}"
              f"{row['quantity']:>6}{row['unit_price']:>11}{row['line_total']:>12}")

    print(f"\n  {'CASE MAPPING':<24}{'item code':<13}{'confirmed':>10}{'suggested':>11}  source")
    for row in record["case_mappings"]:
        print(f"  {(row['description'] or '')[:23]:<24}{str(row['item_code'] or '-'):<13}"
              f"{str(row['units_per_case'] or '—'):>10}"
              f"{str(row['suggested_units_per_case'] or '—'):>11}"
              f"  {row['suggestion_source'] or '—'}")

    audit = record["edi_audit"]
    if audit is None:
        print("\n  EDI: not generated — confirm the mappings above "
              "(or re-run with --assume-suggested to inspect a dry run).")
        return

    if record["dry_run"]:
        print("\n  *** DRY RUN — units-per-case taken from unconfirmed document "
              "suggestions.\n  *** DO NOT IMPORT THIS FILE INTO PDI.")

    print(f"\n  EDI  bytes={audit['byte_count']}  records={audit['record_count']}  "
          f"B records={audit['detail_count']}  ok={audit['ok']}")
    for record_audit in audit["records"]:
        print(f"    [{record_audit['index']}] {record_audit['record_type']:<6} "
              f"len={record_audit['length']}  {record_audit['raw']!r}")
        for f in record_audit["fields"]:
            shown = f" -> {f['value']}" if f["value"] else ""
            print(f"         {f['name']:<15}[{f['start']}:{f['end']}] {f['raw']!r}{shown}")

    failed = [c for c in audit["checks"] if not c["passed"]]
    print(f"\n  CHECKS  {len(audit['checks']) - len(failed)} passed, {len(failed)} failed")
    for check in failed:
        print(f"    FAIL [{check['kind']}] {check['name']}: "
              f"expected {check['expected']}, got {check['actual']}")

    diff = audit["balance_difference_cents"]
    print("\n  HEADER/DETAIL BALANCE (unresolved — see PDI_OPEN_QUESTIONS.md Q7)")
    print(f"    AMOUNT header       ${audit['header_amount_cents'] / 100:,.2f}")
    print(f"    sum(case cost x qty) ${audit['detail_total_cents'] / 100:,.2f}")
    print(f"    difference          ${diff / 100:,.2f}"
          + ("  <- deposits/fuel not represented in the EDI" if diff else ""))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list every processed invoice")
    audit_cmd = sub.add_parser("audit", help="full record + EDI byte audit")
    audit_cmd.add_argument("selector", help="invoice number, invoice id, or 'all'")
    audit_cmd.add_argument("--json", dest="json_path", help="write the machine-readable audit here")
    audit_cmd.add_argument("--edi-dir", help="write each generated .txt EDI into this directory")
    audit_cmd.add_argument("--assume-suggested", action="store_true",
                           help="DRY RUN: fill unconfirmed mappings from document suggestions")
    args = parser.parse_args()

    session_factory = get_session_factory()
    async with session_factory() as session:
        if args.command == "list":
            invoices = await _load(session, "all")
            print(f"{len(invoices)} invoice(s)\n")
            print(f"{'invoice':<14}{'vendor':<30}{'date':<12}{'items':>6}{'total':>11}  id")
            for invoice in invoices:
                print(f"{str(invoice.invoice_number):<14}"
                      f"{(invoice.vendor.name if invoice.vendor else '—')[:29]:<30}"
                      f"{str(invoice.invoice_date):<12}{len(invoice.items):>6}"
                      f"{_f(invoice.grand_total):>11}  {invoice.id}")
            return 0

        invoices = await _load(session, args.selector)
        if not invoices:
            print(f"No invoice matched {args.selector!r}.")
            return 1

        records = [await _record(session, invoice, args.assume_suggested)
                   for invoice in invoices]

    for record in records:
        _print(record)

    if args.edi_dir:
        directory = Path(args.edi_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for record in records:
            if not record["edi"]:
                continue
            suffix = "_DRYRUN" if record["dry_run"] else ""
            path = directory / f"invoice_{record['invoice_number']}{suffix}_pdi.txt"
            path.write_bytes(record["edi"].encode())
            print(f"\nwrote {path} ({len(record['edi'].encode())} bytes)")

    if args.json_path:
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=2))
        print(f"\nwrote {path}")

    failed = [r for r in records if r["edi_audit"] and not r["edi_audit"]["ok"]]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
