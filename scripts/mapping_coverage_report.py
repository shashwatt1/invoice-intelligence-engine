"""
Mapping coverage report — scripts/mapping_coverage_report.py — READ-ONLY.

    python scripts/mapping_coverage_report.py --database pm_audit
    python scripts/mapping_coverage_report.py --database pm_audit --output out/coverage.json
    python scripts/mapping_coverage_report.py --database pm_audit --compare baseline.json [--fail-on-regression]
    python scripts/mapping_coverage_report.py --database pm_audit \
        --product-master-resolution on --source-identity 47708760

For every stored invoice, classifies each line that would become a PDI B
record (export_service.pdi_items) by how the LIVE commercial resolution
resolves it — case_mapping_service.invoice_commercial_resolution, i.e.
commercial_resolution.resolve_store_codes / resolve_code, unchanged — and,
for a line that does not resolve, why not:

  category                        sub_reason
  PRODUCT_MASTER_PHYSICAL_STORE   -
  PRODUCT_MASTER_GLOBAL           -
  LEGACY_FALLBACK                 -
  CONFLICT                        multiple_approved_in_scope | physical_global_disagree |
                                  approved_without_units
  REQUIRES_MAPPING                product_master_mapping_not_approved | product_master_disabled |
                                  identity_known_no_conversion | absent_from_master_corpus
  NO_USABLE_CODE                  -   (blocked by the identity gate, not the mapping gate)
  STORE_PENDING                   -   (no store yet: nothing can resolve)

Each line also carries the printed identifier's shape (digits only, never the
description), the store kind (physical / source_identity), the units the
resolution produced, the store's legacy units and whether they disagree with
an approved Product Master value. Product descriptions, prices and costs are
never read into the output.

Safety: connects only to the database named with --database, and only when
the configured DATABASE_URL host is local (localhost / 127.0.0.1); the session
is READ ONLY at the connection and the transaction level and is rolled back.
Nothing is written anywhere except the --output file. Output is deterministic:
the same database produces the same JSON, so two runs can be diffed.

The resolver flag and the global-mapping source identity come from the
environment (.env) unless overridden with --product-master-resolution and
--source-identity; the values used are recorded in the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import structlog

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import raiseload, selectinload  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.models.product_master import (  # noqa: E402
    STATE_APPROVED,
    MasterCommercialMapping,
    MasterProductIdentifier,
)
from app.models.store import KIND_PHYSICAL  # noqa: E402
from app.repositories.store_repository import StoreRepository  # noqa: E402
from app.services.case_mapping_service import invoice_commercial_resolution  # noqa: E402
from app.services.export_service import normalize_item_code, pdi_items  # noqa: E402
from app.services.product_master.commercial_resolution import (  # noqa: E402
    PATH_CONFLICT,
    PATH_LEGACY_FALLBACK,
    PATH_PRODUCT_MASTER_GLOBAL,
    PATH_PRODUCT_MASTER_PHYSICAL,
    PATH_REQUIRES_MAPPING,
    CodeResolution,
    global_mapping_holder,
)
from app.services.product_master.identifiers import has_valid_upc_check  # noqa: E402
from scripts.pilot_seed_guard import LOCAL_HOSTS  # noqa: E402

REPORT_VERSION = 1

CATEGORY_NO_USABLE_CODE = "NO_USABLE_CODE"
CATEGORY_STORE_PENDING = "STORE_PENDING"
RESOLVED = frozenset({PATH_PRODUCT_MASTER_PHYSICAL, PATH_PRODUCT_MASTER_GLOBAL, PATH_LEGACY_FALLBACK})
NEEDS_DECISION = frozenset({PATH_REQUIRES_MAPPING, PATH_CONFLICT})

SUB_NOT_APPROVED = "product_master_mapping_not_approved"
SUB_PM_DISABLED = "product_master_disabled"
SUB_IDENTITY_NO_CONVERSION = "identity_known_no_conversion"
SUB_ABSENT = "absent_from_master_corpus"
SUB_MULTIPLE_APPROVED = "multiple_approved_in_scope"
SUB_PHYSICAL_GLOBAL_DISAGREE = "physical_global_disagree"
SUB_APPROVED_WITHOUT_UNITS = "approved_without_units"
SUB_UNKNOWN_CONFLICT = "unclassified_conflict"

STORE_KIND_PENDING = "store_pending"
STORE_KIND_MISSING = "store_record_missing"

_NON_DIGITS = re.compile(r"\D")


class RefusedTargetError(Exception):
    """The target is not allowed; raised before any connection. main() prints it and exits 2."""

    code = 2


# ---- target and session ---------------------------------------------------------------

def target_url(configured_url: str, database: str | None) -> URL:
    """The configured async URL with its database replaced by `database`; local hosts only."""
    if not database or not database.strip():
        raise RefusedTargetError("name the database to read with --database; nothing is assumed.")
    url = make_url(configured_url)
    host = (url.host or "").lower()
    if host not in LOCAL_HOSTS:
        raise RefusedTargetError(f"database host {host or '(none)'!r} is not local. This report reads local "
                            "databases only; it never connects to the pilot or any remote database.")
    if not url.drivername.endswith("asyncpg"):
        raise RefusedTargetError(f"expected an asyncpg DATABASE_URL, got driver {url.drivername!r}.")
    return url.set(database=database.strip())


def apply_overrides(resolution: str | None, source_identity: str | None) -> None:
    """Pin the resolver settings for this process only, before they are first read."""
    if resolution is not None and resolution != "env":
        os.environ["PRODUCT_MASTER_COMMERCIAL_RESOLUTION"] = "true" if resolution == "on" else "false"
    if source_identity is not None:
        os.environ["COMMERCIAL_SOURCE_IDENTITY"] = source_identity
    get_settings.cache_clear()


def route_logs_to_stderr(verbose: bool) -> None:
    """Keep stdout for the report: application logs go to stderr, warnings only unless --verbose."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO if verbose else logging.WARNING),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def read_only_engine(url: URL):
    return create_async_engine(url, poolclass=NullPool,
                               connect_args={"server_settings": {"default_transaction_read_only": "on"}})


