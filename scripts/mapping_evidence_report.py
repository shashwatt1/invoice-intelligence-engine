"""
Mapping evidence report — scripts/mapping_evidence_report.py — READ-ONLY.

    python scripts/mapping_evidence_report.py --database pm_audit
    python scripts/mapping_evidence_report.py --database pm_audit --output analysis/evidence/mapping_evidence.json
    python scripts/mapping_evidence_report.py --database pm_audit \
        --output analysis/evidence/mapping_evidence_detailed.json --include-descriptions     # local, opt-in

Every invoice line the live resolver leaves unresolved (REQUIRES_MAPPING or
CONFLICT — as classified by scripts/mapping_coverage_report.py, which runs the
unchanged resolver), grouped by DISTINCT item code, with all evidence the local
database already holds for that code, so one person can decide a product once
instead of re-investigating it on every invoice.

Per code, two independent answers:

  decision_status   resolver_conflict       approved Product Master mappings disagree (resolver CONFLICT)
                    approved_out_of_scope   an approved mapping exists, but in a store the resolver does
                                            not read for these invoices (e.g. another store's legacy row)
                    pending_candidate       a Product Master mapping in scope awaits review
                    no_candidate            nothing awaiting a decision
  evidence          conflicting             more than one value of the same kind
                    single_valued           exactly one value of each kind present
                    identity_only           the Product Master knows the code, but no unit evidence
                    none                    nothing

Two KINDS of unit value are kept apart and never compared as equals:
  commercial_multiplier     units_accounted_for / units_per_case — what PDI multiplies Item Retail by
  physical_items_per_case   product_pricing items/case — how many items the case physically holds
A case that is itself the selling unit has multiplier 1 even when it holds 18 cans
(app/models/product_master.py:151-157, 384-389), so a physical count is never a
multiplier. Evidence is never an approval: every code still requires a person.

Safety: the same guards as the coverage report (local host only, --database
required, READ ONLY connection + transaction, rolled back). Output carries no
prices or costs ever, and no descriptions unless --include-descriptions is given
together with an --output path inside analysis/ (git-ignored); files are mode 600.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.invoice import Invoice  # noqa: E402
from app.models.product_case_mapping import ProductCaseMapping  # noqa: E402
from app.models.product_data_proposal import (  # noqa: E402
    ENTITY_CASE_MAPPING,
    FIELD_UNITS_PER_CASE,
    ProductDataProposal,
)
from app.models.product_master import (  # noqa: E402
    STATE_APPROVED,
    STATE_REJECTED,
    MasterCommercialMapping,
    MasterProduct,
    MasterProductIdentifier,
)
from app.models.product_reference import ProductIdentity, ProductPricing  # noqa: E402
from app.models.store import KIND_PHYSICAL, Store  # noqa: E402
from app.models.vendor import Vendor  # noqa: E402
from app.services.export_service import normalize_item_code  # noqa: E402
from app.services.product_master.commercial_resolution import (  # noqa: E402
    PATH_CONFLICT,
    PATH_REQUIRES_MAPPING,
    global_mapping_holder,
)
from scripts.mapping_coverage_report import (  # noqa: E402
    RefusedTargetError,
    apply_overrides,
    begin_read_only,
    build_report,
    read_only_engine,
    route_logs_to_stderr,
    target_url,
)

REPORT_VERSION = 1
UNRESOLVED = frozenset({PATH_REQUIRES_MAPPING, PATH_CONFLICT})

DECISION_RESOLVER_CONFLICT = "resolver_conflict"
DECISION_APPROVED_OUT_OF_SCOPE = "approved_out_of_scope"
DECISION_PENDING = "pending_candidate"
DECISION_NONE = "no_candidate"
EVIDENCE_CONFLICTING = "conflicting"
EVIDENCE_SINGLE = "single_valued"
EVIDENCE_IDENTITY_ONLY = "identity_only"
EVIDENCE_NONE = "none"

SCOPE_SAME_STORE = "same_store"          # the store of (one of) the invoices carrying the code
SCOPE_GLOBAL = "global_holder"           # the Item Sales location the resolver reads as global
SCOPE_OTHER = "other_store"              # any other store: evidence only, never applied by the resolver
SAMPLE_LIMIT = 5


# ---- pure classification ---------------------------------------------------------------------

def scope_of(store_id: str | None, invoice_store_ids: set[str], holder_id: str | None) -> str:
    if store_id is not None and store_id in invoice_store_ids:
        return SCOPE_SAME_STORE
    if store_id is not None and store_id == holder_id:
        return SCOPE_GLOBAL
    return SCOPE_OTHER


def resolver_reads(scope: str, store_kind: str | None) -> bool:
    """Whether commercial_resolution would read a mapping held here for these invoices (RES:282-293)."""
    return scope == SCOPE_GLOBAL or (scope == SCOPE_SAME_STORE and store_kind == KIND_PHYSICAL)


def classify(
    *, resolver_categories: Mapping[str, int], product_master: Sequence[Mapping[str, Any]],
    legacy: Sequence[Mapping[str, Any]], proposals: Sequence[Mapping[str, Any]],
    pricing: Sequence[Mapping[str, Any]], identity_known: bool,
) -> dict[str, Any]:
    """decision_status, evidence and the two kinds of value for one code. Pure."""
    commercial: dict[int, list[str]] = defaultdict(list)
    for m in product_master:
        if m["units"] is not None and m["approval_state"] != STATE_REJECTED:
            commercial[m["units"]].append(f"product_master:{m['approval_state']}:{m['scope']}")
    for lg in legacy:
        commercial[lg["units_per_case"]].append(f"legacy:{lg['scope']}")
    for p in proposals:
        if p["status"] == "PENDING" and isinstance(p["value"], int):
            commercial[p["value"]].append(f"proposal:PENDING:{p['scope']}")
    physical: dict[int, list[str]] = defaultdict(list)
    for r in pricing:
        for field in ("items_per_case_stated", "items_per_case_derived"):
            if r[field] is not None:
                physical[r[field]].append(f"pricing:{field.rsplit('_', 1)[1]}:{r['scope']}")

    if any(cat.startswith(PATH_CONFLICT) for cat in resolver_categories):
        decision = DECISION_RESOLVER_CONFLICT
    elif (any(m["approval_state"] == STATE_APPROVED and not m["resolver_reads"] for m in product_master)
          or any(not lg["resolver_reads"] for lg in legacy)):
        decision = DECISION_APPROVED_OUT_OF_SCOPE
    elif any(m["approval_state"] not in (STATE_APPROVED, STATE_REJECTED) and m["resolver_reads"]
             for m in product_master):
        decision = DECISION_PENDING
    else:
        decision = DECISION_NONE

    pricing_flagged = any(r["is_conflicted"] for r in pricing)
    if len(commercial) > 1 or len(physical) > 1 or pricing_flagged:
        evidence = EVIDENCE_CONFLICTING
    elif commercial or physical:
        evidence = EVIDENCE_SINGLE
    elif identity_known:
        evidence = EVIDENCE_IDENTITY_ONLY
    else:
        evidence = EVIDENCE_NONE

    return {
        "decision_status": decision,
        "evidence": evidence,
        "commercial_multiplier": {"values": sorted(commercial),
                                  "sources": {str(v): sorted(set(s)) for v, s in sorted(commercial.items())}},
        "physical_items_per_case": {"values": sorted(physical),
                                    "sources": {str(v): sorted(set(s)) for v, s in sorted(physical.items())},
                                    "source_flagged_conflict": pricing_flagged},
        "pending_without_units": any(m["units"] is None and m["approval_state"] not in (STATE_APPROVED, STATE_REJECTED)
                                     for m in product_master),
        "previously_rejected_values": sorted({p["value"] for p in proposals
                                              if p["status"] == "REJECTED" and isinstance(p["value"], int)}),
        "requires_human_decision": True,
    }


def summarize(codes: Sequence[Mapping[str, Any]], skipped: Mapping[str, int]) -> dict[str, Any]:
    return {
        "distinct_codes": len(codes),
        "unresolved_lines": sum(c["occurrences"]["lines"] for c in codes),
        "lines_without_usable_code": skipped.get("NO_USABLE_CODE", 0),
        "lines_store_pending": skipped.get("STORE_PENDING", 0),
        "codes_by_decision_status": dict(sorted(Counter(c["decision_status"] for c in codes).items())),
        "codes_by_evidence": dict(sorted(Counter(c["evidence"] for c in codes).items())),
        "lines_by_decision_and_evidence": dict(sorted(Counter(
            f"{c['decision_status']} | {c['evidence']}" for c in codes for _ in range(c["occurrences"]["lines"])
        ).items())),
    }


# ---- database reads ----------------------------------------------------------------------------

def _sorted(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))


def proposal_units(value: Any) -> int | None:
    """A case-mapping proposal's units as an int; anything else is not unit evidence."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


