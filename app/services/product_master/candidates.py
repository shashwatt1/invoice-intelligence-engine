"""
Product Master candidate construction — app/services/product_master/candidates.py

Builds reviewable Product Master candidates from resolved source rows, and
reports what it could not decide.

Three rules govern everything here:

1. Identity comes from a resolved identifier, never from a description.
   Two rows with the same wording and no barcode stay two candidates.

2. Physical pack composition and the PDI commercial multiplier are
   different facts and are produced by different evidence. A distributor's
   `items/case` column and unit-cost divisor state the commercial unit; a
   package notation beside a unit barcode states what is physically inside.
   Neither is derived from the other.

3. Disagreement is an output, not something to resolve. Nothing is chosen
   because it appeared more often.

Pure and database-free: this constructs candidates, it does not persist
them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from app.models.product_master import (
    BASIS_UNRESOLVED,
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    COMMERCIAL_UNKNOWN,
    DESC_SOURCE,
    PACK_PACKAGE_NOTATION,
    STATE_AUTO_MATCHED,
    STATE_CONFLICT,
    STATE_REVIEW_REQUIRED,
    STATE_UNRESOLVED,
)
from app.services.product_master.identifiers import (
    ROLE_SUPPLIER_ID,
    DerivedIdentifier,
    SourceProfile,
    canonical_key_for,
    derive_identifier,
)

# Conflict categories surfaced to a reviewer.
CONFLICT_DESCRIPTION = "DESCRIPTION_CONFLICT"
CONFLICT_PACK_COMPOSITION = "PACK_COMPOSITION_CONFLICT"
CONFLICT_COMMERCIAL_UNIT = "COMMERCIAL_UNIT_CONFLICT"
CONFLICT_IDENTIFIER = "IDENTIFIER_CONFLICT"
CONFLICT_AMBIGUOUS_IDENTITY = "AMBIGUOUS_IDENTITY"
CONFLICT_UNRESOLVED_IDENTITY = "UNRESOLVED_IDENTITY"

_PUNCT = re.compile(r"[^A-Z0-9 ]+")
_SPACES = re.compile(r"\s+")
# "18/12 CAN" and "C18 12OZ" both state eighteen. Only these explicit
# package-column forms are read; free-text descriptions are not parsed.
_PACK_SLASH = re.compile(r"^(\d{1,3})\s*/")
_PACK_PREFIXED = re.compile(r"^[A-Z]\s?(\d{1,3})\b")


def normalize_description(raw: str | None) -> str | None:
    if not raw or not str(raw).strip():
        return None
    text = _PUNCT.sub(" ", str(raw).upper())
    return _SPACES.sub(" ", text).strip() or None


def parse_package_count(notation: str | None) -> int | None:
    """
    The unit count an explicit package column states, or None.

    Only the two notations the corpus actually uses are read, and only from
    a package column — never from a product description.
    """
    if not notation:
        return None
    text = str(notation).strip().upper()
    for pattern in (_PACK_SLASH, _PACK_PREFIXED):
        match = pattern.match(text)
        if match:
            count = int(match.group(1))
            if 1 <= count <= 999:
                return count
    return None


@dataclass
class MasterSourceRow:
    """One reference-data row, with the profile that explains its formats."""

    source_system: str
    profile: SourceProfile
    source_file: str
    source_sheet: str
    source_row: int
    raw_identifier: str | None = None
    raw_unit_identifier: str | None = None
    raw_supplier_id: str | None = None
    description: str | None = None
    package_notation: str | None = None
    # A distributor's own statement of how many sellable units a case breaks
    # into — an items/case column, or the divisor in its unit-cost formula.
    commercial_units_statement: str | None = None
    commercial_statement_evidence: str | None = None
    store_context: str | None = None
    case_cost: str | None = None
    unit_cost: str | None = None


@dataclass
class ProductCandidate:
    canonical_key: str
    identity_basis: str
    canonical_upc: str | None
    identity_state: str
    identifiers: list[dict] = field(default_factory=list)
    descriptions: list[dict] = field(default_factory=list)
    source_rows: list[str] = field(default_factory=list)
    stores: set[str] = field(default_factory=set)


@dataclass
class CandidateGraph:
    products: dict[str, ProductCandidate] = field(default_factory=dict)
    pack_compositions: list[dict] = field(default_factory=list)
    commercial_candidates: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)

    def counts(self) -> dict:
        states: dict[str, int] = defaultdict(int)
        for product in self.products.values():
            states[product.identity_state] += 1
        categories: dict[str, int] = defaultdict(int)
        for conflict in self.conflicts:
            categories[conflict["category"]] += 1
        return {
            "products": len(self.products),
            "products_by_identity_state": dict(states),
            "identifiers": sum(len(p.identifiers) for p in self.products.values()),
            "descriptions": sum(len(p.descriptions) for p in self.products.values()),
            "pack_compositions": len(self.pack_compositions),
            "commercial_candidates": len(self.commercial_candidates),
            "conflicts": len(self.conflicts),
            "conflicts_by_category": dict(categories),
            "unresolved_identities": states.get(STATE_UNRESOLVED, 0),
        }


def _supplier_profile(profile: SourceProfile) -> SourceProfile:
    """The same source, read as a supplier-id column rather than a barcode."""
    return SourceProfile(
        name=profile.name, representation=profile.representation,
        barcode_convention=profile.barcode_convention, role=ROLE_SUPPLIER_ID,
        distributor=profile.distributor,
    )


def _row_reference(row: MasterSourceRow) -> str:
    return f"{row.source_file}|{row.source_sheet}|{row.source_row}"


def build_candidates(rows: list[MasterSourceRow]) -> CandidateGraph:
    """Construct the candidate graph. Deterministic for a given input."""
    graph = CandidateGraph()

    for row in rows:
        reference = _row_reference(row)
        primary = derive_identifier(row.raw_identifier, row.profile)
        key = canonical_key_for(primary, fallback=reference)

        product = graph.products.get(key)
        if product is None:
            product = ProductCandidate(
                canonical_key=key,
                identity_basis=primary.identity_basis,
                canonical_upc=primary.canonical_upc,
                identity_state=(
                    STATE_AUTO_MATCHED if primary.resolves_identity else STATE_UNRESOLVED
                ),
            )
            graph.products[key] = product

        product.source_rows.append(reference)
        if row.store_context:
            product.stores.add(row.store_context)
        _record_identifier(product, primary, row)

        # A unit barcode on the same row is a different product. It is
        # resolved in its own right, never folded into the parent.
        unit_product = None
        if row.raw_unit_identifier:
            unit = derive_identifier(row.raw_unit_identifier, row.profile)
            unit_key = canonical_key_for(unit, fallback=f"{reference}#unit")
            unit_product = graph.products.get(unit_key)
            if unit_product is None:
                unit_product = ProductCandidate(
                    canonical_key=unit_key,
                    identity_basis=unit.identity_basis,
                    canonical_upc=unit.canonical_upc,
                    identity_state=(
                        STATE_AUTO_MATCHED if unit.resolves_identity else STATE_UNRESOLVED
                    ),
                )
                graph.products[unit_key] = unit_product
            unit_product.source_rows.append(reference)
            _record_identifier(unit_product, unit, row)

        if row.raw_supplier_id:
            # A supplier's own product id is not a barcode, so it is resolved
            # under a supplier-role profile. Deriving it under the row's
            # barcode profile would classify it by length instead of by what
            # the column actually is.
            supplier = derive_identifier(row.raw_supplier_id, _supplier_profile(row.profile))
            if supplier.normalized_value:
                _record_identifier(product, supplier, row, supplier_scoped=True)

        _record_description(product, row)
        _record_pack_composition(graph, product, unit_product, row)
        _record_commercial_candidate(graph, product, row)

    _detect_conflicts(graph)
    return graph


def _record_identifier(
    product: ProductCandidate, derived: DerivedIdentifier, row: MasterSourceRow,
    *, supplier_scoped: bool = False,
) -> None:
    entry = {
        "raw_value": derived.raw_value,
        "normalized_value": derived.normalized_value,
        "identifier_type": derived.identifier_type,
        "derivation": derived.derivation,
        "derivation_detail": derived.derivation_detail,
        "evidence_state": derived.evidence_state,
        "source_system": row.source_system,
        "source_distributor": row.profile.distributor,
        "source_file": row.source_file,
        "source_sheet": row.source_sheet,
        "source_row": row.source_row,
        "supplier_scoped": supplier_scoped,
    }
    # One row asserting the same thing twice adds nothing.
    signature = (entry["raw_value"], entry["identifier_type"], entry["source_system"],
                 entry["source_file"], entry["source_sheet"], entry["source_row"])
    for existing in product.identifiers:
        if (existing["raw_value"], existing["identifier_type"], existing["source_system"],
                existing["source_file"], existing["source_sheet"],
                existing["source_row"]) == signature:
            return
    product.identifiers.append(entry)


def _record_description(product: ProductCandidate, row: MasterSourceRow) -> None:
    normalized = normalize_description(row.description)
    if not normalized:
        return
    for existing in product.descriptions:
        if (existing["normalized_description"] == normalized
                and existing["source_system"] == row.source_system):
            existing["observed_count"] += 1
            return
    product.descriptions.append({
        "description": str(row.description).strip()[:255],
        "normalized_description": normalized[:255],
        "role": DESC_SOURCE,
        "evidence_state": STATE_AUTO_MATCHED,
        "source_system": row.source_system,
        "source_file": row.source_file,
        "source_sheet": row.source_sheet,
        "source_row": row.source_row,
        "observed_count": 1,
    })


def _record_pack_composition(
    graph: CandidateGraph, parent: ProductCandidate,
    child: ProductCandidate | None, row: MasterSourceRow,
) -> None:
    """
    Record what is physically inside the package — only where the row
    already carries all three facts: a parent barcode, a child barcode and
    an explicit package-column count. No inference from descriptions.
    """
    count = parse_package_count(row.package_notation)
    if count is None or child is None:
        return
    if parent.canonical_key == child.canonical_key:
        return
    graph.pack_compositions.append({
        "parent_canonical_key": parent.canonical_key,
        "parent_canonical_upc": parent.canonical_upc,
        "child_canonical_key": child.canonical_key,
        "child_canonical_upc": child.canonical_upc,
        "child_quantity": count,
        "composition_basis": PACK_PACKAGE_NOTATION,
        "evidence_state": STATE_REVIEW_REQUIRED,
        "evidence": {"package_notation": row.package_notation},
        "source_system": row.source_system,
        "source_file": row.source_file,
        "source_sheet": row.source_sheet,
        "source_row": row.source_row,
    })


def _record_commercial_candidate(
    graph: CandidateGraph, product: ProductCandidate, row: MasterSourceRow,
) -> None:
    """
    Record how many sellable units a case breaks into, when a distributor
    stated it. This is the PDI multiplier, not the package contents.
    """
    if row.commercial_units_statement is None:
        return
    try:
        units = int(float(str(row.commercial_units_statement)))
    except (TypeError, ValueError):
        return
    if units < 1:
        return
    graph.commercial_candidates.append({
        "canonical_key": product.canonical_key,
        "canonical_upc": product.canonical_upc,
        "pdi_item_code": (product.canonical_upc or "")[:-1] if product.canonical_upc else None,
        "units_accounted_for": units,
        "commercial_unit_basis": (
            COMMERCIAL_CASE_IS_SELLING_UNIT if units == 1 else COMMERCIAL_UNKNOWN
        ),
        # Never auto-approved: a wrong multiplier silently corrupts Case
        # Retail in PDI.
        "approval_state": STATE_REVIEW_REQUIRED,
        "store_context": row.store_context,
        "store_id_resolved": False,
        "case_cost": row.case_cost,
        "evidence": {"statement": row.commercial_statement_evidence,
                     "raw_value": row.commercial_units_statement},
        "source_system": row.source_system,
        "source_file": row.source_file,
        "source_sheet": row.source_sheet,
        "source_row": row.source_row,
    })


def _detect_conflicts(graph: CandidateGraph) -> None:
    """Surface every disagreement. Nothing is resolved here."""
    for key, product in graph.products.items():
        if product.identity_basis == BASIS_UNRESOLVED:
            graph.conflicts.append({
                "category": CONFLICT_UNRESOLVED_IDENTITY,
                "canonical_key": key,
                "detail": "no identifier resolved to a canonical form",
                "observed": "",
                "source_rows": " || ".join(product.source_rows[:6]),
            })
            continue

        distinct_descriptions = {d["normalized_description"] for d in product.descriptions}
        if len(distinct_descriptions) > 1:
            graph.conflicts.append({
                "category": CONFLICT_DESCRIPTION,
                "canonical_key": key,
                "detail": "sources spell this product differently; none was promoted",
                "observed": " || ".join(sorted(distinct_descriptions)[:6]),
                "source_rows": " || ".join(product.source_rows[:6]),
            })

        # Two different canonical values of the same identifier type under
        # one identity means the resolution disagreed with itself.
        by_type: dict[str, set[str]] = defaultdict(set)
        for identifier in product.identifiers:
            if identifier["normalized_value"] and not identifier["supplier_scoped"]:
                by_type[identifier["identifier_type"]].add(identifier["normalized_value"])
        for id_type, values in by_type.items():
            if len(values) > 1:
                graph.conflicts.append({
                    "category": CONFLICT_IDENTIFIER,
                    "canonical_key": key,
                    "detail": f"multiple {id_type} values resolved under one identity",
                    "observed": " || ".join(sorted(values)),
                    "source_rows": " || ".join(product.source_rows[:6]),
                })

    by_parent: dict[str, set[int]] = defaultdict(set)
    for composition in graph.pack_compositions:
        by_parent[composition["parent_canonical_key"]].add(composition["child_quantity"])
    for parent, quantities in by_parent.items():
        if len(quantities) > 1:
            graph.conflicts.append({
                "category": CONFLICT_PACK_COMPOSITION,
                "canonical_key": parent,
                "detail": "sources disagree on what the package physically contains",
                "observed": ", ".join(str(q) for q in sorted(quantities)),
                "source_rows": "",
            })

    by_scope: dict[tuple[str, str | None], set[int]] = defaultdict(set)
    for candidate in graph.commercial_candidates:
        by_scope[(candidate["canonical_key"], candidate["store_context"])].add(
            candidate["units_accounted_for"]
        )
    for (key, store), values in by_scope.items():
        if len(values) > 1:
            graph.conflicts.append({
                "category": CONFLICT_COMMERCIAL_UNIT,
                "canonical_key": key,
                "detail": f"sources disagree on the PDI multiplier for store {store or '—'}",
                "observed": ", ".join(str(v) for v in sorted(values)),
                "source_rows": "",
            })

    # Mark products whose own evidence disagrees, without deciding for them.
    conflicted = {c["canonical_key"] for c in graph.conflicts
                  if c["category"] in {CONFLICT_IDENTIFIER, CONFLICT_PACK_COMPOSITION}}
    for key in conflicted:
        product = graph.products.get(key)
        if product is not None and product.identity_state == STATE_AUTO_MATCHED:
            product.identity_state = STATE_CONFLICT


def product_rows(graph: CandidateGraph) -> list[dict]:
    """Flatten products for CSV output, deterministically ordered."""
    rows = []
    for key in sorted(graph.products):
        product = graph.products[key]
        data = asdict(product)
        data["stores"] = " || ".join(sorted(product.stores))
        data["identifier_count"] = len(product.identifiers)
        data["description_count"] = len(product.descriptions)
        data["source_row_count"] = len(product.source_rows)
        data["descriptions"] = " || ".join(
            sorted({d["description"] for d in product.descriptions})
        )[:500]
        data.pop("identifiers", None)
        data["source_rows"] = " || ".join(product.source_rows[:8])
        rows.append(data)
    return rows


def identifier_rows(graph: CandidateGraph) -> list[dict]:
    rows = []
    for key in sorted(graph.products):
        for identifier in graph.products[key].identifiers:
            entry = dict(identifier)
            entry["canonical_key"] = key
            entry["derivation_chain"] = " -> ".join(
                identifier["derivation_detail"].get("derivation_chain", []) or ["NONE"]
            )
            entry.pop("derivation_detail", None)
            rows.append(entry)
    return rows
