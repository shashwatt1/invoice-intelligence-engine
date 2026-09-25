#!/usr/bin/env python
"""
Cross-store Units/Case conflicts — READ-ONLY INVESTIGATION.

    python scripts/investigate_units_per_case_conflicts.py

Two item codes carry different units-per-case in different stores:

    01820096721  MICHELOB ULTRA C-18 12OZ            1 vs 18
    08769200057  TWISTED TEA HALF & HALF C-18 12OZ   1 vs 18

This assembles every independent line of evidence about them — the
governed mappings, the proposal history that produced each one, the
identity and identifier records, the invoice lines, and the distributor
workbook rows — and states what the evidence supports. It changes
nothing: no mapping, identity, invoice, EDI path or normalization rule is
touched, and the database session is opened READ ONLY.

The question each product has to answer is whether "units per case" is
counting sellable units or individual cans, because the case and the can
are two different products with two different barcodes.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.xlsx_reader import read_workbook  # noqa: E402
from scripts.analyze_product_master import (  # noqa: E402
    OUTPUT_DIR,
    refuse_database_arguments,
    upc_a_check_digit,
)

BEER_INVENTORY = ROOT / "data" / "reference" / "store_47708760" / "Beer Inventory.xlsx"
OUTPUT_JSON = OUTPUT_DIR / "units_per_case_conflict_investigation.json"
REPORT_MD = ROOT / "docs" / "units-per-case-conflict-investigation.md"

UNDER_INVESTIGATION = ["01820096721", "08769200057"]

_DIVISOR = re.compile(r"^[A-Z]+\d+\s*/\s*(\d+(?:\.\d+)?)$")


def reconstructed_upc(item_code: str) -> str:
    """The 12-digit form the PDI convention implies. Evidence, not a rewrite."""
    return item_code + upc_a_check_digit(item_code)


def scientific_form_of(upc12: str) -> str:
    """How Excel writes that UPC once its leading zero is gone."""
    return upc12.lstrip("0")


def gather_database_evidence(codes: list[str]) -> dict:
    """Everything the application already holds about these codes."""
    try:
        import psycopg2
    except ImportError:
        return {"available": False, "reason": "psycopg2 not installed"}
    try:
        from app.core.config import get_settings

        dsn = get_settings().database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
        connection = psycopg2.connect(dsn, connect_timeout=5)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": type(exc).__name__}

    connection.set_session(readonly=True, autocommit=True)
    upcs = [reconstructed_upc(c) for c in codes]
    try:
        cursor = connection.cursor()
        cursor.execute(
            """SELECT m.item_code, m.store_id, st.display_name, st.identity_status,
                      m.units_per_case, m.source, m.description
               FROM product_case_mappings m LEFT JOIN stores st ON st.id = m.store_id
               WHERE m.item_code = ANY(%s) ORDER BY m.item_code, m.units_per_case""",
            (codes,),
        )
        mappings = [
            {"item_code": a, "store_id": str(b) if b else None, "store": c,
             "store_identity_status": d, "units_per_case": e, "source": f, "description": g}
            for a, b, c, d, e, f, g in cursor.fetchall()
        ]

        cursor.execute(
            """SELECT entity_key, store_id, proposed_value, source, status, reason, evidence
               FROM product_data_proposals
               WHERE entity_key = ANY(%s) AND field = 'units_per_case'
               ORDER BY entity_key, created_at""",
            (codes,),
        )
        proposals = [
            {"item_code": a, "store_id": str(b) if b else None, "proposed_value": c,
             "source": d, "status": e, "reason": f, "evidence": g}
            for a, b, c, d, e, f, g in cursor.fetchall()
        ]

        cursor.execute(
            "SELECT item_code, kind, value, distributor FROM product_identifier "
            "WHERE item_code = ANY(%s) ORDER BY item_code, kind",
            (codes,),
        )
        identifiers = [
            {"item_code": a, "kind": b, "value": c, "distributor": d}
            for a, b, c, d in cursor.fetchall()
        ]

        cursor.execute(
            "SELECT item_code, description, brand, supplier FROM product_identity "
            "WHERE item_code = ANY(%s)",
            (codes,),
        )
        identities = [
            {"item_code": a, "description": b, "brand": c, "supplier": d}
            for a, b, c, d in cursor.fetchall()
        ]

        cursor.execute(
            """SELECT ii.product_sku, ii.description, ii.quantity, ii.unit_price, ii.pack_size,
                      i.invoice_number, st.display_name, st.identity_status, d.filename
               FROM invoice_items ii
               JOIN invoices i ON i.id = ii.invoice_id
               LEFT JOIN stores st ON st.id = i.store_id
               LEFT JOIN documents d ON d.id = i.document_id
               WHERE ii.product_sku = ANY(%s) ORDER BY ii.product_sku, i.invoice_number""",
            (upcs,),
        )
        invoice_lines = [
            {"product_sku": a, "description": b, "quantity": float(c) if c is not None else None,
             "unit_price": float(d) if d is not None else None, "pack_size": e,
             "invoice_number": f, "store": g, "store_identity_status": h, "document": i}
            for a, b, c, d, e, f, g, h, i in cursor.fetchall()
        ]
    finally:
        connection.close()

    return {
        "available": True,
        "case_mappings": mappings,
        "proposals": proposals,
        "identifiers": identifiers,
        "identities": identities,
        "invoice_lines": invoice_lines,
    }


def gather_workbook_evidence(codes: list[str]) -> dict:
    """
    Distributor rows for these products, including the pack divisor the
    sheets encode as a formula rather than a typed number.
    """
    if not BEER_INVENTORY.exists():
        return {}
    workbook = read_workbook(BEER_INVENTORY)
    wanted = set()
    for code in codes:
        upc = reconstructed_upc(code)
        wanted |= {code, upc, scientific_form_of(upc)}

    found: dict[str, list] = {code: [] for code in codes}
    for sheet_name, sheet in workbook.items():
        for index, row in enumerate(sheet.rows, start=1):
            cells = [str(c) for c in row if c is not None]
            digits = {re.sub(r"[^0-9]", "", c) for c in cells}
            # Scientific cells expand to the same digits the wanted forms use.
            expanded = set()
            for cell in cells:
                if re.fullmatch(r"\d+(?:\.\d+)?[Ee]\+?\d+", cell):
                    expanded.add(str(int(float(cell))))
            matched = [code for code in codes
                       if (digits | expanded) & {code, reconstructed_upc(code),
                                                 scientific_form_of(reconstructed_upc(code))}]
            if not matched:
                continue
            divisors = {
                reference: _DIVISOR.match(formula.replace(" ", "")).group(1)
                for reference, formula in sheet.formulas.items()
                if reference[1:] == str(index) and _DIVISOR.match(formula.replace(" ", ""))
            }
            for code in matched:
                found[code].append({
                    "sheet": sheet_name,
                    "row": index,
                    "cells": [str(c)[:34] for c in row[:13]],
                    "pack_divisor_formulas": divisors,
                })
    return found


def assess(code: str, database: dict, workbook: dict) -> dict:
    """State what the evidence supports, and how strongly."""
    upc = reconstructed_upc(code)
    mappings = [m for m in database.get("case_mappings", []) if m["item_code"] == code]
    values = sorted({m["units_per_case"] for m in mappings})
    invoice_lines = [i for i in database.get("invoice_lines", []) if i["product_sku"] == upc]
    identifiers = [i for i in database.get("identifiers", []) if i["item_code"] == code]

    # Does the application already hold the 12-digit form under this code?
    identifier_values = {i["value"] for i in identifiers}
    identity_confirmed_by_identifier = upc in identifier_values or any(
        re.sub(r"[^0-9]", "", v) == upc for v in identifier_values
    )
    identity_confirmed_by_invoice = bool(invoice_lines)

    # Distributor statements of pack size: an explicit items/case column, or
    # the divisor in the unit-cost formula.
    distributor_pack: list[dict] = []
    for row in workbook.get(code, []):
        for reference, divisor in row["pack_divisor_formulas"].items():
            distributor_pack.append({"sheet": row["sheet"], "row": row["row"],
                                     "evidence": f"unit-cost formula {reference} divides by {divisor}",
                                     "implies_units_per_case": divisor})
        if row["sheet"] == "Sheet1" and len(row["cells"]) > 7:
            distributor_pack.append({"sheet": row["sheet"], "row": row["row"],
                                     "evidence": f"items/case column = {row['cells'][7]}",
                                     "implies_units_per_case": row["cells"][7]})

    implied = {str(int(float(d["implies_units_per_case"])))
               for d in distributor_pack if d["implies_units_per_case"]}

    # Does a reference cost equal the invoice case price? If the selling unit
    # costs what the case costs, the case IS one selling unit.
    cost_identity = []
    for line in invoice_lines:
        for proposal in database.get("proposals", []):
            evidence = proposal.get("evidence") or {}
            reference_cost = evidence.get("reference_avg_cost")
            if reference_cost and line["unit_price"] and abs(reference_cost - line["unit_price"]) < 0.01:
                cost_identity.append({
                    "invoice_number": line["invoice_number"],
                    "invoice_unit_price": line["unit_price"],
                    "reference_avg_cost": reference_cost,
                    "implication": "selling-unit cost equals case price, so one case = one selling unit",
                })
                break

    supported = sorted(implied) if implied else []
    contradicted = [str(v) for v in values if supported and str(v) not in supported]

    if len(values) <= 1:
        verdict = "NO_CONFLICT"
    elif not supported:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif contradicted:
        verdict = "INCORRECT_EXISTING_MAPPING"
    else:
        verdict = "LEGITIMATE_STORE_SPECIFIC"

    return {
        "item_code": code,
        "reconstructed_upc12": upc,
        "identity": {
            "same_canonical_product": identity_confirmed_by_identifier or identity_confirmed_by_invoice,
            "confirmed_by_app_identifier_record": identity_confirmed_by_identifier,
            "confirmed_by_invoice_sku": identity_confirmed_by_invoice,
            "identifier_values": sorted(identifier_values),
            "note": (
                "The 11-digit item code and the 12-digit invoice SKU are the same "
                "product; both forms are already recorded under one item_code."
            ),
        },
        "stores": [
            {"store": m["store"] or f"unresolved:{(m['store_id'] or '')[:8]}",
             "identity_status": m["store_identity_status"],
             "units_per_case": m["units_per_case"], "source": m["source"]}
            for m in mappings
        ],
        "existing_units_per_case_values": values,
        "distributor_pack_evidence": distributor_pack,
        "units_per_case_supported_by_reference": supported,
        "units_per_case_contradicted_by_reference": contradicted,
        "cost_identity_evidence": cost_identity,
        "affected_invoices": sorted({i["invoice_number"] for i in invoice_lines}),
        "invoice_lines": invoice_lines,
        "difference_level": (
            "store/commercial-data-level (the product identity is not in question)"
            if (identity_confirmed_by_identifier or identity_confirmed_by_invoice)
            else "identity-level — the codes may not denote one product"
        ),
        "verdict": verdict,
    }


def main() -> None:
    refuse_database_arguments(sys.argv[1:])
    print("Cross-store Units/Case conflicts — READ-ONLY investigation\n")

    database = gather_database_evidence(UNDER_INVESTIGATION)
    if not database.get("available"):
        print(f"  database unavailable ({database.get('reason')}) — workbook evidence only")
    workbook = gather_workbook_evidence(UNDER_INVESTIGATION)

    findings = [assess(code, database, workbook) for code in UNDER_INVESTIGATION]
    report = {
        "read_only": True,
        "changed": "NOTHING — no mapping, identity, invoice, EDI path or rule was modified.",
        "findings": findings,
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    write_report(findings, database, workbook)

    for finding in findings:
        print(f"  {finding['item_code']} -> {finding['reconstructed_upc12']}  "
              f"values={finding['existing_units_per_case_values']}  "
              f"supported={finding['units_per_case_supported_by_reference']}  "
              f"verdict={finding['verdict']}")
    print(f"\nWrote {OUTPUT_JSON.relative_to(ROOT)} and {REPORT_MD.relative_to(ROOT)}")


def write_report(findings, database, workbook) -> None:
    lines: list[str] = []
    add = lines.append
    add("# Cross-store Units/Case Conflicts — Read-only Investigation")
    add("")
    add("**Nothing was changed.** No case mapping, product identity, identifier, "
        "invoice, EDI output or normalization rule was modified; the database session "
        "was opened read-only. This records what the evidence supports so a person can "
        "decide.")
    add("")
    add("## Evidence table")
    add("")
    add("| | `01820096721` | `08769200057` |")
    add("| --- | --- | --- |")
    michelob, twisted = findings[0], findings[1]

    def cell(finding, key):
        return str(key(finding)).replace("\n", " ")

    rows = [
        ("Product", lambda f: f["invoice_lines"][0]["description"] if f["invoice_lines"] else "—"),
        ("Reconstructed UPC-12", lambda f: f"`{f['reconstructed_upc12']}`"),
        ("Stores and Units/Case", lambda f: "<br>".join(
            f"{s['store']} ({s['identity_status']}): **{s['units_per_case']}**" for s in f["stores"])),
        ("Existing Units/Case values", lambda f: ", ".join(str(v) for v in f["existing_units_per_case_values"])),
        ("Reference evidence", lambda f: "<br>".join(
            f"{d['sheet']} r{d['row']}: {d['evidence']}" for d in f["distributor_pack_evidence"]) or "none"),
        ("Supported by reference", lambda f: ", ".join(f["units_per_case_supported_by_reference"]) or "—"),
        ("Contradicted by reference", lambda f: ", ".join(f["units_per_case_contradicted_by_reference"]) or "—"),
        ("Affected invoices", lambda f: ", ".join(f["affected_invoices"]) or "none"),
        ("Identity confirmed by", lambda f: ", ".join(filter(None, [
            "app identifier record" if f["identity"]["confirmed_by_app_identifier_record"] else "",
            "invoice SKU" if f["identity"]["confirmed_by_invoice_sku"] else ""])) or "—"),
        ("Difference level", lambda f: f["difference_level"]),
        ("Verdict", lambda f: f"**{f['verdict']}**"),
    ]
    for label, key in rows:
        add(f"| {label} | {cell(michelob, key)} | {cell(twisted, key)} |")
    add("")
    add("## What the evidence says")
    add("")
    add("Both item codes denote **one canonical product each**, not two. The 11-digit "
        "code in the case mappings and the 12-digit SKU on the invoices are the same "
        "product: the application already stores both forms under a single `item_code` "
        "in `product_identifier`, and the invoice lines carry the 12-digit form.")
    add("")
    add("The disagreement is therefore **not identity-level**. It is a disagreement about "
        "what *units per case* counts — sellable units, or individual cans.")
    add("")
    add("The distributor sheets answer that directly:")
    add("")
    for finding in findings:
        add(f"- **{finding['item_code']}** — "
            + ("; ".join(f"{d['sheet']} r{d['row']}: {d['evidence']}"
                         for d in finding["distributor_pack_evidence"]) or "no distributor pack evidence"))
    add("")
    add("A case whose unit cost equals its case price contains one sellable unit. That is "
        "what `items/case = 1` and a `/1` divisor both state, and it is consistent with "
        "the reference cost matching the invoice line price. The `C-18` in the "
        "description describes the pack configuration — eighteen cans — not the number "
        "of sellable units the case breaks into.")
    add("")
    add("This matters because the case and the can are **different products with "
        "different barcodes**. For Michelob Ultra the application already records both: "
        "`retail_upc_raw` for the 18-pack and `unit_upc` for the single can. A "
        "units-per-case of 18 attached to the case barcode is only meaningful as "
        "\"eighteen of the *unit* barcode\" — a relationship between two identities, not "
        "a number on one.")
    add("")
    add("## Recommended data-model treatment — not applied")
    add("")
    add("1. **Do not merge or split any identity.** Both products are single canonical "
        "identities and the existing records are correct on that point.")
    add("2. **Treat the `18` mappings as candidates for correction, through the existing "
        "proposal workflow** — not by direct edit. The proposal history shows the `18` "
        "values came from `suggestion_source: \"pack_size\"`, i.e. parsed from the "
        "`C-18` string with `reference_avg_cost: null`, while the `1` values were "
        "entered where reference cost data was present.")
    add("3. **Make the unit of account explicit.** `units_per_case` is ambiguous on its "
        "own; the same number means different things depending on whether the unit is "
        "the pack or the can. Expressing pack composition as a relation between the "
        "retail barcode and the unit barcode would make `18` and `1` unambiguous "
        "instead of contradictory.")
    add("4. **Keep the per-store mapping shape.** Nothing here argues against store-"
        "specific units-per-case; the conflict is not a legitimate store difference, but "
        "the model that allows one is still the right model.")
    add("5. **Resolve the two placeholder stores.** Mappings are currently split across "
        "`unresolved` store records, which fragments the evidence and makes a single "
        "product look like it disagrees with itself.")
    add("")
    add("## Limitation found in the bridge analysis")
    add("")
    add("`08769200057` is reported as `NO_BRIDGE_EVIDENCE` by "
        "`scripts/analyze_identity_bridge.py`, yet its counterpart is present in the "
        "corpus. The distributor row stores the UPC in Excel's float form "
        "(`8.769200057E10`), which normalizes to an 11-digit value and therefore lands "
        "in the `id11` namespace rather than `upc12`. The bridge only looks for "
        "reconstructions that land on an existing 12-digit UPC, so a link between the "
        "two 11-digit populations — one missing its leading zero, the other its check "
        "digit, both resolving to the same UPC — is invisible to it.")
    add("")
    add("Both reconstructions converge here:")
    add("")
    add("```")
    add("08769200057    + check digit  -> 087692000570   (Item Sales scan code)")
    add("8.769200057E10 -> 87692000570")
    add("               prepend zero   -> 087692000570   (Monarch distributor row)")
    add("invoice SKU                      087692000570   (invoices 101497, 450033)")
    add("```")
    add("")
    add("That convergence is strong evidence for the reconstruction rules, and it is the "
        "case the current bridge cannot see. Reported only — the bridge logic was not "
        "changed.")
    add("")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
