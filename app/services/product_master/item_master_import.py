"""
Item-master import with preview — app/services/product_master/item_master_import.py

A repeatable way to bring a product list into the Product Master: a store's
PDI/CStorePro item-master export when one is obtained, a vendor catalog, or a
re-run of a workbook already on file. The source's columns are declared in a
small mapping file (scripts/item_master_mappings/*.json) because no item-master
export has been seen yet; nothing here assumes its layout.

Every row is classified first; `apply` writes only what the preview showed:

    NEW               no product with this barcode yet — product, identifiers,
                      description and (when stated) a units-per-case candidate
    EXISTING          the product exists and the row adds nothing new
    POSSIBLE_UPDATE   the product exists; the row adds a wording or a vendor
                      item number (kept as additional assertions, never
                      replacing the canonical description)
    DUPLICATE         the same facts already appeared earlier in this source
    CONFLICT          the row's units-per-case disagrees with an approved
                      value, an open candidate, or another row of this source —
                      reported, nothing about units is written
    INVALID           no usable barcode, a barcode PDI's item code cannot
                      hold, or a units-per-case that is not a whole number
                      from 1 to 9999

Rules:
  * units-per-case enters as a REVIEW_REQUIRED shared candidate, exactly like
    the distributor seed's — a stated items/case is evidence, never an
    approval. A value derived from a formula or a price ratio is not read at
    all: only an explicit units column is;
  * approved values, open candidates and canonical descriptions are never
    overwritten;
  * every written row carries source system, file, sheet and row;
  * a re-run writes nothing it already wrote (identifier, description and
    candidate existence are checked first, on top of the table constraints).
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_master import (
    BASIS_UPC_A,
    DESC_SOURCE,
    ID_SUPPLIER_ITEM_ID,
    STATE_AUTO_MATCHED,
    STATE_REVIEW_REQUIRED,
    MasterCommercialMapping,
    MasterProduct,
    MasterProductDescription,
    MasterProductIdentifier,
)
from app.services.product_master.candidates import normalize_description
from app.services.product_master.identifiers import (
    BARCODE_CHECK_DIGIT_OMITTED,
    BARCODE_FULL,
    REPRESENTATION_EXCEL_FLOAT,
    REPRESENTATION_TEXT,
    SourceProfile,
    canonical_key_for,
    derive_identifier,
)
from app.services.product_master.shared_decisions import basis_for, item_states
from app.services.xlsx_reader import read_workbook

NEW = "NEW"
EXISTING = "EXISTING"
POSSIBLE_UPDATE = "POSSIBLE_UPDATE"
DUPLICATE = "DUPLICATE"
CONFLICT = "CONFLICT"
INVALID = "INVALID"
CATEGORIES = (NEW, EXISTING, POSSIBLE_UPDATE, DUPLICATE, CONFLICT, INVALID)


@dataclass(frozen=True)
class ColumnMapping:
    """Which source column holds which fact, and how the source writes barcodes."""

    source_system: str                       # e.g. "item_master:cstorepro" — recorded on every row
    upc: str
    description: str | None = None
    units_per_case: str | None = None
    vendor_item_code: str | None = None
    vendor: str | None = None
    sheet: str | None = None
    header_row: int = 1                      # 1-based
    barcode_convention: str = BARCODE_FULL   # or CHECK_DIGIT_OMITTED (11-digit PDI codes)
    representation: str = REPRESENTATION_TEXT  # or EXCEL_FLOAT (leading zeros may be lost)

    @classmethod
    def from_file(cls, path: str | Path) -> ColumnMapping:
        data = json.loads(Path(path).read_text())
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        mapping = cls(**data)
        if not mapping.source_system.startswith("item_master:"):
            raise ValueError("source_system must start with 'item_master:' so imported rows are recognizable.")
        if mapping.barcode_convention not in (BARCODE_FULL, BARCODE_CHECK_DIGIT_OMITTED):
            raise ValueError(f"barcode_convention must be {BARCODE_FULL} or {BARCODE_CHECK_DIGIT_OMITTED}.")
        if mapping.representation not in (REPRESENTATION_TEXT, REPRESENTATION_EXCEL_FLOAT):
            raise ValueError(f"representation must be {REPRESENTATION_TEXT} or {REPRESENTATION_EXCEL_FLOAT}.")
        return mapping

    @property
    def profile(self) -> SourceProfile:
        return SourceProfile(name=self.source_system, representation=self.representation,
                             barcode_convention=self.barcode_convention)


@dataclass
class RowResult:
    row: int
    category: str
    reason: str | None = None
    item_code: str | None = None
    upc: str | None = None
    units: int | None = None
    description: str | None = None
    vendor: str | None = None
    vendor_item_code: str | None = None
    raw_upc: str | None = None
    writes: list[str] = field(default_factory=list)


@dataclass
class ImportPreview:
    source_file: str
    sheet: str | None
    source_system: str
    rows: list[RowResult]

    def counts(self) -> dict[str, int]:
        found = Counter(r.category for r in self.rows)
        return {c: found.get(c, 0) for c in CATEGORIES}

    def summary(self) -> dict[str, Any]:
        reasons = Counter(f"{r.category}: {r.reason}" for r in self.rows if r.reason)
        return {"source_file": self.source_file, "sheet": self.sheet, "source_system": self.source_system,
                "rows": len(self.rows), "counts": self.counts(),
                "units_candidates": sum(1 for r in self.rows if "commercial_candidate" in r.writes),
                "reasons": dict(reasons.most_common())}

    def to_dict(self) -> dict[str, Any]:
        return self.summary() | {"detail": [asdict(r) for r in self.rows]}


# ---- reading ----------------------------------------------------------------------

def read_rows(path: str | Path, mapping: ColumnMapping) -> tuple[list[tuple[int, dict[str, str]]], str | None]:
    """(1-based row number, {header: text}) for every non-empty data row, and the sheet read."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            table = list(csv.reader(handle))
        sheet = None
    else:
        sheets = read_workbook(path)
        name = mapping.sheet or next(iter(sheets))
        if name not in sheets:
            raise ValueError(f"sheet {name!r} not found; the workbook has {sorted(sheets)}")
        table = [[(c if c is not None else "") for c in row] for row in sheets[name].rows]
        sheet = name
    header_index = mapping.header_row - 1
    if header_index >= len(table):
        raise ValueError("header_row is past the end of the source")
    header = [str(h).strip() for h in table[header_index]]
    wanted = [c for c in (mapping.upc, mapping.description, mapping.units_per_case,
                          mapping.vendor_item_code, mapping.vendor) if c]
    missing = [c for c in wanted if c not in header]
    if missing:
        raise ValueError(f"columns not found in the header row: {missing}")
    rows: list[tuple[int, dict[str, str]]] = []
    for offset, row in enumerate(table[header_index + 1:], start=header_index + 2):
        values = {header[i]: str(v).strip() for i, v in enumerate(row) if i < len(header)}
        if any(values.get(c) for c in wanted):
            rows.append((offset, values))
    return rows, sheet


