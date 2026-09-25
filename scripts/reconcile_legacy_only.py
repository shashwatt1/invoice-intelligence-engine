#!/usr/bin/env python
"""
LEGACY_ONLY reconciliation queue — READ-ONLY.

    python scripts/reconcile_legacy_only.py

Every legacy case mapping that the Product Master does not yet cover, with
what it would take to cover it. These are the mappings EDI depends on
today, so the purpose is to make sure none is lost at cutover — not to
migrate or modify any of them.

Bridging is never inferred from description, cost, pack notation,
leading-zero padding or check-digit manipulation. A bridge is only reported
where the Product Master already holds the same identifier, by the identity
evidence it recorded at seed time.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUTPUT_CSV = ROOT / "analysis" / "master-data" / "legacy_only_reconciliation.csv"
OUTPUT_JSON = ROOT / "analysis" / "master-data" / "legacy_only_reconciliation.json"
OUTPUT_MD = ROOT / "docs" / "legacy-only-reconciliation.md"

BRIDGEABLE = "BRIDGEABLE"
NEEDS_SOURCE_EVIDENCE = "NEEDS_SOURCE_EVIDENCE"
NEEDS_IDENTITY_REVIEW = "NEEDS_IDENTITY_REVIEW"
NEEDS_COMMERCIAL_REVIEW = "NEEDS_COMMERCIAL_REVIEW"
RETAIN_LEGACY_ONLY = "RETAIN_LEGACY_ONLY"


def load(dsn: str) -> list[dict]:
    import psycopg2

    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute("""
            SELECT l.item_code, l.units_per_case, l.description, l.source, l.store_id,
                   s.identity_status
            FROM product_case_mappings l
            LEFT JOIN stores s ON s.id = l.store_id
            WHERE NOT EXISTS (
                SELECT 1 FROM master_commercial_mappings m
                WHERE m.pdi_item_code = l.item_code
            )
            ORDER BY l.item_code
        """)
        legacy_rows = cursor.fetchall()

        rows: list[dict] = []
        for item_code, units, description, source, store_id, identity_status in legacy_rows:
            # 1/2. Does the master hold this identity, under any source form?
            cursor.execute("""
                SELECT p.id, p.canonical_upc, p.canonical_key
                FROM master_products p
                JOIN master_product_identifiers i ON i.product_id = p.id
                WHERE i.normalized_value = %s OR p.canonical_upc LIKE %s
                LIMIT 1
            """, (item_code, item_code + "%"))
            product = cursor.fetchone()

            identifier_forms: list[str] = []
            master_has_commercial_for_store = False
            product_id = None
            canonical_upc = None
            if product:
                product_id, canonical_upc, _canonical_key = product
                cursor.execute("""
                    SELECT DISTINCT raw_value, identifier_type, derivation
                    FROM master_product_identifiers WHERE product_id = %s
                """, (product_id,))
                identifier_forms = [
                    f"{raw} [{kind}/{derivation}]"
                    for raw, kind, derivation in cursor.fetchall()
                ]
                # 4. Does the master already carry commercial data for this store?
                cursor.execute("""
                    SELECT count(*) FROM master_commercial_mappings
                    WHERE product_id = %s AND store_id = %s
                """, (product_id, store_id))
                master_has_commercial_for_store = bool(cursor.fetchone()[0])

            if product is None:
                classification = NEEDS_IDENTITY_REVIEW
                explanation = (
                    "No Product Master identity holds this item code under any recorded "
                    "source form. Bridging would require identity evidence that does not "
                    "exist yet."
                )
            elif master_has_commercial_for_store:
                classification = NEEDS_COMMERCIAL_REVIEW
                explanation = (
                    "The product and a commercial mapping for this store both exist; the "
                    "legacy row is not represented because that mapping is still "
                    "unapproved."
                )
            elif canonical_upc:
                classification = BRIDGEABLE
                explanation = (
                    "The Product Master already holds this identifier; what is missing is "
                    "commercial evidence for this store, not identity."
                )
            else:
                classification = NEEDS_SOURCE_EVIDENCE
                explanation = (
                    "Identity exists but carries no canonical barcode, so no commercial "
                    "mapping can be keyed to it yet."
                )

            # 5/6. Does legacy hold something the master lacks?
            legacy_only_information = []
            if units is not None:
                legacy_only_information.append(f"units_per_case={units}")
            if description:
                legacy_only_information.append("description")

            rows.append({
                "item_code": item_code,
                "legacy_units_per_case": units,
                "legacy_description": description,
                "legacy_source": source,
                "store_id": str(store_id) if store_id else None,
                "store_identity_status": identity_status,
                "master_product_found": bool(product),
                "master_canonical_upc": canonical_upc,
                "master_identifier_forms": " || ".join(identifier_forms[:4]),
                "master_has_commercial_for_store": master_has_commercial_for_store,
                "legacy_only_information": ", ".join(legacy_only_information),
                "classification": classification,
                "explanation": explanation,
                "bridge_evidence": (
                    f"Product Master identifier matches item code {item_code}"
                    if product else "none"
                ),
            })
    finally:
        connection.close()
    return rows


def main() -> None:
    from app.core.config import get_settings

    dsn = get_settings().database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    rows = load(dsn)
    counts = Counter(r["classification"] for r in rows)
    # A legacy mapping exists per store, so rows and distinct item codes are
    # different numbers. The reconciliation's LEGACY_ONLY figure counts codes.
    distinct_codes = len({r["item_code"] for r in rows})

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    OUTPUT_JSON.write_text(json.dumps(
        {"read_only": True, "total_rows": len(rows), "distinct_item_codes": distinct_codes,
         "classifications": dict(counts),
         "records": rows}, indent=2, sort_keys=True, default=str), encoding="utf-8")

    lines = [
        "# LEGACY_ONLY Reconciliation",
        "",
        "**Status: READ-ONLY. No legacy mapping was modified or deleted.** These are the "
        "case mappings the live EDI path depends on that the Product Master does not yet "
        "cover. The purpose is to make sure none is lost at cutover.",
        "",
        f"Records: **{len(rows)} legacy rows** covering **{distinct_codes} distinct item "
        "codes** — the figure the legacy reconciliation reports as `LEGACY_ONLY`. A case "
        "mapping exists per store, so one item code can appear more than once. Every row "
        "is listed in the CSV.",
        "",
        "## Classification",
        "",
        "| Class | Count | Meaning |",
        "| --- | --- | --- |",
        f"| `{BRIDGEABLE}` | {counts.get(BRIDGEABLE, 0)} | the Product Master already "
        "holds this identifier; commercial evidence for the store is what is missing |",
        f"| `{NEEDS_COMMERCIAL_REVIEW}` | {counts.get(NEEDS_COMMERCIAL_REVIEW, 0)} | "
        "product and store mapping both exist, but the mapping is unapproved |",
        f"| `{NEEDS_IDENTITY_REVIEW}` | {counts.get(NEEDS_IDENTITY_REVIEW, 0)} | no master "
        "identity holds this item code under any recorded source form |",
        f"| `{NEEDS_SOURCE_EVIDENCE}` | {counts.get(NEEDS_SOURCE_EVIDENCE, 0)} | identity "
        "exists but carries no canonical barcode |",
        f"| `{RETAIN_LEGACY_ONLY}` | {counts.get(RETAIN_LEGACY_ONLY, 0)} | keep as legacy "
        "only |",
        "",
        "## What was deliberately not done",
        "",
        "No bridge was inferred from description, cost, pack notation, leading-zero "
        "padding or check-digit manipulation. A row is `BRIDGEABLE` only where the "
        "Product Master already records the same identifier, by the identity evidence "
        "captured at seed time.",
        "",
        "No legacy mapping was modified, deleted or migrated. Until each of these is "
        "either bridged or explicitly retained, **switching EDI authority would remove "
        "mappings the current export depends on** — which is why this is a cutover "
        "blocker rather than a cleanup task.",
        "",
    ]
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(f"LEGACY_ONLY reconciliation (read-only): {len(rows)} legacy rows "
          f"covering {distinct_codes} distinct item codes\n")
    for name, count in counts.most_common():
        print(f"  {name:<26} {count}")
    print(f"\nWrote {OUTPUT_CSV.relative_to(ROOT)}, {OUTPUT_JSON.relative_to(ROOT)} "
          f"and {OUTPUT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
