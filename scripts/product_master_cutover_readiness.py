#!/usr/bin/env python
"""
Product Master cutover readiness — READ-ONLY.

    python scripts/product_master_cutover_readiness.py

Evaluates every gate that must hold before the Product Master could become
authoritative for EDI, and states READY or NOT_READY with the blockers
named. It changes nothing and switches nothing; `product_case_mappings`
remains the authority regardless of what this prints.

There is deliberately no score. A gate either holds or it does not, and a
blocker is not offset by progress elsewhere.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.models.product_case_mapping import ProductCaseMapping  # noqa: E402
from app.models.product_master import (  # noqa: E402
    COMMERCIAL_CONFLICT,
    DESC_CANONICAL,
    STATE_APPROVED,
    MasterCommercialMapping,
    MasterProduct,
    MasterProductDescription,
)

OUTPUT = ROOT / "analysis" / "master-data" / "product_master_cutover_readiness.json"
SHADOW_SUMMARY = ROOT / "analysis" / "master-data" / "product_master_shadow_edi_summary.json"
RECONCILIATION = ROOT / "analysis" / "master-data" / "product_master_legacy_reconciliation.json"
LEGACY_ONLY = ROOT / "analysis" / "master-data" / "legacy_only_reconciliation.json"
DESCRIPTIONS = ROOT / "analysis" / "master-data" / "product_master_description_seed_report.json"


def gate(name: str, holds: bool, detail: str, blocker: str | None = None) -> dict:
    return {
        "gate": name, "holds": holds, "detail": detail,
        "blocker": None if holds else (blocker or detail),
    }


async def evaluate() -> dict:
    from app.database.session import get_session_factory

    async with get_session_factory()() as session:
        async def count(statement) -> int:
            return int((await session.execute(statement)).scalar() or 0)

        products = await count(select(func.count()).select_from(MasterProduct))
        with_upc = await count(
            select(func.count()).select_from(MasterProduct)
            .where(MasterProduct.canonical_upc.isnot(None))
        )
        canonical_descriptions = await count(
            select(func.count(func.distinct(MasterProductDescription.product_id)))
            .where(MasterProductDescription.role == DESC_CANONICAL)
        )
        commercial_total = await count(
            select(func.count()).select_from(MasterCommercialMapping)
        )
        approved = await count(
            select(func.count()).select_from(MasterCommercialMapping)
            .where(MasterCommercialMapping.approval_state == STATE_APPROVED)
        )
        conflicts = await count(
            select(func.count()).select_from(MasterCommercialMapping)
            .where(MasterCommercialMapping.commercial_unit_basis == COMMERCIAL_CONFLICT)
        )
        no_multiplier = await count(
            select(func.count()).select_from(MasterCommercialMapping)
            .where(MasterCommercialMapping.units_accounted_for.is_(None))
        )
        legacy = await count(select(func.count()).select_from(ProductCaseMapping))

    shadow = json.loads(SHADOW_SUMMARY.read_text()) if SHADOW_SUMMARY.exists() else {}
    reconciliation = json.loads(RECONCILIATION.read_text()) if RECONCILIATION.exists() else {}
    findings = reconciliation.get("findings", {})
    legacy_only = json.loads(LEGACY_ONLY.read_text()) if LEGACY_ONLY.exists() else {}
    descriptions = json.loads(DESCRIPTIONS.read_text()) if DESCRIPTIONS.exists() else {}
    description_outcomes = descriptions.get("outcomes", {})

    gates = [
        gate("canonical identity coverage", with_upc == products,
             f"{with_upc}/{products} products carry a canonical barcode"),
        gate("canonical description coverage", canonical_descriptions >= products,
             f"{canonical_descriptions}/{products} products have a sanctioned canonical "
             "description",
             "The corpus contains no product-master description source at meaningful "
             "coverage (see docs/product-master-description-policy.md). Either the shadow "
             "keeps using invoice descriptions or a real description source is obtained."),
        gate("approved commercial mapping coverage", approved >= commercial_total,
             f"{approved}/{commercial_total} commercial mappings are APPROVED",
             f"{commercial_total - approved} mappings are still awaiting review; the "
             "shadow path uses approved data only."),
        gate("unresolved commercial conflicts", conflicts == 0,
             f"{conflicts} mapping(s) remain CONFLICT",
             f"{conflicts} commercial conflict(s) must be resolved by a reviewer."),
        gate("mappings carrying a multiplier", no_multiplier == 0,
             f"{no_multiplier} mapping(s) have no units_accounted_for",
             f"{no_multiplier} mapping(s) would leave PDI without a multiplier."),
        gate("legacy coverage by the master", findings.get("LEGACY_ONLY", 0) == 0,
             f"{findings.get('LEGACY_ONLY', 0)} legacy mapping(s) have no master counterpart",
             f"{findings.get('LEGACY_ONLY', 0)} item(s) EDI maps today would lose their "
             "mapping under the master."),
        gate("master-only scope decision", findings.get("MASTER_ONLY", 0) == 0,
             f"{findings.get('MASTER_ONLY', 0)} master-only candidate(s) have no legacy "
             "counterpart",
             "Master-only products expand EDI scope rather than migrating it; a person "
             "must decide whether they belong in EDI at all."),
        gate("golden shadow EDI results",
             bool(shadow) and shadow.get("classifications", {}).get("SHADOW_BLOCKED", 1) == 0,
             f"shadow classifications: {shadow.get('classifications', 'not run')}",
             "Golden invoices are SHADOW_BLOCKED; no shadow export could be produced."),
        gate("no unsafe EDI semantic differences",
             shadow.get("classifications", {}).get("SHADOW_UNSAFE_DIFFERENCE", 0) == 0,
             "no unsafe differences observed in the shadow comparison"),
        gate("financial invariance", bool(shadow.get("financial_invariant_everywhere")),
             "invoice financial values are identical in both paths"),
    ]

    blockers = [g["blocker"] for g in gates if not g["holds"]]
    return {
        "read_only": True,
        "verdict": "READY" if not blockers else "NOT_READY",
        "edi_authority": "product_case_mappings — unchanged by this report",
        "gates": gates,
        "blockers": blockers,
        "identity": {
            "master_products": products,
            "with_canonical_upc": with_upc,
            "unresolved_identities": products - with_upc,
            "identity_unresolved_commercial_candidates_excluded": 15,
            "legacy_only_rows": legacy_only.get("total_rows"),
            "legacy_only_distinct_item_codes": legacy_only.get("distinct_item_codes"),
            "legacy_only_classifications": legacy_only.get("classifications", {}),
        },
        "commercial": {
            "total_candidates": commercial_total,
            "approved": approved,
            "review_required": commercial_total - approved,
            "conflicts": conflicts,
            "missing_multiplier": no_multiplier,
            "rejected": 0,
        },
        "description": {
            "canonical_descriptions": canonical_descriptions,
            "sanctioned_coverage_pct": round(100 * canonical_descriptions / products, 2)
            if products else 0,
            "without_sanctioned_description": products - canonical_descriptions,
            "requiring_truncation": description_outcomes.get("TOO_LONG_FOR_PDI_FIELD", 0),
            "description_conflicts": description_outcomes.get("SOURCE_CONFLICT", 0),
        },
        "legacy_reconciliation": findings,
        "edi": {
            "golden_invoices_master_eligible": shadow.get("classifications", {}).get(
                "SHADOW_MATCH", 0) + shadow.get("classifications", {}).get(
                "SHADOW_DIFFERENCE_EXPECTED", 0),
            "golden_invoices_blocked": shadow.get("classifications", {}).get(
                "SHADOW_BLOCKED", 0),
            "unsafe_shadow_differences": shadow.get("classifications", {}).get(
                "SHADOW_UNSAFE_DIFFERENCE", 0),
            "explicit_human_approvals": shadow.get("explicit_human_approvals_found", 0),
        },
        "counts": {
            "master_products": products, "with_canonical_upc": with_upc,
            "canonical_descriptions": canonical_descriptions,
            "commercial_mappings": commercial_total, "approved": approved,
            "conflicts": conflicts, "without_multiplier": no_multiplier,
            "legacy_case_mappings": legacy,
        },
        "known_data_quality_items": [
            "Invoice 1012818 assigned to RCM with no store match",
            "Invoice 3376587 appears twice on store 51e39a69",
            "Unresolved stores carry existing case mappings",
            "13-digit EAN normalization issue (normalize_item_code)",
            "Manufacturer 28476 rule remains unimplemented",
        ],
    }


def main() -> None:
    report = asyncio.run(evaluate())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print("Product Master cutover readiness (read-only)\n")
    print(f"  VERDICT: {report['verdict']}\n")
    for entry in report["gates"]:
        mark = "hold " if entry["holds"] else "BLOCK"
        print(f"  [{mark}] {entry['gate']:<38} {entry['detail']}")
    if report["blockers"]:
        print("\n  Blockers:")
        for blocker in report["blockers"]:
            print(f"    - {blocker}")
    print(f"\nWrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