async def begin_read_only(session: AsyncSession) -> None:
    """Make the session's transaction read only, and prove it before reading anything."""
    await session.execute(text("SET TRANSACTION READ ONLY"))
    if (await session.execute(text("SHOW transaction_read_only"))).scalar() != "on":
        raise RuntimeError("the transaction is not read only; refusing to continue")


# ---- pure classification ----------------------------------------------------------------

def identifier_shape(printed: str | None) -> str:
    """The printed code's digit shape — never its text. 'none' when nothing usable is printed."""
    digits = _NON_DIGITS.sub("", printed or "")
    if not digits or not digits.strip("0"):
        return "none"
    if len(digits) == 12:
        return "upc_a_12" if has_valid_upc_check(digits) else "12_digits_invalid_check"
    return f"{len(digits)}_digits"


def is_formatted(printed: str | None) -> bool:
    """True when the printed code carried separators or other non-digits."""
    raw = (printed or "").strip()
    return bool(raw) and _NON_DIGITS.sub("", raw) != raw


def conflict_reason(resolved: CodeResolution) -> str:
    note = resolved.notes[0] if resolved.notes else ""
    if "approved Product Master mappings for this code" in note:
        return SUB_MULTIPLE_APPROVED
    if "disagree" in note:
        return SUB_PHYSICAL_GLOBAL_DISAGREE
    if "carries no units_accounted_for" in note:
        return SUB_APPROVED_WITHOUT_UNITS
    return SUB_UNKNOWN_CONFLICT


def classify(
    code: str | None,
    resolved: CodeResolution | None,
    *,
    store_kind: str,
    flag_on: bool,
    product_master_states: Sequence[str] = (),
    identity_known: bool = False,
) -> tuple[str, str | None]:
    """(category, sub_reason) for one PDI line, from the live resolver's answer."""
    if store_kind == STORE_KIND_PENDING:
        return CATEGORY_STORE_PENDING, None
    if code is None:
        return CATEGORY_NO_USABLE_CODE, None
    if resolved is None:
        raise ValueError(f"the resolution has no entry for code {code!r}")
    if resolved.path == PATH_CONFLICT:
        return PATH_CONFLICT, conflict_reason(resolved)
    if resolved.path != PATH_REQUIRES_MAPPING:
        return resolved.path, None
    if product_master_states:
        if STATE_APPROVED in product_master_states and not flag_on:
            return PATH_REQUIRES_MAPPING, SUB_PM_DISABLED
        return PATH_REQUIRES_MAPPING, SUB_NOT_APPROVED
    if identity_known:
        return PATH_REQUIRES_MAPPING, SUB_IDENTITY_NO_CONVERSION
    return PATH_REQUIRES_MAPPING, SUB_ABSENT