async def gather(session: AsyncSession, coverage: Mapping[str, Any], *, include_descriptions: bool) -> dict[str, Any]:
    """Group the coverage report's unresolved lines by code and attach every local piece of evidence."""
    stores = {str(s.id): s for s in (await session.execute(select(Store))).scalars().all()}
    holder = await global_mapping_holder(session)
    holder_id = str(holder.id) if holder is not None else None

    by_code: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    skipped: Counter[str] = Counter()
    for line in coverage["lines"]:
        if line["category"] in UNRESOLVED and line["item_code"]:
            by_code[line["item_code"]].append(line)
        elif line["category"] in ("NO_USABLE_CODE", "STORE_PENDING"):
            skipped[line["category"]] += 1
    codes = sorted(by_code)
    if not codes:
        return {"codes": [], "summary": summarize([], skipped)}
    wanted = set(codes)

    def store_info(store_id: Any) -> tuple[str | None, str | None]:
        s = stores.get(str(store_id)) if store_id else None
        return (s.kind, s.label) if s is not None else (None, None)

    invoice_ids = {uuid.UUID(ln["invoice_id"]) for lines in by_code.values() for ln in lines}
    vendor_of = {str(i): v for i, v in (await session.execute(
        select(Invoice.id, Vendor.name).outerjoin(Vendor, Vendor.id == Invoice.vendor_id)
        .where(Invoice.id.in_(invoice_ids)))).tuples().all()}

    pm_rows = (await session.execute(select(
        MasterCommercialMapping.pdi_item_code, MasterCommercialMapping.store_id, MasterCommercialMapping.approval_state,
        MasterCommercialMapping.commercial_unit_basis, MasterCommercialMapping.units_accounted_for,
        MasterCommercialMapping.source_file, MasterCommercialMapping.source_sheet, MasterCommercialMapping.source_row,
        MasterCommercialMapping.product_id,
    ).where(MasterCommercialMapping.pdi_item_code.in_(codes)))).all()
    legacy_rows = (await session.execute(
        select(ProductCaseMapping.item_code, ProductCaseMapping.store_id, ProductCaseMapping.units_per_case,
               ProductCaseMapping.source, ProductDataProposal.status)
        .outerjoin(ProductDataProposal, ProductDataProposal.id == ProductCaseMapping.approved_proposal_id)
        .where(ProductCaseMapping.item_code.in_(codes)))).all()
    proposal_rows = (await session.execute(
        select(ProductDataProposal.entity_key, ProductDataProposal.store_id, ProductDataProposal.status,
               ProductDataProposal.proposed_value)
        .where(ProductDataProposal.entity_type == ENTITY_CASE_MAPPING,
               ProductDataProposal.field == FIELD_UNITS_PER_CASE,
               ProductDataProposal.entity_key.in_(codes)))).all()
    # Workbook codes are stored as imported; compare them the way the exporter normalizes invoice codes.
    pricing_rows = [r for r in (await session.execute(select(
        ProductPricing.item_code, ProductPricing.store_id, ProductPricing.distributor, ProductPricing.pricing_basis,
        ProductPricing.package, ProductPricing.items_per_case_stated, ProductPricing.items_per_case_derived,
        ProductPricing.items_per_case_derivation, ProductPricing.is_conflicted,
        ProductPricing.source_file, ProductPricing.source_sheet, ProductPricing.source_row,
    ))).all() if normalize_item_code(r.item_code) in wanted]
    identity_rows = [r for r in (await session.execute(select(
        MasterProductIdentifier.normalized_value, MasterProductIdentifier.identifier_type,
        MasterProductIdentifier.source_file, MasterProductIdentifier.source_sheet, MasterProductIdentifier.source_row,
        MasterProductIdentifier.product_id,
    ))).all() if normalize_item_code(r.normalized_value) in wanted]

    descriptions: dict[str, set[str]] = defaultdict(set)
    if include_descriptions:
        product_codes = [(r.product_id, r.pdi_item_code) for r in pm_rows]
        product_codes += [(r.product_id, normalize_item_code(r.normalized_value)) for r in identity_rows]
        product_ids = {pid for pid, _ in product_codes}
        canonical: dict[Any, str | None] = dict((await session.execute(
            select(MasterProduct.id, MasterProduct.canonical_description).where(MasterProduct.id.in_(product_ids))
        )).tuples().all()) if product_ids else {}
        for pid, pcode in product_codes:
            if pcode and canonical.get(pid):
                descriptions[pcode].add(f"product_master: {canonical[pid]}")
        for ref in (await session.execute(select(ProductIdentity.item_code, ProductIdentity.store_id,
                                                 ProductIdentity.description))).all():
            rcode = normalize_item_code(ref.item_code)
            if rcode in wanted and ref.description:
                descriptions[rcode].add(f"reference ({store_info(ref.store_id)[1]}): {ref.description}")

    def scoped(store_id: Any, invoice_stores: set[str]) -> tuple[str, str | None, str | None]:
        kind, label = store_info(store_id)
        return scope_of(str(store_id) if store_id else None, invoice_stores, holder_id), kind, label

    out = []
    for code in codes:
        lines = by_code[code]
        invoice_stores = {ln["invoice_store_id"] for ln in lines if ln["invoice_store_id"]}

        pm = []
        for m in (x for x in pm_rows if x.pdi_item_code == code):
            sc, kind, label = scoped(m.store_id, invoice_stores)
            pm.append({"scope": sc, "store_kind": kind, "store_label": label, "approval_state": m.approval_state,
                       "basis": m.commercial_unit_basis, "units": m.units_accounted_for,
                       "resolver_reads": resolver_reads(sc, kind),
                       "source_file": m.source_file, "source_sheet": m.source_sheet, "source_row": m.source_row})
        legacy = []
        for lg in (x for x in legacy_rows if x.item_code == code):
            sc, kind, label = scoped(lg.store_id, invoice_stores)
            legacy.append({"scope": sc, "store_kind": kind, "store_label": label, "units_per_case": lg.units_per_case,
                           "source": lg.source, "proposal_status": lg.status,
                           "resolver_reads": sc == SCOPE_SAME_STORE})
        proposals = []
        for pr in (x for x in proposal_rows if x.entity_key == code):
            sc, _, label = scoped(pr.store_id, invoice_stores)
            proposals.append({"scope": sc, "store_label": label, "status": pr.status,
                              "value": proposal_units(pr.proposed_value)})
        pricing = []
        for pp in (x for x in pricing_rows if normalize_item_code(x.item_code) == code):
            sc, kind, label = scoped(pp.store_id, invoice_stores)
            pricing.append({"scope": sc, "store_kind": kind, "store_label": label, "distributor": pp.distributor,
                            "pricing_basis": pp.pricing_basis, "package": pp.package,
                            "items_per_case_stated": pp.items_per_case_stated,
                            "items_per_case_derived": pp.items_per_case_derived,
                            "derivation": pp.items_per_case_derivation, "is_conflicted": pp.is_conflicted,
                            "source_file": pp.source_file, "source_sheet": pp.source_sheet,
                            "source_row": pp.source_row})
        identity = [{"identifier_type": r.identifier_type, "source_file": r.source_file,
                     "source_sheet": r.source_sheet, "source_row": r.source_row}
                    for r in identity_rows if normalize_item_code(r.normalized_value) == code]

        categories = Counter(ln["category"] + (f" | {ln['sub_reason']}" if ln["sub_reason"] else "") for ln in lines)
        verdict = classify(resolver_categories=categories, product_master=pm, legacy=legacy, proposals=proposals,
                           pricing=pricing, identity_known=bool(identity))
        record = {
            "item_code": code,
            "identifier_shapes": sorted({ln["identifier_shape"] for ln in lines}),
            "occurrences": {
                "lines": len(lines),
                "invoices": len({ln["invoice_id"] for ln in lines}),
                "store_kinds": dict(sorted(Counter(ln["store_kind"] for ln in lines).items())),
                "stores": sorted({store_info(s)[1] or "unknown" for s in invoice_stores}),
                "vendors": sorted({vendor_of.get(ln["invoice_id"]) or "unknown" for ln in lines}),
            },
            "resolver_categories": dict(sorted(categories.items())),
            **verdict,
            "evidence_detail": {
                "product_master": _sorted(pm), "legacy": _sorted(legacy), "proposals": _sorted(proposals),
                "pricing": _sorted(pricing),
                "identity": {"master_identifiers": len(identity), "sample": _sorted(identity)[:SAMPLE_LIMIT]},
            },
        }
        if include_descriptions:
            record["descriptions"] = sorted(descriptions.get(code, set()))
        out.append(record)
    out.sort(key=lambda c: (-c["occurrences"]["lines"], c["item_code"]))
    return {"codes": out, "summary": summarize(out, skipped)}


