#!/usr/bin/env python
"""
Human review package for the commercial candidates — READ-ONLY.

    python scripts/build_human_review_package.py

Assembles all 414 commercial candidates into the six groups a reviewer
actually works in, so the queue can be read as a whole rather than opened
one record at a time.

It decides nothing. The grouping is descriptive — it reports what the
evidence produced and whether the legacy mapping agrees — and the counts
are statistics, not recommendations. "Legacy agrees" is an observation
about two sources matching, not a finding that a candidate is safe to
approve; that judgement is the reviewer's.

The session is opened READ ONLY. Nothing is approved, rejected, resolved or
written.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.master_commercial_review_service import (  # noqa: E402
    LEGACY_AGREES,
    LEGACY_DISSENTS,
)
from app.services.product_master.display_name import resolve_display_name  # noqa: E402
from scripts.review_product_master_candidates import (  # noqa: E402
    derive_review_status,
    legacy_agreement,
)

OUTPUT_CSV = ROOT / "analysis" / "master-data" / "product_master_human_review_package.csv"
OUTPUT_JSON = ROOT / "analysis" / "master-data" / "product_master_human_review_summary.json"
OUTPUT_MD = ROOT / "docs" / "product-master-human-review-package.md"
CONFLICT_DOSSIER = ROOT / "analysis" / "master-data" / "product_master_commercial_conflicts.json"

GROUP_1 = "1_READY_LEGACY_AGREES"
GROUP_2 = "2_READY_NO_LEGACY_MAPPING"
GROUP_3 = "3_READY_LEGACY_DISSENTS"
GROUP_4 = "4_CONFLICT"
GROUP_5 = "5_APPROVED"
GROUP_6 = "6_REJECTED"
GROUPS = [GROUP_1, GROUP_2, GROUP_3, GROUP_4, GROUP_5, GROUP_6]

# Business-facing first: what the product is called, how it is identified,
# where and how it is sold, then the evidence. `product_name` is a label
# from the description policy (display_name.py), never the identity;
# `product_name_basis` says whether it is canonical, source-derived, or
# AMBIGUOUS_SOURCE — "Multiple source names", with every distinct wording
# and where it came from in `product_name_variants`.
COLUMNS = [
    "group", "product_name", "product_name_basis", "product_name_variants",
    "canonical_upc", "pdi_item_code", "store", "units_accounted_for", "case_cost",
    "cost_basis", "review_status", "commercial_unit_basis", "evidence_source",
    "source_file", "source_sheet", "source_row", "product_name_variant_count",
    "product_name_source", "product_name_reference", "store_id",
    "store_identity_status", "legacy_units_per_case", "legacy_agreement",
    "evidence_notes", "why_ready_or_conflicted",
]


def variants_cell(name) -> str | None:
    """Every distinct source wording, with where each came from — one cell."""
    if name.basis != "AMBIGUOUS_SOURCE":
        return None
    return " || ".join(
        f"{w.description} [{'; '.join(w.references) or w.source_class}]" for w in name.wordings
    )


def group_for(review_status: str, agreement: str) -> str:
    """Which section of the package a candidate belongs to. Exactly one."""
    if review_status == "APPROVED":
        return GROUP_5
    if review_status == "REJECTED":
        return GROUP_6
    if review_status == "CONFLICT":
        return GROUP_4
    if agreement == LEGACY_AGREES:
        return GROUP_1
    if agreement == LEGACY_DISSENTS:
        return GROUP_3
    return GROUP_2


def why(review_status: str, agreement: str, basis: str, units, notes: str | None) -> str:
    if review_status == "CONFLICT":
        return notes or "The evidence produced no single multiplier."
    if review_status == "NO_MULTIPLIER":
        return "No distributor stated sellable units per case for this product."
    if review_status in {"APPROVED", "REJECTED"}:
        return f"A reviewer recorded a decision; state is {review_status}."
    settled = (
        "the case is the selling unit, so it accounts for 1"
        if basis == "CASE_IS_SELLING_UNIT"
        else f"the case breaks into {units} sellable units"
    )
    if agreement == LEGACY_AGREES:
        return (f"One distributor statement settled it — {settled} — and the existing "
                "governed mapping holds the same value.")
    if agreement == LEGACY_DISSENTS:
        return (f"One distributor statement settled it — {settled} — but the existing "
                "governed mapping holds a different value.")
    return (f"One distributor statement settled it — {settled}. No governed mapping "
            "exists to corroborate or dissent.")


def load(dsn: str) -> list[dict]:
    import psycopg2

    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT item_code, units_per_case FROM product_case_mappings")
        legacy: dict[str, set[int]] = {}
        for item_code, units in cursor.fetchall():
            legacy.setdefault(item_code, set()).add(units)

        cursor.execute("""
            SELECT d.product_id, d.role, d.description, d.source_system,
                   d.source_file, d.source_sheet, d.source_row
            FROM master_product_descriptions d
            WHERE d.product_id IN (SELECT product_id FROM master_commercial_mappings)
        """)
        descriptions: dict[str, list[dict]] = {}
        for product_id, role, text, system, file, sheet, row in cursor.fetchall():
            descriptions.setdefault(str(product_id), []).append({
                "role": role, "description": text, "source_system": system,
                "source_file": file, "source_sheet": sheet, "source_row": row})

        cursor.execute("""
            SELECT p.canonical_upc, m.pdi_item_code, m.store_id, s.identity_status,
                   m.commercial_unit_basis, m.units_accounted_for, m.case_cost,
                   m.cost_basis, m.approval_state, m.source_file, m.source_sheet,
                   m.source_row, m.evidence, m.product_id, s.display_name
            FROM master_commercial_mappings m
            JOIN master_products p ON p.id = m.product_id
            JOIN stores s ON s.id = m.store_id
        """)
        rows: list[dict] = []
        for (upc, code, store_id, identity_status, basis, units, cost, cost_basis,
             approval_state, source_file, source_sheet, source_row,
             evidence, product_id, store_name) in cursor.fetchall():
            evidence = evidence or {}
            status = derive_review_status(approval_state, basis, units)
            agreement = legacy_agreement(units, legacy.get(code, set()))
            statements = evidence.get("source_statements") or []
            name = resolve_display_name(descriptions.get(str(product_id), []))
            rows.append({
                "group": group_for(status, agreement),
                "product_name": name.label,
                "product_name_basis": name.basis,
                "product_name_variants": variants_cell(name),
                "product_name_variant_count": name.variant_count,
                "product_name_source": name.source_class,
                "product_name_reference": name.source_reference,
                "review_status": status,
                "canonical_upc": upc,
                "pdi_item_code": code,
                # The same label the review workbench shows for the store.
                "store": store_name or (
                    f"Store {evidence.get('source_store_identifier', '')}".strip()),
                "store_id": str(store_id),
                "store_identity_status": identity_status,
                "commercial_unit_basis": basis,
                "units_accounted_for": units,
                "case_cost": str(cost) if cost is not None else None,
                "cost_basis": cost_basis,
                "evidence_source": " || ".join(
                    str(s.get("statement")) for s in statements if s.get("statement")),
                "source_file": source_file,
                "source_sheet": source_sheet,
                "source_row": source_row,
                "legacy_units_per_case": " || ".join(
                    str(u) for u in sorted(legacy.get(code, set()))) or None,
                "legacy_agreement": agreement,
                "evidence_notes": evidence.get("notes"),
                "why_ready_or_conflicted": why(
                    status, agreement, basis, units, evidence.get("notes")),
            })
    finally:
        connection.close()
    return rows


def summarise(rows: list[dict], conflicts: list[dict]) -> dict:
    ready = [r for r in rows if r["review_status"] == "READY_FOR_REVIEW"]
    return {
        "read_only": True,
        "decisions_made_by_this_report": 0,
        "total_candidates": len(rows),
        "group_counts": {group: sum(1 for r in rows if r["group"] == group)
                         for group in GROUPS},
        "ready_for_review": {
            "total": len(ready),
            "legacy_agrees": sum(1 for r in ready if r["legacy_agreement"] == LEGACY_AGREES),
            "no_legacy_mapping": sum(
                1 for r in ready if r["legacy_agreement"] == "NO_LEGACY_MAPPING"),
            "legacy_dissents": sum(
                1 for r in ready if r["legacy_agreement"] == LEGACY_DISSENTS),
            "by_commercial_basis": dict(Counter(r["commercial_unit_basis"] for r in ready)),
            "by_store": dict(Counter(r["store_id"] for r in ready)),
            "by_evidence_source": dict(Counter(r["evidence_source"] for r in ready)),
            "by_multiplier": dict(Counter(str(r["units_accounted_for"]) for r in ready)),
        },
        "conflicts": {
            "total": sum(1 for r in rows if r["group"] == GROUP_4),
            "resolved_by_this_report": 0,
            "dossier": conflicts,
        },
        "statistics_note": (
            "These are counts, not recommendations. A candidate whose legacy mapping "
            "agrees is not thereby safe to approve — agreement is two sources matching, "
            "and the decision remains the reviewer's."
        ),
    }


def write_markdown(summary: dict, rows: list[dict]) -> None:
    lines = [
        "# Product Master — Human Review Package",
        "",
        "**Status: READ-ONLY. No decision was made and nothing was written.** Every one of "
        f"the {summary['total_candidates']} commercial candidates appears below exactly "
        "once, grouped by what the evidence produced and whether the existing governed "
        "mapping agrees.",
        "",
        "The counts are statistics. **A group is not a recommendation** — \"legacy agrees\" "
        "means two sources match, not that a candidate is safe to approve.",
        "",
        "Full per-candidate detail: `analysis/master-data/product_master_human_review_package.csv`.",
        "",
        "## Groups",
        "",
        "| Group | Count | What it means |",
        "| --- | --- | --- |",
    ]
    meanings = {
        GROUP_1: "one distributor statement settled it and the governed mapping holds the same value",
        GROUP_2: "one distributor statement settled it; no governed mapping exists either way",
        GROUP_3: "one distributor statement settled it but the governed mapping differs",
        GROUP_4: "the evidence produced no multiplier; an explicit decision is required",
        GROUP_5: "a reviewer approved it",
        GROUP_6: "a reviewer rejected it",
    }
    for group in GROUPS:
        lines.append(
            f"| `{group}` | {summary['group_counts'][group]} | {meanings[group]} |")

    ready = summary["ready_for_review"]
    lines += [
        "",
        f"## The {ready['total']} READY_FOR_REVIEW candidates",
        "",
        f"- legacy agrees: **{ready['legacy_agrees']}**",
        f"- no legacy mapping: **{ready['no_legacy_mapping']}**",
        f"- legacy dissents: **{ready['legacy_dissents']}**",
        "",
        "### By commercial basis", "",
        "| Basis | Count |", "| --- | --- |",
    ]
    for basis, count in sorted(ready["by_commercial_basis"].items()):
        lines.append(f"| `{basis}` | {count} |")
    lines += ["", "### By multiplier", "", "| Multiplier | Count |", "| --- | --- |"]
    for multiplier, count in sorted(ready["by_multiplier"].items(), key=lambda kv: int(kv[0])):
        lines.append(f"| {multiplier} | {count} |")
    lines += ["", "### By store", "", "| Store | Count |", "| --- | --- |"]
    for store, count in sorted(ready["by_store"].items()):
        lines.append(f"| `{store[:8]}` | {count} |")
    lines += ["", "### By evidence source", "", "| Source | Count |", "| --- | --- |"]
    for source, count in sorted(ready["by_evidence_source"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {source or '—'} | {count} |")

    lines += [
        "",
        f"## The {summary['conflicts']['total']} conflicts — unresolved",
        "",
        "Carried through from the existing dossier unchanged. None was resolved here, and "
        "none carries a multiplier.",
        "",
        "| Canonical UPC | States | Legacy | Evidence interpretation |",
        "| --- | --- | --- | --- |",
    ]
    for dossier in summary["conflicts"]["dossier"]:
        states = sorted({str(a["units"]) for a in dossier["conflicting_assertions"]})
        legacy = sorted({str(m["units_per_case"]) for m in dossier["legacy_mappings"]})
        lines.append(
            f"| `{dossier['canonical_upc']}` | {', '.join(states)} | "
            f"{', '.join(legacy) or 'none'} | `{dossier['evidence_interpretation']}` |"
        )
    lines += [
        "",
        "Full dossier, including source rows and why each was classified: "
        "`docs/product-master-commercial-conflicts.md`.",
        "",
        "## What this package does not do",
        "",
        "It makes no business decision, approves nothing, and does not mark any group as "
        "safe. Approving a candidate remains a reviewer action through the Product Master "
        "review workbench, and approving one still changes no EDI output — "
        "`product_case_mappings` remains the authority.",
        "",
    ]
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    from app.core.config import get_settings

    dsn = get_settings().database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    rows = load(dsn)
    rows.sort(key=lambda r: (r["group"], str(r["canonical_upc"]), str(r["pdi_item_code"])))

    conflicts = []
    if CONFLICT_DOSSIER.exists():
        conflicts = json.loads(CONFLICT_DOSSIER.read_text()).get("conflicts", [])

    summary = summarise(rows, conflicts)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    OUTPUT_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str),
                           encoding="utf-8")
    write_markdown(summary, rows)

    print("Product Master — human review package (read-only, decides nothing)\n")
    print(f"  total candidates ................ {summary['total_candidates']}")
    for group in GROUPS:
        print(f"     {group:<28} {summary['group_counts'][group]}")
    ready = summary["ready_for_review"]
    print(f"\n  READY_FOR_REVIEW ................ {ready['total']}")
    print(f"     legacy agrees ................ {ready['legacy_agrees']}")
    print(f"     no legacy mapping ............ {ready['no_legacy_mapping']}")
    print(f"     legacy dissents .............. {ready['legacy_dissents']}")
    print(f"     by basis ..................... {ready['by_commercial_basis']}")
    print(f"     by multiplier ................ {ready['by_multiplier']}")
    print(f"     by store ..................... "
          f"{ {k[:8]: v for k, v in ready['by_store'].items()} }")
    print(f"\n  conflicts (unresolved) .......... {summary['conflicts']['total']}")
    print(f"  decisions made by this report ... {summary['decisions_made_by_this_report']}")
    print(f"\nWrote {OUTPUT_CSV.relative_to(ROOT)},")
    print(f"      {OUTPUT_JSON.relative_to(ROOT)} and {OUTPUT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