def line_record(
    invoice_id: str, invoice_number: str | None, item, *, store_kind: str, flag_on: bool,
    resolved_by_code: Mapping[str, CodeResolution], states_by_code: Mapping[str, Sequence[str]],
    identity_codes: set[str],
) -> dict[str, Any]:
    """One output line. Deliberately built field by field: no description, price or cost can leak in."""
    code = normalize_item_code(item.product_sku)
    resolved = resolved_by_code.get(code) if code else None
    states = sorted(states_by_code.get(code, ())) if code else []
    category, sub_reason = classify(code, resolved, store_kind=store_kind, flag_on=flag_on,
                                    product_master_states=states, identity_known=bool(code and code in identity_codes))
    units = resolved.units_per_case if resolved is not None and category in RESOLVED else None
    legacy = resolved.legacy_units if resolved is not None else None
    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "sort_order": item.sort_order,
        "item_code": code,
        "identifier_shape": identifier_shape(item.product_sku),
        "formatted": is_formatted(item.product_sku),
        "store_kind": store_kind,
        "category": category,
        "sub_reason": sub_reason,
        "units_per_case": units,
        "legacy_units": legacy,
        "legacy_disagreement": bool(category in (PATH_PRODUCT_MASTER_PHYSICAL, PATH_PRODUCT_MASTER_GLOBAL)
                                    and legacy is not None and legacy != units),
        "product_master_states": states,
        "scope": resolved.scope_label if resolved is not None and category in RESOLVED else None,
    }


def summarize(lines: Sequence[Mapping[str, Any]], invoices: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_category = Counter(line["category"] for line in lines)
    resolved = sum(by_category[c] for c in RESOLVED)
    queue = {(line["store_kind"], line["invoice_store_id"], line["item_code"])
             for line in lines if line["category"] in NEEDS_DECISION}
    return {
        "invoices": len(invoices),
        "pdi_lines": len(lines),
        "resolved_lines": resolved,
        "auto_resolution_rate": round(resolved / len(lines), 4) if lines else None,
        "invoices_mapping_complete": sum(1 for inv in invoices if inv["mapping_complete"]),
        "manual_queue_store_codes": len(queue),
        "lines_without_usable_code": by_category[CATEGORY_NO_USABLE_CODE],
        "legacy_disagreements": sum(1 for line in lines if line["legacy_disagreement"]),
        "by_category": dict(sorted(by_category.items())),
        "by_category_and_store_kind": _count(lines, "category", "store_kind"),
        "by_sub_reason": _count([line for line in lines if line["sub_reason"]], "category", "sub_reason"),
        "unresolved_by_identifier_shape": _count(
            [line for line in lines if line["category"] not in RESOLVED], "category", "identifier_shape"),
    }


def _count(lines: Iterable[Mapping[str, Any]], *keys: str) -> dict[str, int]:
    return dict(sorted(Counter(" | ".join(str(line[k]) for k in keys) for line in lines).items()))


def compare(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Line-by-line differences keyed by (invoice_id, sort_order), and the summary deltas."""
    key = lambda line: (line["invoice_id"], line["sort_order"])  # noqa: E731
    before = {key(line): line for line in baseline["lines"]}
    after = {key(line): line for line in current["lines"]}
    changed, regressions = [], []
    for k in sorted(before.keys() & after.keys()):
        old, new = before[k], after[k]
        old_state = (old["category"], old["sub_reason"], old["units_per_case"])
        new_state = (new["category"], new["sub_reason"], new["units_per_case"])
        if old_state == new_state:
            continue
        change = {"invoice_id": k[0], "invoice_number": new["invoice_number"], "sort_order": k[1],
                  "item_code": new["item_code"], "before": list(old_state), "after": list(new_state)}
        changed.append(change)
        if old["category"] in RESOLVED and (new["category"] not in RESOLVED
                                            or new["units_per_case"] != old["units_per_case"]):
            regressions.append(change)
    deltas: dict[str, dict[str, int]] = {}
    for section in ("by_category", "by_sub_reason"):
        old_counts, new_counts = baseline["summary"][section], current["summary"][section]
        deltas[section] = {name: new_counts.get(name, 0) - old_counts.get(name, 0)
                           for name in sorted(set(old_counts) | set(new_counts))
                           if new_counts.get(name, 0) != old_counts.get(name, 0)}
    return {
        "lines_added": len(after.keys() - before.keys()),
        "lines_removed": len(before.keys() - after.keys()),
        "lines_changed": len(changed),
        "regressions": regressions,
        "changes": changed,
        "summary_deltas": deltas,
    }


# ---- database reads -----------------------------------------------------------------------

async def alembic_revisions(session: AsyncSession) -> list[str]:
    """The database's Alembic revisions; empty for a schema built without Alembic."""
    if not (await session.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))).scalar():
        return []
    return list((await session.execute(text("SELECT version_num FROM alembic_version ORDER BY 1"))).scalars())