async def run(url, expect_revision: str | None, *, include_descriptions: bool) -> dict[str, Any]:
    engine = read_only_engine(url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await begin_read_only(session)
            coverage = await build_report(session, database={"host": url.host, "name": url.database})
            if expect_revision is not None and coverage["database"]["alembic_revisions"] != [expect_revision]:
                raise RefusedTargetError(f"expected alembic revision {expect_revision!r}, database has "
                                         f"{coverage['database']['alembic_revisions']!r}.")
            evidence = await gather(session, coverage, include_descriptions=include_descriptions)
            await session.rollback()
    finally:
        await engine.dispose()
    return {
        "report": "mapping_evidence",
        "version": REPORT_VERSION,
        "database": coverage["database"],
        "settings": coverage["settings"],
        "includes_descriptions": include_descriptions,
        "summary": evidence["summary"],
        "codes": evidence["codes"],
    }


# ---- output ----------------------------------------------------------------------------------------

def output_allowed(path: Path | None, include_descriptions: bool) -> None:
    """Descriptions only into a file under the repository's git-ignored analysis/ directory."""
    if not include_descriptions:
        return
    if path is None:
        raise RefusedTargetError("--include-descriptions needs --output (it is never printed to the terminal).")
    resolved = path.resolve()
    if not resolved.is_relative_to((ROOT / "analysis").resolve()):
        raise RefusedTargetError("--include-descriptions output must be inside analysis/ (git-ignored, local only).")


def write_private(path: Path, payload: str) -> None:
    """Directories created here are mode 700, the file mode 600."""
    missing = [p for p in [path.parent, *path.parent.parents] if not p.exists()]
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(payload)
    os.chmod(path, 0o600)


def render_summary(report: Mapping[str, Any], top: int = 25) -> str:
    s = report["summary"]
    out = [
        f"Mapping evidence — database {report['database']['name']!r}, revision "
        f"{','.join(report['database']['alembic_revisions']) or '(none)'}, Product Master resolution "
        f"{'on' if report['settings']['product_master_commercial_resolution'] else 'off'}",
        f"{s['distinct_codes']} distinct unresolved codes across {s['unresolved_lines']} lines "
        f"(+{s['lines_without_usable_code']} lines without a usable code, {s['lines_store_pending']} store-pending)",
        "",
        "  codes  decision status", *[f"  {n:5}  {k}" for k, n in s["codes_by_decision_status"].items()],
        "", "  codes  evidence", *[f"  {n:5}  {k}" for k, n in s["codes_by_evidence"].items()],
    ]
    with_values = [c for c in report["codes"] if c["evidence"] in (EVIDENCE_SINGLE, EVIDENCE_CONFLICTING)
                   or c["decision_status"] != DECISION_NONE]
    without = [c for c in report["codes"] if c not in with_values]
    header = "  lines  item_code     decision / evidence                      multiplier   physical/case"
    for title, group in (("with reusable evidence — review these first", with_values),
                         ("without unit evidence", without)):
        out += ["", f"  {len(group)} codes {title} (top {top} by lines; no descriptions or prices; "
                    "every code needs a person's decision):", header]
        for c in group[:top]:
            out.append(f"  {c['occurrences']['lines']:5}  {c['item_code']:12}  "
                       f"{c['decision_status'] + ' / ' + c['evidence']:40} "
                       f"{','.join(map(str, c['commercial_multiplier']['values'])) or '-':12} "
                       f"{','.join(map(str, c['physical_items_per_case']['values'])) or '-'}")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="Local database to read (required).")
    parser.add_argument("--output", type=Path, help="Write the full JSON report here (mode 600).")
    parser.add_argument("--include-descriptions", action="store_true",
                        help="Add product descriptions to the JSON; requires --output inside analysis/.")
    parser.add_argument("--product-master-resolution", choices=("env", "on", "off"), default="env")
    parser.add_argument("--source-identity", metavar="STORE_CODE")
    parser.add_argument("--expect-revision", metavar="REV")
    parser.add_argument("--top", type=int, default=25, help="Codes listed in the terminal summary.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    route_logs_to_stderr(args.verbose)
    apply_overrides(args.product_master_resolution, args.source_identity)
    try:
        output_allowed(args.output, args.include_descriptions)
        url = target_url(get_settings().database_url, args.database)
        report = asyncio.run(run(url, args.expect_revision, include_descriptions=args.include_descriptions))
    except RefusedTargetError as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return refused.code
    if args.output:
        write_private(args.output, json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(render_summary(report, args.top))
    if args.output:
        print(f"\nJSON written to {args.output} (mode 600)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