def _units(raw: str | None) -> tuple[int | None, str | None]:
    """A stated whole number of units, or why it is not one. Excel's "12.0" is twelve; "12.5" is refused."""
    text = (raw or "").strip()
    if not text:
        return None, None
    try:
        value = float(text)
    except ValueError:
        return None, "units_per_case_not_a_number"
    if value != int(value) or not 1 <= int(value) <= 9999:
        return None, "units_per_case_out_of_range"
    return int(value), None


# ---- classifying -------------------------------------------------------------------

async def preview(session: AsyncSession, path: str | Path, mapping: ColumnMapping) -> ImportPreview:
    """Classify every row against the database. Reads only."""
    source_rows, sheet = read_rows(path, mapping)
    results: list[RowResult] = []
    for number, values in source_rows:
        raw = values.get(mapping.upc) or ""
        derived = derive_identifier(raw, mapping.profile)
        r = RowResult(row=number, category=NEW, raw_upc=raw or None,
                      description=(values.get(mapping.description) or None) if mapping.description else None,
                      vendor=(values.get(mapping.vendor) or None) if mapping.vendor else None,
                      vendor_item_code=(values.get(mapping.vendor_item_code) or None) if mapping.vendor_item_code else None)
        if derived.identity_basis != BASIS_UPC_A or not derived.canonical_upc:
            r.category, r.reason = INVALID, (
                "not_a_pdi_barcode" if derived.canonical_upc else derived.derivation_detail.get("reason", "no_barcode"))
            results.append(r)
            continue
        r.upc, r.item_code = derived.canonical_upc, derived.canonical_upc[:11]
        units, problem = _units(values.get(mapping.units_per_case)) if mapping.units_per_case else (None, None)
        if problem:
            r.category, r.reason = INVALID, problem
        r.units = units
        results.append(r)

    valid = [r for r in results if r.category != INVALID]
    # Within the source: the first row for a code stands; an identical repeat is a
    # duplicate; a repeat stating different units makes every row for it a conflict.
    by_code: dict[str, list[RowResult]] = defaultdict(list)
    for r in valid:
        by_code[r.item_code].append(r)
    for rows in by_code.values():
        stated = {r.units for r in rows if r.units is not None}
        if len(stated) > 1:
            for r in rows:
                r.category, r.reason = CONFLICT, "source_rows_state_different_units"
            continue
        seen: set[tuple] = set()
        for r in rows:
            key = (normalize_description(r.description), r.units, r.vendor_item_code, r.vendor)
            if key in seen:
                r.category, r.reason = DUPLICATE, "repeats_an_earlier_row"
            seen.add(key)

    live = [r for r in valid if r.category not in (CONFLICT, DUPLICATE)]
    codes = sorted({r.item_code for r in live})
    products = {p.canonical_upc: p for p in (await session.execute(
        select(MasterProduct).where(MasterProduct.canonical_upc.in_([c + _check(c) for c in codes]))
    )).scalars().all()} if codes else {}
    product_ids = [p.id for p in products.values()]
    known_descriptions = {(d.product_id, d.normalized_description) for d in (await session.execute(
        select(MasterProductDescription).where(MasterProductDescription.product_id.in_(product_ids))
    )).scalars().all()} if product_ids else set()
    known_ids = {(i.product_id, i.identifier_type, i.raw_value, i.source_system) for i in (await session.execute(
        select(MasterProductIdentifier).where(MasterProductIdentifier.product_id.in_(product_ids))
    )).scalars().all()} if product_ids else set()
    states = await item_states(session, codes) if codes else {}
    own_candidates = {(m.pdi_item_code, m.units_accounted_for) for m in (await session.execute(
        select(MasterCommercialMapping).where(
            MasterCommercialMapping.store_id.is_(None), MasterCommercialMapping.source_system == mapping.source_system,
            MasterCommercialMapping.pdi_item_code.in_(codes)))).scalars().all()} if codes else set()

    planned_products: set[str] = set()
    planned_descriptions: set[tuple[str, str]] = set()
    planned_vendor_ids: set[tuple[str, str]] = set()
    planned_candidates: set[str] = set()
    for r in live:
        product = products.get(r.upc)
        norm = normalize_description(r.description)
        additions: list[str] = []
        if product is None and r.upc not in planned_products:
            r.category = NEW
            additions = ["product", "identifier"]
            planned_products.add(r.upc)
        else:
            r.category = POSSIBLE_UPDATE
        pid = product.id if product is not None else None
        if norm and (r.upc, norm) not in planned_descriptions and (pid is None or (pid, norm) not in known_descriptions):
            additions.append("description")
            planned_descriptions.add((r.upc, norm))
        if r.vendor_item_code and (r.upc, r.vendor_item_code) not in planned_vendor_ids and (
                pid is None or (pid, ID_SUPPLIER_ITEM_ID, r.vendor_item_code, mapping.source_system) not in known_ids):
            additions.append("vendor_item_identifier")
            planned_vendor_ids.add((r.upc, r.vendor_item_code))
        r.writes = additions
        if r.category == POSSIBLE_UPDATE and not additions:
            r.category = EXISTING
        if r.units is None:
            continue
        st = states.get(r.item_code)
        approved = st.shared_units if st else None
        open_values = {c.units_accounted_for for c in (st.open_global_candidates if st else [])
                       if c.units_accounted_for is not None}
        if approved is not None and approved != r.units:
            r.category, r.reason = CONFLICT, f"approved_shared_value_is_{approved}"
        elif open_values - {r.units}:
            r.category, r.reason = CONFLICT, "an_open_candidate_states_different_units"
        elif (approved is None and (r.item_code, r.units) not in own_candidates and r.units not in open_values
              and r.item_code not in planned_candidates):
            r.writes.append("commercial_candidate")
            planned_candidates.add(r.item_code)
            if r.category == EXISTING:
                r.category = POSSIBLE_UPDATE
        if r.category != CONFLICT and st:
            differing = [v for v in st.stores.values() if v.units != r.units and v.override_reason is None]
            if differing:
                r.reason = f"{len(differing)}_store(s)_hold_a_different_value"
    return ImportPreview(source_file=Path(path).name, sheet=sheet, source_system=mapping.source_system, rows=results)