async def identity_codes(session: AsyncSession) -> set[str]:
    """Every Product Master identifier, reduced exactly as an invoice code is (normalize_item_code)."""
    values = (await session.execute(select(MasterProductIdentifier.normalized_value))).scalars().all()
    return {code for code in (normalize_item_code(v) for v in values) if code}


async def in_scope_states(
    session: AsyncSession, scope_ids: Sequence[Any], codes: Sequence[str],
) -> dict[str, list[str]]:
    """Approval states of Product Master mappings in the scopes the resolver reads, per code."""
    if not scope_ids or not codes:
        return {}
    rows = (await session.execute(
        select(MasterCommercialMapping.pdi_item_code, MasterCommercialMapping.approval_state).where(
            MasterCommercialMapping.store_id.in_(scope_ids),
            MasterCommercialMapping.pdi_item_code.in_(codes),
        )
    )).all()
    states: dict[str, list[str]] = {}
    for code, state in rows:
        states.setdefault(code, []).append(state)
    return states


async def build_report(session: AsyncSession, *, database: Mapping[str, Any]) -> dict[str, Any]:
    """The full report from one read-only session. Reads only."""
    settings = get_settings()
    flag_on = settings.product_master_commercial_resolution
    revisions = await alembic_revisions(session)
    holder = await global_mapping_holder(session)
    known = await identity_codes(session)
    invoices = (await session.execute(
        select(Invoice).options(selectinload(Invoice.items), raiseload("*"))
        .order_by(Invoice.invoice_number.nulls_last(), Invoice.id)
    )).scalars().all()

    stores = StoreRepository(session)
    lines: list[dict[str, Any]] = []
    invoice_rows: list[dict[str, Any]] = []
    for invoice in invoices:
        store = await stores.get(invoice.store_id) if invoice.store_id else None
        if invoice.store_id is None:
            store_kind = STORE_KIND_PENDING
        elif store is None:
            store_kind = STORE_KIND_MISSING
        else:
            store_kind = store.kind
        resolution = await invoice_commercial_resolution(session, invoice)
        items = pdi_items(invoice)
        codes = [c for c in dict.fromkeys(normalize_item_code(i.product_sku) for i in items) if c]
        scope_ids = [s.id for s in (store if store is not None and store.kind == KIND_PHYSICAL else None, holder)
                     if s is not None]
        states = await in_scope_states(session, scope_ids, codes)
        invoice_lines = [
            line_record(str(invoice.id), invoice.invoice_number, item, store_kind=store_kind, flag_on=flag_on,
                        resolved_by_code=resolution.lines, states_by_code=states, identity_codes=known)
            for item in items
        ]
        for line in invoice_lines:
            line["invoice_store_id"] = str(invoice.store_id) if invoice.store_id else None
        lines.extend(invoice_lines)
        invoice_rows.append({
            "invoice_id": str(invoice.id),
            "invoice_number": invoice.invoice_number,
            "status": invoice.status,
            "store_kind": store_kind,
            "store_label": store.label if store is not None else None,
            "pdi_lines": len(invoice_lines),
            "resolved_lines": sum(1 for line in invoice_lines if line["category"] in RESOLVED),
            "mapping_complete": bool(invoice_lines) and all(line["category"] in RESOLVED for line in invoice_lines),
        })

    return {
        "report": "mapping_coverage",
        "version": REPORT_VERSION,
        "database": {**database, "alembic_revisions": revisions},
        "settings": {
            "product_master_commercial_resolution": flag_on,
            "commercial_source_identity": settings.commercial_source_identity.strip() or None,
            "global_holder_found": holder is not None,
        },
        "summary": summarize(lines, invoice_rows),
        "invoices": invoice_rows,
        "lines": lines,
    }


