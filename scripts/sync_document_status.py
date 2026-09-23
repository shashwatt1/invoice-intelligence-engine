"""
Backfill: bring documents.status in step with invoices.status.

    python scripts/sync_document_status.py            # report only
    python scripts/sync_document_status.py --apply    # repair, log, commit

Governed corrections used to re-judge the invoice without moving the
document's lifecycle state, so a corrected-and-validated invoice kept
showing as "Needs review" in the list and dashboard. Revalidation now
syncs the two; this repairs rows that diverged before that. It changes
`documents.status` and appends one log entry per repair — nothing on
the invoice, its lines, mappings, history or EDI. Safe to run twice.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.session import get_session_factory  # noqa: E402
from app.services.document_lifecycle import backfill_document_status  # noqa: E402


async def main(apply: bool) -> int:
    async with get_session_factory()() as session:
        repairs = await backfill_document_status(session, apply=apply)
        if apply:
            await session.commit()
    if not repairs:
        print("Nothing to repair: every persisted document already matches its invoice's decision.")
        return 0
    print(f"{'Repaired' if apply else 'Would repair'} {len(repairs)} document(s):")
    for r in repairs:
        print(f"  invoice {r.invoice_number or '?':>12}  {r.invoice_id}  invoice.status={r.invoice_status:16} "
              f"document.status {r.old.value} -> {r.new.value}")
    if not apply:
        print("Run again with --apply to persist.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="persist the repairs (default: report only)")
    sys.exit(asyncio.run(main(parser.parse_args().apply)))
