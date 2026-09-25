#!/usr/bin/env python
"""
Commercial conflict dossier — READ-ONLY.

    python scripts/report_commercial_conflicts.py

Assembles, for every candidate the system classified CONFLICT, the full set
of assertions that disagree and where each came from. It resolves nothing:
the point is to put the decision in front of a person with the evidence
already gathered, not to pick a winner.

No UPC is named in this script. It reports whatever is in CONFLICT, so it
keeps working for conflicts that do not exist yet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT_JSON = ROOT / "analysis" / "master-data" / "product_master_commercial_conflicts.json"
OUTPUT_MD = ROOT / "docs" / "product-master-commercial-conflicts.md"

# Where the evidence itself admits only one reading, but a decision is
# still required because something outside it dissents.
SINGLE_INTERPRETATION = "EVIDENCE_SUPPORTS_SINGLE_INTERPRETATION"
MULTIPLE_INTERPRETATIONS = "EVIDENCE_SUPPORTS_MULTIPLE_INTERPRETATIONS"


def load(dsn: str) -> list[dict]:
    import psycopg2

    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute("""
            SELECT p.canonical_upc, p.canonical_key, m.pdi_item_code, m.store_id,
                   s.identity_status, m.commercial_unit_basis, m.units_accounted_for,
                   m.cost_basis, m.approval_state, m.evidence, m.source_file,
                   m.source_sheet, m.source_row, p.id
            FROM master_commercial_mappings m
            JOIN master_products p ON p.id = m.product_id
            JOIN stores s ON s.id = m.store_id
            WHERE m.commercial_unit_basis = 'CONFLICT'
            ORDER BY p.canonical_upc
        """)
        conflicts = cursor.fetchall()

        dossiers = []
        for (upc, key, code, store_id, identity_status, basis, units, cost_basis,
             approval_state, evidence, _source_file, _source_sheet, _source_row,
             product_id) in conflicts:
            evidence = evidence or {}
            statements = evidence.get("source_statements") or []

            cursor.execute(
                "SELECT units_per_case, description, source, store_id "
                "FROM product_case_mappings WHERE item_code = %s", (code,))
            legacy = [
                {"units_per_case": u, "description": d, "source": s,
                 "store_id": str(sid) if sid else None}
                for u, d, s, sid in cursor.fetchall()
            ]

            cursor.execute(
                "SELECT child_quantity, composition_basis, evidence "
                "FROM master_pack_compositions WHERE parent_product_id = %s", (product_id,))
            composition = [
                {"child_quantity": q, "basis": b, "evidence": e}
                for q, b, e in cursor.fetchall()
            ]

            distinct_units = {s.get("units") for s in statements if s.get("units") is not None}
            interpretation = (
                SINGLE_INTERPRETATION if len(distinct_units) <= 1
                else MULTIPLE_INTERPRETATIONS
            )

            if len(distinct_units) > 1:
                why = (
                    "Two or more distributor statements assert different sellable-unit "
                    "counts for the same product and store."
                )
                decision = (
                    "Decide which distributor statement describes this product's "
                    "commercial unit; the other is either a different pack or a stale row."
                )
            else:
                why = (
                    "The reference data asserts one count, but an existing governed "
                    "mapping holds a different value, so the commercial question is open."
                )
                decision = (
                    "Decide whether the governed mapping or the distributor statement "
                    "describes how this store accounts for the product in PDI."
                )

            dossiers.append({
                "canonical_upc": upc,
                "canonical_key": key,
                "pdi_item_code": code,
                "store_id": str(store_id),
                "store_identity_status": identity_status,
                "commercial_unit_basis": basis,
                "units_accounted_for": units,
                "cost_basis": cost_basis,
                "approval_state": approval_state,
                "conflicting_assertions": [
                    {
                        "units": s.get("units"),
                        "statement": s.get("statement"),
                        "source_file": s.get("source_file"),
                        "source_sheet": s.get("source_sheet"),
                        "source_row": s.get("source_row"),
                        "source_type": "distributor_reference_sheet",
                    }
                    for s in statements
                ],
                "legacy_mappings": legacy,
                "physical_pack_composition": composition,
                "governed_units_observed": evidence.get("governed_units_observed", []),
                "why_conflict": why,
                "evidence_interpretation": interpretation,
                "decision_required": decision,
                "system_note": evidence.get("notes"),
                "resolved_by_system": False,
            })
    finally:
        connection.close()
    return dossiers


def main() -> None:
    from app.core.config import get_settings

    dsn = get_settings().database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    dossiers = load(dsn)

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps({"read_only": True, "resolved_by_system": False,
                    "conflicts": dossiers}, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    lines = [
        "# Product Master — Commercial Conflicts",
        "",
        "**Status: READ-ONLY. Nothing was resolved.** Each entry below is a decision "
        "waiting for a person; the system deliberately holds no multiplier for any of "
        "them, and approving one requires an explicit interpretation through the review "
        "workbench.",
        "",
        f"Conflicts open: **{len(dossiers)}**",
        "",
    ]
    for dossier in dossiers:
        lines += [
            f"## `{dossier['canonical_upc']}` (PDI item `{dossier['pdi_item_code']}`)",
            "",
            f"- **Store**: `{dossier['store_id'][:8]}` "
            f"(physical identity {dossier['store_identity_status']})",
            f"- **Current state**: `{dossier['commercial_unit_basis']}`, multiplier "
            f"`{dossier['units_accounted_for']}`, `{dossier['approval_state']}`",
            f"- **Evidence interpretation**: `{dossier['evidence_interpretation']}`",
            "",
            "**Conflicting assertions**",
            "",
            "| Units | Source | Sheet | Row |",
            "| --- | --- | --- | --- |",
        ]
        for assertion in dossier["conflicting_assertions"]:
            lines.append(
                f"| {assertion['units']} | {assertion['statement']} | "
                f"{assertion['source_sheet']} | {assertion['source_row']} |"
            )
        lines.append("")
        if dossier["legacy_mappings"]:
            lines += ["**Legacy mapping (current EDI authority)**", "",
                      "| Units/case | Source | Description |", "| --- | --- | --- |"]
            for legacy in dossier["legacy_mappings"]:
                lines.append(
                    f"| {legacy['units_per_case']} | {legacy['source']} | "
                    f"{legacy['description']} |"
                )
            lines.append("")
        else:
            lines += ["**Legacy mapping**: none.", ""]
        if dossier["physical_pack_composition"]:
            counts = ", ".join(
                str(c["child_quantity"]) for c in dossier["physical_pack_composition"])
            lines += [
                f"**Physical pack composition**: contains {counts} unit(s). "
                "This is packaging, not the PDI multiplier, and must not be used to "
                "resolve the conflict.", "",
            ]
        lines += [
            f"**Why the system called it a conflict**: {dossier['why_conflict']}", "",
            f"**Decision required**: {dossier['decision_required']}", "",
        ]

    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Commercial conflicts (read-only): {len(dossiers)}\n")
    for dossier in dossiers:
        units = {a["units"] for a in dossier["conflicting_assertions"]}
        print(f"  {dossier['canonical_upc']}  states={sorted(units)}  "
              f"legacy={[m['units_per_case'] for m in dossier['legacy_mappings']]}  "
              f"{dossier['evidence_interpretation']}")
    print(f"\nWrote {OUTPUT_JSON.relative_to(ROOT)} and {OUTPUT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
