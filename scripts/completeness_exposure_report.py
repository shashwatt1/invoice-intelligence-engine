#!/usr/bin/env python
"""
Completeness exposure report — READ-ONLY.

    DATABASE_URL_SYNC=postgresql://... python scripts/completeness_exposure_report.py [--only-exported]

Runs the deterministic completeness gate (app/services/completeness.py) over every stored invoice, from the OCR text
and the lines as stored, and prints which ones it judges INCOMPLETE — and which of those were already exported to EDI
or received into inventory. Nothing is written: no status, check result or blocking flag is changed, and nothing is
reprocessed. It answers "what exposure did incomplete extraction already create?" so a person can follow it up.

The output names invoice numbers and counts only (no descriptions, prices or codes); keep it out of the repository.
Run it against a restored copy, or with a read-only role, never with credentials that can write.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.completeness import STATUS_INCOMPLETE, assess_completeness  # noqa: E402


def rows(dsn: str):
    import psycopg2

    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute("""
            SELECT i.id, i.invoice_number, i.status, d.raw_ocr_text,
                   (SELECT count(*) FROM document_pages p WHERE p.document_id = d.id),
                   (SELECT count(*) FROM edi_exports e WHERE e.invoice_id = i.id),
                   (SELECT count(*) FROM inventory_receipts r WHERE r.invoice_id = i.id)
            FROM invoices i JOIN documents d ON d.id = i.document_id ORDER BY i.created_at
        """)
        for invoice_id, number, status, ocr, pages, exports, receipts in cursor.fetchall():
            cursor.execute(
                "SELECT product_sku, pack_size, quantity, line_type FROM invoice_items "
                "WHERE invoice_id = %s ORDER BY sort_order", (invoice_id,))
            lines = [SimpleNamespace(product_code=sku, pack_size=pack, section_label=None, quantity=qty,
                                     line_type=kind or "product", printed_units=None)
                     for sku, pack, qty, kind in cursor.fetchall()]
            yield number, status, ocr, pages, exports, receipts, lines
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only-exported", action="store_true", help="list only INCOMPLETE invoices already exported")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL_SYNC")
    if not dsn:
        print("Set DATABASE_URL_SYNC (a read-only connection).", file=sys.stderr)
        return 2
    print(f"{'invoice':<14}{'status':<17}{'photos':>6} {'gate':<11}{'lines':>6}{'missing':>8}{'exports':>8}{'receipts':>9}")
    totals = {"checked": 0, "incomplete": 0, "incomplete_exported": 0, "incomplete_received": 0}
    for number, status, ocr, pages, exports, receipts, lines in rows(dsn):
        report = assess_completeness(ocr, lines)
        totals["checked"] += 1
        if report.status != STATUS_INCOMPLETE:
            continue
        totals["incomplete"] += 1
        totals["incomplete_exported"] += bool(exports)
        totals["incomplete_received"] += bool(receipts)
        if args.only_exported and not exports:
            continue
        print(f"{(number or '-')[:13]:<14}{status:<17}{pages:>6} {report.status:<11}{report.lines:>6}"
              f"{len(report.missing_codes):>8}{exports:>8}{receipts:>9}")
    print(f"\nchecked {totals['checked']} · INCOMPLETE {totals['incomplete']} · of which exported to EDI "
          f"{totals['incomplete_exported']} · received into inventory {totals['incomplete_received']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
