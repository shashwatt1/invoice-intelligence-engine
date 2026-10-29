"""
Mapping replay — scripts/mapping_replay.py — READ-ONLY.

    python scripts/mapping_replay.py                      # the database DATABASE_URL names (local only)
    python scripts/mapping_replay.py --output replay.json
    python scripts/mapping_replay.py --compare baseline.json   # fail (exit 1) if automation got less correct

Measures how much manual mapping work the reuse rules remove, on the invoices
already in a database, without crediting a decision to the invoice it was made on.

Invoices are replayed in the order they were processed. For each product line
the question is "could the system have resolved this line from decisions made
BEFORE this invoice?" under two policies:

  store_only   today's rule: only this store's own earlier decision for the code
  shared       0038: any store's earlier decision for the code (a shared value),
               plus vendor item links from earlier UPC entries for the same vendor

Two timelines:

  recorded      a decision exists from the moment it was actually approved
                (product_data_proposals.reviewed_at; a mapping with no proposal
                from its created_at). Honest about this database's history.
  steady_state  every invoice is fully resolved before the next one arrives —
                the operating rhythm the pilot is moving to. A line's decision
                becomes available once its invoice has been processed.

Correctness: the "truth" for a line is the value its own store finally holds
(product_case_mappings). An automatic value that differs from it counts as an
INCORRECT automatic match and is never added to the automated total; one with
no truth yet is UNVERIFIED. Two earlier decisions that disagree count as a
CONFLICT (sent to review), not as automation.

Output is aggregate counts only — no descriptions, prices or codes — and is
deterministic for a given database, so two runs can be compared.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.models.document import INACTIVE_DOCUMENT_STATUSES, Document  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.models.product_case_mapping import ProductCaseMapping  # noqa: E402
from app.models.product_data_proposal import (  # noqa: E402
    ENTITY_CASE_MAPPING,
    STATUS_APPROVED,
    ProductDataProposal,
)
from app.services import gtin  # noqa: E402
from app.services.export_service import normalize_item_code, pdi_items  # noqa: E402
from app.services.supplier_item_context import supplier_item_ids  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
REPLAY_VERSION = "mapping-replay-1"

# Line outcomes
AUTO_STORE = "auto_same_store"
AUTO_SHARED = "auto_shared_value"
CONFLICT = "conflict_for_review"
MANUAL = "manual_mapping"
NO_CODE = "no_usable_code"
NOT_BARCODE = "code_not_exportable"


@dataclass
class Line:
    invoice_id: str
    at: datetime
    store_id: str
    vendor_id: str | None
    code: str | None
    exportable: bool
    truth: int | None
    upc_entry: tuple[str, str] | None     # (vendor code, entered code) when a person typed this line's UPC


@dataclass
class PolicyResult:
    outcomes: Counter = field(default_factory=Counter)
    correctness: Counter = field(default_factory=Counter)      # correct / incorrect / unverified (automatic only)
    manual_work_items: set = field(default_factory=set)        # distinct (store, code) a person had to decide
    upc_entries_manual: int = 0
    upc_entries_auto: int = 0
    upc_entries_auto_incorrect: int = 0

    def to_dict(self) -> dict:
        lines = sum(self.outcomes.values())
        correct = self.correctness.get("correct", 0)
        return {
            "lines": lines, "outcomes": dict(sorted(self.outcomes.items())),
            "automatic_correctness": dict(sorted(self.correctness.items())),
            "correct_automatic_lines": correct,
            "correct_automatic_rate": round(correct / lines, 4) if lines else 0.0,
            "incorrect_automatic_lines": self.correctness.get("incorrect", 0),
            "manual_work_items": len(self.manual_work_items),
            "upc_entries": {"manual": self.upc_entries_manual, "automatic": self.upc_entries_auto,
                            "automatic_incorrect": self.upc_entries_auto_incorrect},
        }


async def load(session) -> tuple[list[Line], list[tuple[datetime, str, str, int]]]:
    inactive = list(INACTIVE_DOCUMENT_STATUSES)
    invoices = (await session.execute(
        select(Invoice).join(Document, Document.id == Invoice.document_id)
        .where(Invoice.store_id.isnot(None), Document.status.notin_(inactive))
        .options(selectinload(Invoice.items)).order_by(Invoice.created_at, Invoice.id)
    )).scalars().all()
    truth = {(str(m.store_id), m.item_code): m.units_per_case
             for m in (await session.execute(select(ProductCaseMapping))).scalars().all()}
    lines: list[Line] = []
    for inv in invoices:
        vendor_items = supplier_item_ids(inv)
        for item in pdi_items(inv):
            code = normalize_item_code(item.product_sku)
            entry = None
            for change in item.correction_history or []:
                if isinstance(change, dict) and change.get("field") == "product_code" and change.get("new"):
                    vendor = gtin.normalize_vendor_item_code(vendor_items.get(item.sort_order)) or (
                        gtin.classify(change.get("old")).digits
                        if gtin.classify(change.get("old")).kind == gtin.KIND_SHORT else None)
                    if vendor:
                        entry = (vendor, normalize_item_code(change["new"]) or "")
            lines.append(Line(str(inv.id), inv.created_at, str(inv.store_id),
                              str(inv.vendor_id) if inv.vendor_id else None, code,
                              gtin.classify(item.product_sku).exportable,
                              truth.get((str(inv.store_id), code)) if code else None, entry))
    decisions: list[tuple[datetime, str, str, int]] = []
    approved = (await session.execute(select(ProductDataProposal).where(
        ProductDataProposal.entity_type == ENTITY_CASE_MAPPING, ProductDataProposal.status == STATUS_APPROVED,
    ))).scalars().all()
    with_proposal: set[tuple[str, str]] = set()
    for p in approved:
        decisions.append((p.reviewed_at or p.created_at, str(p.store_id), p.entity_key, int(p.proposed_value)))
        with_proposal.add((str(p.store_id), p.entity_key))
    for m in (await session.execute(select(ProductCaseMapping))).scalars().all():
        if (str(m.store_id), m.item_code) not in with_proposal:
            decisions.append((m.created_at, str(m.store_id), m.item_code, m.units_per_case))
    decisions.sort(key=lambda d: (d[0], d[1], d[2]))
    return lines, decisions


def _judge(result: PolicyResult, value: int, truth: int | None) -> None:
    result.correctness["unverified" if truth is None else ("correct" if value == truth else "incorrect")] += 1


def replay(lines: list[Line], decisions, *, timeline: str) -> dict[str, dict]:
    policies = {"store_only": PolicyResult(), "shared": PolicyResult()}
    known_store: dict[tuple[str, str], int] = {}
    known_code: dict[str, dict[str, int]] = defaultdict(dict)
    known_vendor: dict[tuple[str, str], str] = {}
    pending = list(decisions)
    by_invoice: dict[str, list[Line]] = defaultdict(list)
    for line in lines:
        by_invoice[line.invoice_id].append(line)
    order = sorted(by_invoice, key=lambda i: (by_invoice[i][0].at, i))

    def learn(store: str, code: str, value: int) -> None:
        known_store[(store, code)] = value
        known_code[code][store] = value

    for invoice_id in order:
        group = by_invoice[invoice_id]
        at = group[0].at
        if timeline == "recorded":
            while pending and pending[0][0] < at:
                _, store, code, value = pending.pop(0)
                learn(store, code, value)
        for line in group:
            for name, result in policies.items():
                if line.upc_entry is not None:
                    key = (line.vendor_id or "", line.upc_entry[0])
                    if name == "shared" and key in known_vendor:
                        result.upc_entries_auto += 1
                        if known_vendor[key] != line.upc_entry[1]:
                            result.upc_entries_auto_incorrect += 1
                    else:
                        result.upc_entries_manual += 1
                if line.code is None:
                    result.outcomes[NO_CODE] += 1
                    continue
                if not line.exportable:
                    result.outcomes[NOT_BARCODE] += 1
                    continue
                own = known_store.get((line.store_id, line.code))
                if own is not None:
                    result.outcomes[AUTO_STORE] += 1
                    _judge(result, own, line.truth)
                    continue
                if name == "shared":
                    values = {v for s, v in known_code.get(line.code, {}).items() if s != line.store_id}
                    if len(values) == 1:
                        result.outcomes[AUTO_SHARED] += 1
                        _judge(result, next(iter(values)), line.truth)
                        continue
                    if len(values) > 1:
                        result.outcomes[CONFLICT] += 1
                        result.manual_work_items.add((line.store_id, line.code))
                        continue
                result.outcomes[MANUAL] += 1
                result.manual_work_items.add((line.store_id, line.code))
        if timeline == "steady_state":
            for line in group:
                if line.code and line.truth is not None:
                    learn(line.store_id, line.code, line.truth)
        for line in group:
            if line.upc_entry is not None and line.vendor_id:
                known_vendor.setdefault((line.vendor_id, line.upc_entry[0]), line.upc_entry[1])
    return {name: r.to_dict() for name, r in policies.items()}


async def main_async(args) -> dict:
    async with get_session_factory()() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        lines, decisions = await load(session)
        await session.rollback()
    return {
        "version": REPLAY_VERSION,
        "invoices": len({line.invoice_id for line in lines}),
        "product_lines": len(lines),
        "stores": len({line.store_id for line in lines}),
        "decisions": len(decisions),
        "timelines": {t: replay(lines, decisions, timeline=t) for t in ("recorded", "steady_state")},
    }


def _print(report: dict) -> None:
    print(f"{report['invoices']} invoices, {report['product_lines']} product lines, {report['stores']} stores, "
          f"{report['decisions']} recorded decisions")
    for timeline, policies in report["timelines"].items():
        print(f"\n[{timeline}]")
        head = f"  {'':34}" + "".join(f"{p:>14}" for p in policies)
        print(head)
        keys = sorted({k for p in policies.values() for k in p["outcomes"]})
        for k in keys:
            print(f"  {k:34}" + "".join(f"{p['outcomes'].get(k, 0):>14}" for p in policies.values()))
        for label, getter in (("correct automatic lines", lambda p: p["correct_automatic_lines"]),
                              ("INCORRECT automatic lines", lambda p: p["incorrect_automatic_lines"]),
                              ("manual work items (store, code)", lambda p: p["manual_work_items"]),
                              ("UPC entries typed by hand", lambda p: p["upc_entries"]["manual"]),
                              ("UPC entries from vendor links", lambda p: p["upc_entries"]["automatic"])):
            print(f"  {label:34}" + "".join(f"{getter(p):>14}" for p in policies.values()))


def compare(report: dict, baseline: dict) -> list[str]:
    """Regressions: fewer correct automatic lines, or more incorrect ones, on any timeline/policy."""
    problems = []
    for timeline, policies in report["timelines"].items():
        for name, now in policies.items():
            then = baseline.get("timelines", {}).get(timeline, {}).get(name)
            if not then:
                continue
            if now["incorrect_automatic_lines"] > then["incorrect_automatic_lines"]:
                problems.append(f"{timeline}/{name}: incorrect automatic lines "
                                f"{then['incorrect_automatic_lines']} -> {now['incorrect_automatic_lines']}")
            if now["correct_automatic_lines"] < then["correct_automatic_lines"]:
                problems.append(f"{timeline}/{name}: correct automatic lines "
                                f"{then['correct_automatic_lines']} -> {now['correct_automatic_lines']}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output")
    parser.add_argument("--compare")
    args = parser.parse_args()
    host = make_url(get_settings().database_url).host or ""
    if host not in LOCAL_HOSTS:
        print(f"Refusing to read non-local host {host!r}: run this against a local copy.", file=sys.stderr)
        return 2
    report = asyncio.run(main_async(args))
    _print(report)
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True))
    if args.compare:
        problems = compare(report, json.loads(Path(args.compare).read_text()))
        for p in problems:
            print("REGRESSION:", p)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
