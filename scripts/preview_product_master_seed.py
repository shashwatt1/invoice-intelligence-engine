#!/usr/bin/env python
"""
Product Master seed PREVIEW — READ-ONLY.

    python scripts/preview_product_master_seed.py
    python scripts/preview_product_master_seed.py --store 47708760 --verbose
    python scripts/preview_product_master_seed.py --output-dir /tmp/preview

Reads the reference workbooks, resolves every identifier through its own
source profile, builds Product Master candidates, and writes them where a
person can review them. **It never writes to PostgreSQL.** There is no
`--write` flag and any write-shaped flag is refused, because the point of
this phase is to see what would be seeded before anything is.

The workbook readers are reused from scripts/analyze_product_master.py so
that the preview and the earlier analysis cannot drift on how a sheet is
laid out. What is new here is the resolution: the analysis deliberately
recorded raw forms without repairing them, while this builds the canonical
identities the Product Master would hold.

Output is deterministic — the same workbooks produce byte-identical files,
so a reviewer can diff two runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.product_master.candidates import (  # noqa: E402
    MasterSourceRow,
    build_candidates,
    identifier_rows,
    product_rows,
)
from app.services.product_master.identifiers import (  # noqa: E402
    DISTRIBUTOR_WORKBOOK,
    ITEM_SALES_SUMMARY,
    SourceProfile,
)
from app.services.product_master.stores import (  # noqa: E402
    StoreIdentifierIndex,
    load_store_identifier_index,
    resolve_commercial_candidates,
)
from scripts.analyze_product_master import (  # noqa: E402
    BEER_INVENTORY_SPECS,
    SOURCE_ROOT,
    discover_sources,
    load_raw_records,
)

DEFAULT_OUTPUT_DIR = ROOT / "analysis" / "master-data"

# Flags that would imply persistence. Refused rather than ignored, so a
# mistaken invocation stops instead of silently previewing.
FORBIDDEN_FLAGS = {
    "--write", "--commit", "--seed", "--apply", "--persist", "--insert",
    "--upsert", "--db", "--database", "--database-url", "--dsn", "--execute",
}

# Which sheets carry a supplier's own product id rather than a barcode.
SUPPLIER_ID_SHEETS = {"Monarch Frontline", "Monarch Rung", "Monarch Package", "Monarch Singles"}


def refuse_write_flags(argv: list[str]) -> None:
    offending = [a for a in argv if a.split("=")[0].lower() in FORBIDDEN_FLAGS]
    if offending:
        sys.exit(
            f"Refusing to run: {' '.join(offending)} implies a database write. "
            "This preview never writes to PostgreSQL — there is no --write flag. "
            "Remove it and re-run."
        )


def profile_for(record) -> SourceProfile:
    """
    The source profile that explains this row's identifier formats.

    Item Sales scan codes omit the check digit; distributor workbooks write
    barcodes as Excel numbers and lose the leading zero. Choosing by source
    rather than by digit length is the whole architecture.
    """
    if record.source_type == "item_sales_summary":
        return ITEM_SALES_SUMMARY
    return DISTRIBUTOR_WORKBOOK


def to_master_rows(records, store_filter: str | None) -> list[MasterSourceRow]:
    """Adapt the analysed source records into Product Master inputs."""
    rows: list[MasterSourceRow] = []
    for record in records:
        if store_filter and record.store_context != store_filter:
            continue
        profile = profile_for(record)
        spec = BEER_INVENTORY_SPECS.get(record.source_sheet)

        # A Monarch "Product ID" is the supplier's code, not a barcode, so
        # it is resolved under a supplier-id profile.
        supplier_id = None
        if record.source_sheet in SUPPLIER_ID_SHEETS:
            supplier_id = record.raw_manufacturer_id

        rows.append(MasterSourceRow(
            source_system=record.source_type,
            profile=profile,
            source_file=record.source_file,
            source_sheet=record.source_sheet,
            source_row=record.source_row,
            raw_identifier=record.raw_identifier,
            raw_unit_identifier=record.raw_secondary_identifier,
            raw_supplier_id=supplier_id,
            description=record.raw_description,
            package_notation=record.raw_pack_hint,
            # An items/case column or a unit-cost divisor is a statement
            # about sellable units, i.e. the PDI multiplier.
            commercial_units_statement=record.raw_units_per_case,
            commercial_statement_evidence=(
                f"{record.source_sheet} units/case column or unit-cost divisor"
                if record.raw_units_per_case else None
            ),
            store_context=record.store_context or None,
            case_cost=record.raw_case_cost,
            unit_cost=record.raw_unit_cost,
        ))
        if spec is None and record.source_type != "item_sales_summary":
            continue
    return rows


def _load_store_index() -> tuple[StoreIdentifierIndex, str | None]:
    """
    Load store_identifiers read-only. A database that is simply absent must
    not fail the preview — it reports every candidate as unknown instead.
    """
    try:
        from app.core.config import get_settings

        dsn = get_settings().database_url_sync.replace(
            "postgresql+psycopg2://", "postgresql://")
        return load_store_identifier_index(dsn), None
    except Exception as exc:  # noqa: BLE001 — absence of a database is not an error here
        return StoreIdentifierIndex([]), type(exc).__name__


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    refuse_write_flags(sys.argv[1:])
    parser = argparse.ArgumentParser(
        description="Preview the Product Master seed. Never writes to PostgreSQL.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--store", default=None, help="Limit to one store context, e.g. 47708760.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print("Product Master seed PREVIEW — READ-ONLY (no database connection)\n")
    files = discover_sources()
    if not files:
        sys.exit(f"No workbooks found under {SOURCE_ROOT}.")
    print(f"Reading {len(files)} workbook(s) under {SOURCE_ROOT.relative_to(ROOT)}/")

    records = load_raw_records()
    rows = to_master_rows(records, args.store)
    print(f"Source rows considered: {len(rows)}"
          + (f" (store {args.store})" if args.store else ""))

    graph = build_candidates(rows)
    counts = graph.counts()

    # Source-store resolution: an exact store_identifiers lookup, read-only.
    # A store whose physical identity is unconfirmed still resolves — the two
    # facts are reported separately and never substituted for one another.
    store_index, store_lookup_error = _load_store_index()
    store_tally = resolve_commercial_candidates(graph.commercial_candidates, store_index)

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    products = product_rows(graph)
    _write_csv(output / "product_master_candidates.csv", products, list(products[0].keys()))

    identifiers = identifier_rows(graph)
    _write_csv(output / "product_master_identifiers.csv", identifiers, list(identifiers[0].keys()))

    conflicts = sorted(graph.conflicts, key=lambda c: (c["category"], c["canonical_key"]))
    _write_csv(output / "product_master_conflicts.csv", conflicts,
               ["category", "canonical_key", "detail", "observed", "source_rows"])

    commercial = sorted(
        graph.commercial_candidates,
        key=lambda c: (c["canonical_key"], c["source_file"], c["source_sheet"], c["source_row"]),
    )
    if commercial:
        _write_csv(output / "product_master_commercial_candidates.csv", commercial,
                   list(commercial[0].keys()))

    compositions = sorted(
        graph.pack_compositions,
        key=lambda c: (c["parent_canonical_key"], c["source_file"], c["source_row"]),
    )
    if compositions:
        _write_csv(output / "product_master_pack_compositions.csv", compositions,
                   list(compositions[0].keys()))

    summary = {
        "read_only": True,
        "wrote_to_database": False,
        "source_workbooks": [str(f.relative_to(ROOT)) for f in files],
        "store_filter": args.store,
        "source_rows_considered": len(rows),
        "counts": counts,
        "pack_compositions": len(compositions),
        "commercial_candidates": len(commercial),
        "store_resolution": store_tally,
        "store_identifier_rows_loaded": len(store_index),
        "store_lookup_error": store_lookup_error,
        "architecture_notes": {
            "identity": "resolved identifiers only; descriptions are never identity",
            "derivation": "chosen by source profile, never by identifier length",
            "pack_vs_commercial": (
                "package contents and the PDI multiplier are separate records "
                "built from separate evidence"
            ),
            "conflicts": "surfaced, never auto-resolved",
            "store_resolution": (
                "exact store_identifiers lookup only; source-store resolution and "
                "physical-store identification are separate fields"
            ),
        },
    }
    (output / "product_master_seed_preview.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )

    print()
    print(f"  canonical product candidates .... {counts['products']}")
    for state, count in sorted(counts["products_by_identity_state"].items()):
        print(f"     {state:<20} {count}")
    print(f"  identifiers ..................... {counts['identifiers']}")
    print(f"  descriptions (aliases) .......... {counts['descriptions']}")
    print(f"  pack compositions ............... {len(compositions)}")
    print(f"  commercial candidates ........... {len(commercial)}")
    print(f"  conflicts ....................... {counts['conflicts']}")
    for category, count in sorted(counts["conflicts_by_category"].items()):
        print(f"     {category:<28} {count}")
    print()
    print(f"  STORE RESOLUTION (store_identifiers lookup, {len(store_index)} rows loaded)")
    print(f"     source-store resolved ........ {store_tally['source_store_resolved']}"
          f" / {store_tally['total']}")
    print(f"     source-store unknown ......... {store_tally['source_store_unknown']}")
    print(f"     ambiguous .................... {store_tally['ambiguous']}")
    print(f"     no source code ............... {store_tally['no_source_code']}")
    print(f"     physical identity CONFIRMED .. {store_tally['physical_identity_confirmed']}")
    print(f"     physical identity unresolved . {store_tally['physical_identity_unresolved']}")
    if store_lookup_error:
        print(f"     (store lookup unavailable: {store_lookup_error})")

    if args.verbose:
        print("\n  sample resolved identifiers:")
        for identifier in identifiers[:8]:
            print(f"     {identifier['raw_value']!r:>20} -> {identifier['normalized_value']} "
                  f"[{identifier['identifier_type']}] via {identifier['derivation_chain']}")

    print(f"\nWrote preview to {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}/")
    print("Nothing was written to PostgreSQL.")


if __name__ == "__main__":
    main()