def _check(code: str) -> str:
    from app.services.product_master.identifiers import upc_a_check_digit

    return upc_a_check_digit(code)


# ---- applying -----------------------------------------------------------------------

async def apply(session: AsyncSession, plan: ImportPreview, mapping: ColumnMapping) -> dict[str, int]:
    """Write what the preview listed under `writes`. Flushes; the caller commits (or rolls back a dry run)."""
    written: Counter[str] = Counter()
    observed = datetime.now(UTC)
    products: dict[str, MasterProduct] = {}
    for r in plan.rows:
        if not r.writes:
            continue
        product = products.get(r.upc) or (await session.execute(
            select(MasterProduct).where(MasterProduct.canonical_upc == r.upc))).scalars().first()
        provenance = {"source_system": mapping.source_system, "source_file": plan.source_file,
                      "source_sheet": plan.sheet, "source_row": r.row}
        if product is None:
            derived = derive_identifier(r.raw_upc, mapping.profile)
            product = MasterProduct(canonical_key=canonical_key_for(derived, fallback=r.upc), canonical_upc=r.upc,
                                    identity_basis=BASIS_UPC_A, identity_state=STATE_AUTO_MATCHED)
            session.add(product)
            await session.flush()
            session.add(MasterProductIdentifier(
                product_id=product.id, raw_value=derived.raw_value, normalized_value=derived.normalized_value,
                identifier_type=derived.identifier_type, derivation=derived.derivation,
                derivation_detail=derived.derivation_detail, evidence_state=derived.evidence_state,
                observed_at=observed, **provenance))
            written["product"] += 1
            written["identifier"] += 1
        products[r.upc] = product
        if "description" in r.writes and r.description:
            session.add(MasterProductDescription(
                product_id=product.id, description=r.description[:255],
                normalized_description=normalize_description(r.description)[:255], role=DESC_SOURCE,
                evidence_state=STATE_AUTO_MATCHED, source_system=mapping.source_system,
                source_file=plan.source_file, source_sheet=plan.sheet, source_row=r.row))
            written["description"] += 1
        if "vendor_item_identifier" in r.writes and r.vendor_item_code:
            session.add(MasterProductIdentifier(
                product_id=product.id, raw_value=r.vendor_item_code[:128], normalized_value=r.vendor_item_code[:64],
                identifier_type=ID_SUPPLIER_ITEM_ID, derivation="NONE",
                derivation_detail={"note": "vendor item number as listed; not a crosswalk confirmation"},
                evidence_state=STATE_REVIEW_REQUIRED, source_distributor=(r.vendor or None) and r.vendor[:64],
                observed_at=observed, **provenance))
            written["vendor_item_identifier"] += 1
        if "commercial_candidate" in r.writes and r.units is not None:
            session.add(MasterCommercialMapping(
                product_id=product.id, store_id=None, pdi_item_code=r.item_code,
                commercial_unit_basis=basis_for(r.units), units_accounted_for=r.units,
                approval_state=STATE_REVIEW_REQUIRED, source_snapshot={"stated_units_per_case": r.units},
                evidence={"stated_units_per_case": r.units, "column": mapping.units_per_case,
                          "rule": "explicit units column; never derived from a formula or ratio"} | provenance,
                source_system=mapping.source_system, source_file=plan.source_file, source_sheet=plan.sheet,
                source_row=r.row))
            written["commercial_candidate"] += 1
        await session.flush()
    return dict(written)


__all__ = ["CATEGORIES", "ColumnMapping", "ImportPreview", "apply", "preview", "read_rows"]