async def run(url: URL, expect_revision: str | None) -> dict[str, Any]:
    engine = read_only_engine(url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await begin_read_only(session)
            if expect_revision is not None:
                revisions = await alembic_revisions(session)
                if revisions != [expect_revision]:
                    raise RefusedTargetError(f"expected alembic revision {expect_revision!r}, database has {revisions!r}.")
            report = await build_report(session, database={"host": url.host, "name": url.database})
            await session.rollback()
            return report
    finally:
        await engine.dispose()


# ---- CLI ------------------------------------------------------------------------------------

def render_summary(report: Mapping[str, Any]) -> str:
    s, settings = report["summary"], report["settings"]
    out = [
        f"Mapping coverage — database {report['database']['name']!r} on {report['database']['host']}, "
        f"revision {','.join(report['database']['alembic_revisions']) or '(none)'}",
        f"Product Master resolution: {'on' if settings['product_master_commercial_resolution'] else 'off'}; "
        f"global source identity: {settings['commercial_source_identity'] or '(none)'}"
        f"{'' if settings['global_holder_found'] else ' (holder not found)'}",
        f"{s['invoices']} invoices, {s['pdi_lines']} PDI lines, {s['resolved_lines']} resolved "
        f"({s['auto_resolution_rate']}), {s['invoices_mapping_complete']} invoices fully mapped, "
        f"{s['manual_queue_store_codes']} store/code pairs need a decision",
        "",
        "  lines  category | store kind",
    ]
    out += [f"  {n:5}  {k}" for k, n in sorted(s["by_category_and_store_kind"].items(), key=lambda kv: -kv[1])]
    out += ["", "  lines  category | sub-reason"]
    out += [f"  {n:5}  {k}" for k, n in sorted(s["by_sub_reason"].items(), key=lambda kv: -kv[1])]
    return "\n".join(out)


def render_comparison(comparison: Mapping[str, Any]) -> str:
    out = ["", f"Compared with baseline: {comparison['lines_changed']} lines changed, "
               f"{comparison['lines_added']} added, {comparison['lines_removed']} removed, "
               f"{len(comparison['regressions'])} regressions"]
    for section, deltas in comparison["summary_deltas"].items():
        out += [f"  {section}: {name} {delta:+d}" for name, delta in deltas.items()]
    for change in comparison["regressions"]:
        out.append(f"  REGRESSION invoice {change['invoice_number']} line {change['sort_order']} "
                   f"{change['item_code']}: {change['before']} -> {change['after']}")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="Local database to read (required; nothing is assumed).")
    parser.add_argument("--output", type=Path, help="Write the full JSON report here.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report to stdout.")
    parser.add_argument("--compare", type=Path, metavar="BASELINE_JSON", help="Compare with a saved report.")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="Exit 1 when a line that resolved in the baseline no longer does, or changed units.")
    parser.add_argument("--product-master-resolution", choices=("env", "on", "off"), default="env")
    parser.add_argument("--source-identity", metavar="STORE_CODE",
                        help="Item Sales store code holding the global mappings (default: environment).")
    parser.add_argument("--expect-revision", metavar="REV", help="Refuse unless alembic_version is exactly REV.")
    parser.add_argument("--verbose", action="store_true", help="Show the resolver's info logs (on stderr).")
    args = parser.parse_args(argv)

    route_logs_to_stderr(args.verbose)

    apply_overrides(args.product_master_resolution, args.source_identity)
    try:
        url = target_url(get_settings().database_url, args.database)
        report = asyncio.run(run(url, args.expect_revision))
    except RefusedTargetError as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return refused.code
    comparison = None
    if args.compare:
        comparison = compare(json.loads(args.compare.read_text()), report)
        report["comparison"] = comparison
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    if args.json:
        sys.stdout.write(payload)
    else:
        print(render_summary(report))
        if comparison is not None:
            print(render_comparison(comparison))
        if args.output:
            print(f"\nJSON written to {args.output}")
    return 1 if args.fail_on_regression and comparison and comparison["regressions"] else 0


if __name__ == "__main__":
    sys.exit(main())
