#!/usr/bin/env python
"""
Master product data consolidation — ANALYSIS ONLY.

    python scripts/analyze_product_master.py

Reads every reference workbook under data/reference/ and produces a
reviewable deduplication dataset under analysis/master-data/. It writes
nothing except those output files: no database connection is opened, no
Alembic migration is created, and no source workbook is modified.

Three layers are kept distinct and are never collapsed into one another:

    A. RawSourceRecord        exactly what the cell contained
    B. NormalizedCandidate    comparable values + how they were derived
    C. CanonicalCandidate     one product identity + provenance + conflicts

The normalizations here are only those the corpus itself demonstrates:
Excel's "130.0" for an integer code, scientific notation in the Monarch
and Zink sheets, hyphen groups in "8-51133-00676-2", and the stray space
in "01820096539 5". There is deliberately no zfill and no rule that pads a
short identifier to twelve digits — an 11-digit code is recorded as an
11-digit code, and the 12-digit UPC it *could* correspond to is kept
beside it as evidence for a human, never substituted for it.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.xlsx_reader import read_workbook  # noqa: E402

SOURCE_ROOT = ROOT / "data" / "reference"
OUTPUT_DIR = ROOT / "analysis" / "master-data"
REPORT_PATH = ROOT / "docs" / "master-data-deduplication-report.md"

# Values that are a spreadsheet's way of saying "nothing here".
NULLISH = {"", "nan", "none", "null", "n/a", "-", "totals", "total"}


# ---------------------------------------------------------------------------
# Refuse to run against a database, however it is asked
# ---------------------------------------------------------------------------

def refuse_database_arguments(argv: list[str]) -> None:
    """
    This phase is read-only by construction. If someone hands it database
    configuration or a write flag, stop rather than let the name of the
    flag imply a capability this script does not have.
    """
    forbidden = {"--write", "--commit", "--seed", "--apply", "--db", "--database",
                 "--database-url", "--dsn", "--upsert", "--insert"}
    offending = [a for a in argv if a.split("=")[0].lower() in forbidden]
    if offending:
        sys.exit(
            f"Refusing to run: {' '.join(offending)} implies a database write. "
            "This script is analysis-only — it never opens a database connection. "
            "Remove the flag and re-run."
        )


# ---------------------------------------------------------------------------
# Source specs — written from inspection, never inferred
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnSpec:
    """Where a meaning lives in one specific sheet."""

    identifier: int | None = None            # the primary barcode-like column
    identifier_role: str = "barcode"
    secondary_identifier: int | None = None  # a second barcode column, if any
    secondary_role: str = "barcode"
    item_code: int | None = None             # distributor/house item code
    supplier_id: int | None = None           # supplier's own product id
    supplier_name: int | None = None
    description: tuple[int, ...] = ()        # joined, in order, for the description
    units_per_case: int | None = None
    case_cost: int | None = None
    unit_cost: int | None = None
    pack_hint: int | None = None             # free text like "C24 12OZ 6P"
    header_row: int = 0


# Beer Inventory is one workbook of seven unrelated distributor price
# sheets; each gets its own spec because each was laid out by a different
# supplier. Column positions verified against the actual header rows.
BEER_INVENTORY_SPECS: dict[str, ColumnSpec] = {
    "Sheet1": ColumnSpec(
        identifier=3, identifier_role="barcode", item_code=2,
        description=(0, 1), units_per_case=7, case_cost=5, unit_cost=8, header_row=0,
    ),
    "Sheet2": ColumnSpec(
        identifier=1, identifier_role="barcode", item_code=0,
        description=(4,), units_per_case=3, case_cost=6, unit_cost=8,
        pack_hint=2, header_row=0,
    ),
    "Sheet3": ColumnSpec(
        identifier=1, identifier_role="barcode", item_code=0,
        description=(4,), units_per_case=3, case_cost=6, unit_cost=8,
        pack_hint=2, header_row=0,
    ),
    "Zink - Tiki": ColumnSpec(
        identifier=0, identifier_role="retail_barcode",
        secondary_identifier=2, secondary_role="unit_barcode",
        item_code=1, description=(3, 4), case_cost=7, unit_cost=9,
        pack_hint=5, header_row=1,
    ),
    "Monarch Frontline": ColumnSpec(
        identifier=1, identifier_role="retail_barcode", supplier_id=2,
        description=(0,), case_cost=4, header_row=3,
    ),
    "Monarch Rung": ColumnSpec(
        identifier=2, identifier_role="retail_barcode", supplier_id=0,
        description=(1,), case_cost=6, unit_cost=8, pack_hint=3, header_row=0,
    ),
    "Monarch Package": ColumnSpec(
        identifier=5, identifier_role="retail_barcode", supplier_id=3,
        supplier_name=1, description=(4,), case_cost=9, unit_cost=11,
        pack_hint=6, header_row=3,
    ),
    "Monarch Singles": ColumnSpec(
        identifier=4, identifier_role="retail_barcode", supplier_id=2,
        supplier_name=0, description=(3,), case_cost=8, unit_cost=10,
        pack_hint=5, header_row=0,
    ),
}

# Every Item Sales Summary export shares one layout; the header row moves
# depending on whether the export carried a "Report Date is" line, so it
# is located by its first cell rather than assumed.
ITEM_SALES_SPEC = ColumnSpec(
    identifier=0, identifier_role="scan_code", description=(1,),
    supplier_name=3, unit_cost=9, header_row=-1,
)


# ---------------------------------------------------------------------------
# Layer A — raw source records
# ---------------------------------------------------------------------------

@dataclass
class RawSourceRecord:
    record_id: str
    source_file: str
    source_sheet: str
    source_row: int          # 1-based row number as a person sees it in Excel
    source_type: str
    store_context: str
    source_period: str
    raw_identifier: str | None
    identifier_role: str
    raw_secondary_identifier: str | None
    raw_item_code: str | None
    raw_manufacturer_id: str | None
    raw_manufacturer_name: str | None
    raw_description: str | None
    raw_units_per_case: str | None
    raw_pack_hint: str | None
    raw_case_cost: str | None
    raw_unit_cost: str | None


def _cell(row: list, index: int | None) -> str | None:
    if index is None or index >= len(row):
        return None
    value = row[index]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_nullish(value: str | None) -> bool:
    return value is None or value.strip().lower() in NULLISH


def _join_description(row: list, columns: tuple[int, ...]) -> str | None:
    parts = [_cell(row, c) for c in columns]
    parts = [p for p in parts if not _is_nullish(p)]
    return " ".join(parts) if parts else None


def read_item_sales(path: Path) -> list[RawSourceRecord]:
    """One Item Sales Summary export: store and period come from its preamble."""
    sheets = read_workbook(path)
    records: list[RawSourceRecord] = []
    for sheet_name, sheet in sheets.items():
        header_row = None
        store = ""
        period = ""
        for index, row in enumerate(sheet.rows[:8]):
            first = _cell(row, 0) or ""
            if first.startswith("Store:"):
                store = first.split(":", 1)[1].strip()
            elif first.startswith("Report Date is"):
                period = first.replace("Report Date is", "").strip()
            elif first == "Scan code":
                header_row = index
                break
        if header_row is None:
            continue
        spec = ITEM_SALES_SPEC
        for offset, row in enumerate(sheet.rows[header_row + 1:], start=header_row + 2):
            identifier = _cell(row, spec.identifier)
            description = _join_description(row, spec.description)
            if _is_nullish(identifier) and _is_nullish(description):
                continue
            # "Totals" footer rows are not products.
            if (identifier or "").strip().lower() in {"totals", "total"}:
                continue
            records.append(RawSourceRecord(
                record_id=f"{path.name}|{sheet_name}|{offset}",
                source_file=str(path.relative_to(ROOT)),
                source_sheet=sheet_name,
                source_row=offset,
                source_type="item_sales_summary",
                store_context=store,
                source_period=period,
                raw_identifier=identifier,
                identifier_role=spec.identifier_role,
                raw_secondary_identifier=None,
                raw_item_code=None,
                raw_manufacturer_id=None,
                raw_manufacturer_name=_cell(row, spec.supplier_name),
                raw_description=description,
                raw_units_per_case=None,
                raw_pack_hint=None,
                raw_case_cost=None,
                # Avg Cost is a selling-unit reference cost, not a case cost.
                raw_unit_cost=_cell(row, spec.unit_cost),
            ))
    return records


_DIVISOR = re.compile(r"^[A-Z]+\d+\s*/\s*(\d+(?:\.\d+)?)$")


def _units_from_formula(sheet, row_number: int, column: int | None) -> str | None:
    """
    Pack size as a distributor actually recorded it: the divisor in
    `=I10/6`. Only an explicit divisor counts — a shared-formula marker
    says a formula exists but not what it divided by, and guessing which
    master cell it copied would be invention.
    """
    if column is None:
        return None
    reference = f"{chr(ord('A') + column)}{row_number}"
    formula = sheet.formulas.get(reference)
    if not formula:
        return None
    match = _DIVISOR.match(formula.replace(" ", ""))
    return match.group(1) if match else None


def read_beer_inventory(path: Path, store: str) -> list[RawSourceRecord]:
    sheets = read_workbook(path)
    records: list[RawSourceRecord] = []
    for sheet_name, spec in BEER_INVENTORY_SPECS.items():
        sheet = sheets.get(sheet_name)
        if sheet is None:
            continue
        for offset, row in enumerate(sheet.rows[spec.header_row + 1:], start=spec.header_row + 2):
            identifier = _cell(row, spec.identifier)
            description = _join_description(row, spec.description)
            if _is_nullish(identifier) and _is_nullish(description):
                continue
            units = _cell(row, spec.units_per_case)
            if units is None and spec.unit_cost is not None:
                units = _units_from_formula(sheet, offset, spec.unit_cost)
            records.append(RawSourceRecord(
                record_id=f"{path.name}|{sheet_name}|{offset}",
                source_file=str(path.relative_to(ROOT)),
                source_sheet=sheet_name,
                source_row=offset,
                source_type="distributor_price_sheet",
                store_context=store,
                source_period="",
                raw_identifier=identifier,
                identifier_role=spec.identifier_role,
                raw_secondary_identifier=_cell(row, spec.secondary_identifier),
                raw_item_code=_cell(row, spec.item_code),
                raw_manufacturer_id=_cell(row, spec.supplier_id),
                raw_manufacturer_name=_cell(row, spec.supplier_name),
                raw_description=description,
                raw_units_per_case=units,
                raw_pack_hint=_cell(row, spec.pack_hint),
                raw_case_cost=_cell(row, spec.case_cost),
                raw_unit_cost=_cell(row, spec.unit_cost),
            ))
    return records


def discover_sources() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.xlsx"))


def load_raw_records() -> list[RawSourceRecord]:
    records: list[RawSourceRecord] = []
    for path in discover_sources():
        store = ""
        for part in path.parts:
            if part.startswith("store_"):
                store = part.replace("store_", "")
        if path.name.lower().startswith(("item_sales", "mckinley")):
            records.extend(read_item_sales(path))
        elif "beer inventory" in path.name.lower():
            records.extend(read_beer_inventory(path, store))
    return records


# ---------------------------------------------------------------------------
# Layer B — normalization
# ---------------------------------------------------------------------------

_SCIENTIFIC = re.compile(r"^\d+(?:\.\d+)?[Ee][+-]?\d+$")
_EXCEL_INT = re.compile(r"^(\d+)\.0+$")
_HYPHEN_GROUPS = re.compile(r"^\d+(?:-\d+)+$")
_SPACE_GROUPS = re.compile(r"^\d+(?: \d+)+$")


@dataclass
class NormalizedIdentifier:
    raw: str | None
    normalized: str | None
    kind: str
    flags: list[str] = field(default_factory=list)
    derived_upc12: str | None = None   # evidence only — never substituted


def upc_a_check_digit(digits11: str) -> str:
    """Standard UPC-A check digit over the first eleven digits."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(digits11))
    return str((10 - total % 10) % 10)


def has_valid_upc_check(digits12: str) -> bool:
    return upc_a_check_digit(digits12[:11]) == digits12[11]


def normalize_identifier(raw: str | None, role: str = "barcode") -> NormalizedIdentifier:
    """
    Make an identifier comparable without inventing information.

    Every transformation below is one the corpus forces: Excel writing an
    integer code as "130.0", the Monarch and Zink sheets carrying UPCs as
    floats, hyphen groups in Sheet3, and a stray space in Zink's retail
    column. Nothing is padded, and length alone never promotes a value to
    a UPC.
    """
    flags: list[str] = []
    if raw is None:
        return NormalizedIdentifier(raw, None, "missing", flags)
    text = str(raw).strip()
    if _is_nullish(text):
        return NormalizedIdentifier(raw, None, "missing", flags)

    if _SCIENTIFIC.match(text):
        try:
            number = Decimal(text)
        except InvalidOperation:
            return NormalizedIdentifier(raw, None, "unresolved_non_numeric", flags)
        if number != number.to_integral_value():
            return NormalizedIdentifier(raw, None, "unresolved_non_integer", flags)
        text = str(int(number))
        # Excel's float form cannot carry a leading zero, so a value that
        # arrives this way may be one digit short of its true form. That is
        # recorded, never repaired.
        flags += ["scientific_notation_expanded", "leading_zero_indeterminate"]
    elif (match := _EXCEL_INT.match(text)) is not None:
        text = match.group(1)
        flags.append("excel_integer_decimal_stripped")

    if _HYPHEN_GROUPS.match(text):
        text = text.replace("-", "")
        flags.append("hyphen_groups_removed")
    elif _SPACE_GROUPS.match(text):
        text = text.replace(" ", "")
        flags.append("internal_spaces_removed")

    if not text.isdigit():
        return NormalizedIdentifier(raw, text, "unresolved_non_numeric", flags)
    if set(text) == {"0"}:
        # Kegs in Sheet1 carry 000000000000: a filler, not an identity.
        return NormalizedIdentifier(raw, text, "placeholder_zeros", flags)
    if text.startswith("0"):
        flags.append("leading_zero_present")

    length = len(text)
    if role == "supplier_product_id":
        return NormalizedIdentifier(raw, text, "supplier_product_id", flags)
    if role == "distributor_item_code":
        return NormalizedIdentifier(raw, text, "distributor_item_code", flags)

    if length == 12:
        kind = "upc12" if has_valid_upc_check(text) else "upc12_check_failed"
        return NormalizedIdentifier(raw, text, kind, flags)
    if length == 13:
        return NormalizedIdentifier(raw, text, "ean13", flags)
    if length == 11:
        # The PDI convention (12-digit UPC minus its check digit) would make
        # this an 11-digit item code. That is recorded as a candidate for a
        # human to confirm, in its own field — this value is NOT rewritten.
        return NormalizedIdentifier(
            raw, text, "identifier11", flags, derived_upc12=text + upc_a_check_digit(text),
        )
    if length <= 5:
        return NormalizedIdentifier(raw, text, "short_code", flags)
    return NormalizedIdentifier(raw, text, "unresolved_length", flags)


_PUNCT = re.compile(r"[^A-Z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalize_description(raw: str | None) -> str | None:
    """Comparable form only — the raw description is always kept as well."""
    if _is_nullish(raw):
        return None
    text = _PUNCT.sub(" ", str(raw).upper())
    return _SPACES.sub(" ", text).strip() or None


def normalize_units_per_case(raw: str | None) -> str | None:
    if _is_nullish(raw):
        return None
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return str(int(value)) if value == value.to_integral_value() else str(value.normalize())


@dataclass
class NormalizedCandidate:
    record_id: str
    identifier: NormalizedIdentifier
    secondary_identifier: NormalizedIdentifier
    item_code: NormalizedIdentifier
    manufacturer_id: NormalizedIdentifier
    normalized_description: str | None
    normalized_units_per_case: str | None
    identity_key: str
    identity_basis: str


TRUSTWORTHY = {"upc12", "ean13"}


def build_candidate(record: RawSourceRecord) -> NormalizedCandidate:
    identifier = normalize_identifier(record.raw_identifier, record.identifier_role)
    secondary = normalize_identifier(record.raw_secondary_identifier, "barcode")
    item_code = normalize_identifier(record.raw_item_code, "distributor_item_code")
    manufacturer = normalize_identifier(record.raw_manufacturer_id, "supplier_product_id")

    # Identity comes from a barcode or it does not come at all. An 11-digit
    # code keeps its own namespace: it is a real identity for grouping rows
    # that share it, but it is never merged into a 12-digit UPC here.
    if identifier.kind in TRUSTWORTHY:
        key, basis = f"upc12:{identifier.normalized}", identifier.kind
    elif identifier.kind == "identifier11":
        key, basis = f"id11:{identifier.normalized}", "identifier11"
    elif secondary.kind in TRUSTWORTHY:
        key, basis = f"upc12:{secondary.normalized}", f"secondary_{secondary.kind}"
    elif secondary.kind == "identifier11":
        key, basis = f"id11:{secondary.normalized}", "secondary_identifier11"
    elif identifier.kind == "upc12_check_failed":
        key, basis = f"upc12bad:{identifier.normalized}", "upc12_check_failed"
    elif identifier.kind == "short_code":
        key, basis = f"short:{identifier.normalized}", "short_code"
    else:
        key, basis = f"unresolved:{record.record_id}", identifier.kind

    return NormalizedCandidate(
        record_id=record.record_id,
        identifier=identifier,
        secondary_identifier=secondary,
        item_code=item_code,
        manufacturer_id=manufacturer,
        normalized_description=normalize_description(record.raw_description),
        normalized_units_per_case=normalize_units_per_case(record.raw_units_per_case),
        identity_key=key,
        identity_basis=basis,
    )


# ---------------------------------------------------------------------------
# Layer C — canonical candidates and their conflicts
# ---------------------------------------------------------------------------

@dataclass
class CanonicalCandidate:
    identity_key: str
    identity_namespace: str
    identity_value: str
    identity_basis: str
    identity_trustworthy: bool
    status: str
    source_record_count: int
    source_files: list[str]
    descriptions: list[str]
    description_count: int
    units_per_case_values: list[str]
    manufacturer_ids: list[str]
    item_codes: list[str]
    unit_costs: list[str]
    case_costs: list[str]
    stores: list[str]
    conflicts: list[str]
    derived_upc12: str | None
    normalization_flags: list[str]


def _distinct(values) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value is not None and value not in seen:
            seen.append(value)
    return seen


def consolidate(
    records: list[RawSourceRecord], candidates: list[NormalizedCandidate]
) -> tuple[list[CanonicalCandidate], list[dict], list[dict]]:
    by_id = {r.record_id: r for r in records}
    groups: dict[str, list[NormalizedCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.identity_key].append(candidate)

    canonicals: list[CanonicalCandidate] = []
    conflicts: list[dict] = []

    for key, members in sorted(groups.items()):
        namespace, _, value = key.partition(":")
        sources = [by_id[m.record_id] for m in members]
        descriptions = _distinct(s.raw_description for s in sources)
        units = _distinct(m.normalized_units_per_case for m in members)
        manufacturers = _distinct(m.manufacturer_id.normalized for m in members)
        item_codes = _distinct(m.item_code.normalized for m in members)
        trustworthy = namespace in {"upc12", "id11"}

        found: list[str] = []
        if len(units) > 1:
            found.append("PACK_SIZE_CONFLICT")
        if len(manufacturers) > 1:
            found.append("MANUFACTURER_CONFLICT")
        if len({normalize_description(d) for d in descriptions if d}) > 1:
            found.append("DESCRIPTION_VARIANTS")
        if len(item_codes) > 1:
            found.append("ITEM_CODE_CONFLICT")

        if not trustworthy:
            status = "UNRESOLVED_IDENTITY"
        elif len(members) == 1:
            status = "SINGLE_SOURCE"
        elif found:
            status = "DUPLICATE_WITH_CONFLICT"
        else:
            status = "SAFE_DUPLICATE"

        flags = _distinct(f for m in members for f in m.identifier.flags)
        derived = _distinct(m.identifier.derived_upc12 for m in members)

        canonicals.append(CanonicalCandidate(
            identity_key=key,
            identity_namespace=namespace,
            identity_value=value,
            identity_basis=members[0].identity_basis,
            identity_trustworthy=trustworthy,
            status=status,
            source_record_count=len(members),
            source_files=_distinct(s.source_file for s in sources),
            descriptions=descriptions,
            description_count=len(descriptions),
            units_per_case_values=units,
            manufacturer_ids=manufacturers,
            item_codes=item_codes,
            unit_costs=_distinct(s.raw_unit_cost for s in sources),
            case_costs=_distinct(s.raw_case_cost for s in sources),
            stores=_distinct(s.store_context for s in sources),
            conflicts=found,
            derived_upc12=derived[0] if derived else None,
            normalization_flags=flags,
        ))

        for conflict in found:
            observed = {
                "PACK_SIZE_CONFLICT": units,
                "MANUFACTURER_CONFLICT": manufacturers,
                "DESCRIPTION_VARIANTS": descriptions,
                "ITEM_CODE_CONFLICT": item_codes,
            }[conflict]
            conflicts.append({
                "identity_key": key,
                "conflict_type": conflict,
                "status": status,
                "source_record_count": len(members),
                "observed_values": " || ".join(str(v) for v in observed),
                "source_records": " || ".join(m.record_id for m in members),
            })

    # Cross-namespace evidence: an 11-digit code whose reconstructed check
    # digit lands on a 12-digit UPC that exists elsewhere in the corpus.
    # Reported for review; deliberately NOT merged.
    upc_keys = {c.identity_value for c in canonicals if c.identity_namespace == "upc12"}
    possible: list[dict] = []
    for canonical in canonicals:
        if canonical.identity_namespace == "id11" and canonical.derived_upc12 in upc_keys:
            possible.append({
                "identifier11": canonical.identity_value,
                "derived_upc12": canonical.derived_upc12,
                "match_type": "PDI_CHECK_DIGIT_RECONSTRUCTION",
                "id11_record_count": canonical.source_record_count,
                "id11_descriptions": " || ".join(canonical.descriptions[:3]),
                "upc12_descriptions": " || ".join(
                    next(c.descriptions[:3] for c in canonicals
                         if c.identity_namespace == "upc12"
                         and c.identity_value == canonical.derived_upc12)
                ),
                "auto_merged": "NO — requires human confirmation",
            })
    return canonicals, conflicts, possible


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _flatten(canonical: CanonicalCandidate) -> dict:
    row = asdict(canonical)
    for column in ("source_files", "descriptions", "units_per_case_values", "manufacturer_ids",
                   "item_codes", "unit_costs", "case_costs", "stores", "conflicts",
                   "normalization_flags"):
        row[column] = " || ".join(str(v) for v in row[column])
    return row


def main() -> None:
    refuse_database_arguments(sys.argv[1:])
    print("Master product data consolidation — ANALYSIS ONLY (no database connection)\n")

    files = discover_sources()
    if not files:
        sys.exit(f"No workbooks found under {SOURCE_ROOT}.")
    print(f"Reading {len(files)} workbook(s) under {SOURCE_ROOT.relative_to(ROOT)}/ ...")

    records = load_raw_records()
    candidates = [build_candidate(r) for r in records]
    canonicals, conflicts, possible = consolidate(records, candidates)
    by_record = {c.record_id: c for c in candidates}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    _write_csv(OUTPUT_DIR / "product_master_candidates.csv",
               [_flatten(c) for c in canonicals],
               list(asdict(canonicals[0]).keys()))

    provenance = []
    for record in records:
        candidate = by_record[record.record_id]
        row = asdict(record)
        row.update({
            "identity_key": candidate.identity_key,
            "identity_basis": candidate.identity_basis,
            "normalized_identifier": candidate.identifier.normalized,
            "identifier_kind": candidate.identifier.kind,
            "identifier_flags": " || ".join(candidate.identifier.flags),
            "derived_upc12_candidate": candidate.identifier.derived_upc12 or "",
            "normalized_secondary_identifier": candidate.secondary_identifier.normalized,
            "normalized_item_code": candidate.item_code.normalized,
            "normalized_manufacturer_id": candidate.manufacturer_id.normalized,
            "normalized_description": candidate.normalized_description,
            "normalized_units_per_case": candidate.normalized_units_per_case,
        })
        provenance.append(row)
    _write_csv(OUTPUT_DIR / "product_source_provenance.csv", provenance, list(provenance[0].keys()))

    _write_csv(OUTPUT_DIR / "deduplication_conflicts.csv", conflicts,
               ["identity_key", "conflict_type", "status", "source_record_count",
                "observed_values", "source_records"])

    unresolved = [p for p in provenance
                  if by_record[p["record_id"]].identity_key.startswith(("unresolved:", "short:", "upc12bad:"))]
    _write_csv(OUTPUT_DIR / "unresolved_identifiers.csv", unresolved, list(provenance[0].keys()))

    _write_csv(OUTPUT_DIR / "possible_matches.csv", possible,
               ["identifier11", "derived_upc12", "match_type", "id11_record_count",
                "id11_descriptions", "upc12_descriptions", "auto_merged"])

    summary = build_summary(files, records, candidates, canonicals, conflicts, possible)
    (OUTPUT_DIR / "deduplication_report.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    write_markdown_report(summary, canonicals, conflicts, possible)

    print(f"\nWrote outputs to {OUTPUT_DIR.relative_to(ROOT)}/ and {REPORT_PATH.relative_to(ROOT)}")
    print(f"  raw source records .......... {summary['source_row_counts']['total']}")
    print(f"  canonical candidates ........ {summary['canonical']['total']}")
    print(f"  safe duplicates collapsed ... {summary['canonical']['safe_duplicate']}")
    print(f"  duplicates with conflict .... {summary['canonical']['duplicate_with_conflict']}")
    print(f"  unresolved identities ....... {summary['canonical']['unresolved_identity']}")
    print(f"  possible matches (unmerged) . {len(possible)}")


def build_summary(files, records, candidates, canonicals, conflicts, possible) -> dict:
    kinds = Counter(c.identifier.kind for c in candidates)
    lengths = Counter(len(c.identifier.normalized) for c in candidates if c.identifier.normalized)
    statuses = Counter(c.status for c in canonicals)
    conflict_types = Counter(c["conflict_type"] for c in conflicts)
    per_file = Counter(r.source_file for r in records)
    per_sheet = Counter(f"{r.source_file} :: {r.source_sheet}" for r in records)
    flags = Counter(f for c in candidates for f in c.identifier.flags)

    return {
        "methodology": {
            "layers": ["RawSourceRecord", "NormalizedCandidate", "CanonicalCandidate"],
            "identity_rule": "Barcode-derived only. Descriptions are never an identity.",
            "namespaces": {
                "upc12": "12 digits with a valid UPC-A check digit, or 13-digit EAN",
                "id11": "11 digits — kept separate from upc12, never auto-promoted",
                "upc12bad": "12 digits whose check digit does not validate",
                "short": "1-5 digits (PLU-like); not treated as a barcode",
                "unresolved": "no usable identifier — one bucket per source row",
            },
            "not_implemented_deliberately": [
                "zfill / left-padding of any identifier",
                "treating a short number as a UPC",
                "manufacturer-28476 five-zero reconstruction",
                "automatic merge of id11 into upc12 via check-digit reconstruction",
                "description-based deduplication",
                "choosing a winning description, pack size, or cost",
            ],
        },
        "files_inspected": [str(f.relative_to(ROOT)) for f in files],
        "sheets_inspected": dict(per_sheet),
        "source_row_counts": {"total": len(records), "per_file": dict(per_file)},
        "identifier_kinds": dict(kinds),
        "identifier_length_distribution": {str(k): v for k, v in sorted(lengths.items())},
        "normalization_flags": dict(flags),
        "valid_identifier_counts": {
            "upc12_valid": kinds.get("upc12", 0),
            "ean13": kinds.get("ean13", 0),
            "identifier11": kinds.get("identifier11", 0),
            "upc12_check_failed": kinds.get("upc12_check_failed", 0),
            "short_code": kinds.get("short_code", 0),
            "placeholder_zeros": kinds.get("placeholder_zeros", 0),
            "missing": kinds.get("missing", 0),
        },
        "canonical": {
            "total": len(canonicals),
            "safe_duplicate": statuses.get("SAFE_DUPLICATE", 0),
            "duplicate_with_conflict": statuses.get("DUPLICATE_WITH_CONFLICT", 0),
            "single_source": statuses.get("SINGLE_SOURCE", 0),
            "unresolved_identity": statuses.get("UNRESOLVED_IDENTITY", 0),
            "exact_duplicate_rows_collapsed": len(records) - len(canonicals),
        },
        "conflicts": dict(conflict_types),
        "possible_matches": len(possible),
        "products_with_multiple_descriptions": sum(1 for c in canonicals if c.description_count > 1),
        "products_with_multiple_costs": sum(1 for c in canonicals if len(c.unit_costs) > 1),
        "leading_zero_cases": flags.get("leading_zero_present", 0),
        "eleven_vs_twelve_digit": {
            "eleven": lengths.get(11, 0),
            "twelve": lengths.get(12, 0),
        },
        "suspicious_short_identifiers": kinds.get("short_code", 0),
        "twelve_digit_check_digit_outcome_by_source": analyze_check_digits(records, candidates),
        "eleven_digit_reconstruction_evidence": analyze_reconstruction_evidence(records, candidates),
        "manufacturer_28476": investigate_28476(records, candidates),
    }


def analyze_reconstruction_evidence(records, candidates) -> dict:
    """
    Eleven digits can mean two entirely different things, and the corpus
    says which is which. A value that reached Excel as a float lost its
    leading zero; a Scan code from the sales system had its check digit
    removed. Both are eleven digits long and the repairs are opposites,
    so this measures each hypothesis instead of picking one.
    """
    by_record = {c.record_id: c for c in candidates}
    corpus_upcs = {c.identifier.normalized for c in candidates if c.identifier.kind == "upc12"}

    populations: dict[str, list] = {"scientific_notation": [], "sales_scan_code": [], "other": []}
    for record in records:
        candidate = by_record[record.record_id]
        if candidate.identifier.kind != "identifier11":
            continue
        if "scientific_notation_expanded" in candidate.identifier.flags:
            populations["scientific_notation"].append(candidate)
        elif record.source_type == "item_sales_summary":
            populations["sales_scan_code"].append(candidate)
        else:
            populations["other"].append(candidate)

    def measure(members: list) -> dict:
        total = len(members)
        if not total:
            return {"population": 0}
        prepend_valid = sum(1 for m in members if has_valid_upc_check("0" + m.identifier.normalized))
        prepend_in_corpus = sum(1 for m in members if "0" + m.identifier.normalized in corpus_upcs)
        append_in_corpus = sum(1 for m in members if m.identifier.derived_upc12 in corpus_upcs)
        return {
            "population": total,
            "prepend_zero_passes_upc_check": prepend_valid,
            "prepend_zero_passes_pct": round(100 * prepend_valid / total, 1),
            "prepend_zero_found_in_corpus": prepend_in_corpus,
            "append_check_digit_found_in_corpus": append_in_corpus,
        }

    measured = {name: measure(members) for name, members in populations.items()}
    return {
        "why_this_matters": (
            "Both populations are 11 digits. Left-padding one and check-digit-appending "
            "the other are the correct repairs; applying either rule generically would "
            "corrupt the other population. This is the evidence against a blanket zfill."
        ),
        "populations": measured,
        "interpretation": {
            "scientific_notation": (
                "Excel float representation dropped a leading zero. Prepending '0' "
                "restores a check-digit-valid UPC-A at the rate shown — treat as a "
                "12-digit UPC identity once confirmed."
            ),
            "sales_scan_code": (
                "Consistent with the PDI convention (12-digit UPC minus check digit). "
                "Prepending '0' performs at roughly the ~10% rate random digits would, "
                "i.e. no signal; appending the computed check digit lands on UPCs that "
                "actually exist in the distributor sheets."
            ),
        },
        "false_merge_check": {
            "description": (
                "Whether any id11 group mixes the two populations, which would merge "
                "two different products under one key."
            ),
            "mixed_groups": _count_mixed_id11_groups(records, candidates),
        },
        "rule_status": "NO reconstruction rule is applied. This is evidence for review only.",
    }


def _count_mixed_id11_groups(records, candidates) -> int:
    by_record = {c.record_id: c for c in candidates}
    groups: dict[str, set[str]] = defaultdict(set)
    for record in records:
        candidate = by_record[record.record_id]
        if not candidate.identity_key.startswith("id11:"):
            continue
        groups[candidate.identity_key].add(
            "scientific" if "scientific_notation_expanded" in candidate.identifier.flags else "plain"
        )
    return sum(1 for populations in groups.values() if len(populations) > 1)


def analyze_check_digits(records, candidates) -> dict:
    """12-digit values split cleanly by where they came from; that is a finding."""
    by_record = {c.record_id: c for c in candidates}
    outcome: dict[str, Counter] = defaultdict(Counter)
    for record in records:
        candidate = by_record[record.record_id]
        normalized = candidate.identifier.normalized
        if normalized and len(normalized) == 12:
            outcome[record.source_type][candidate.identifier.kind] += 1
    return {
        source_type: dict(counts) for source_type, counts in outcome.items()
    }


def investigate_28476(records, candidates) -> dict:
    """Section 12: look for the value, report exactly what is there."""
    by_record = {c.record_id: c for c in candidates}
    hits = [r for r in records
            if "28476" in " ".join(str(v) for v in asdict(r).values() if v is not None)]
    manufacturer_ids = [by_record[r.record_id].manufacturer_id.normalized
                        for r in records if by_record[r.record_id].manufacturer_id.normalized]
    numeric = [int(m) for m in manufacturer_ids if m.isdigit()]
    return {
        "records_with_28476_anywhere": len(hits),
        "records_with_manufacturer_id_28476": sum(1 for m in manufacturer_ids if m == "28476"),
        "manufacturer_id_column_present_in": ["Beer Inventory.xlsx :: Monarch * sheets (Product ID)"],
        "manufacturer_id_population": len(manufacturer_ids),
        "manufacturer_id_length_distribution": dict(Counter(len(m) for m in manufacturer_ids)),
        "manufacturer_id_numeric_range": {"min": min(numeric), "max": max(numeric)} if numeric else None,
        "28476_within_observed_range": bool(numeric and min(numeric) <= 28476 <= max(numeric)),
        "conclusion": (
            "No record anywhere in the corpus carries 28476. The rule cannot be "
            "designed or validated from this data — the five-zero reconstruction "
            "remains UNIMPLEMENTED and unproven."
        ),
    }


def write_markdown_report(summary, canonicals, conflicts, possible) -> None:
    lines: list[str] = []
    add = lines.append
    add("# Master Data Deduplication — Analysis Report")
    add("")
    add("**Status: ANALYSIS ONLY. This dataset is NOT ready to seed.** No database was "
        "contacted, no migration exists, and no source workbook was modified. Every "
        "number below describes what the reference corpus contains, not a decision "
        "about it.")
    add("")
    add("## 1. Files and sheets inspected")
    add("")
    add("| File | Sheet | Source rows |")
    add("| --- | --- | --- |")
    for sheet, count in sorted(summary["sheets_inspected"].items()):
        file_part, _, sheet_part = sheet.partition(" :: ")
        add(f"| `{file_part}` | {sheet_part} | {count} |")
    add("")
    add(f"Total raw source records: **{summary['source_row_counts']['total']}**")
    add("")
    add("## 2. Identifier population")
    add("")
    add("| Identifier kind | Count |")
    add("| --- | --- |")
    for kind, count in sorted(summary["identifier_kinds"].items(), key=lambda kv: -kv[1]):
        add(f"| `{kind}` | {count} |")
    add("")
    add("### Length distribution")
    add("")
    add("| Digits | Count |")
    add("| --- | --- |")
    for length, count in sorted(summary["identifier_length_distribution"].items(), key=lambda kv: int(kv[0])):
        add(f"| {length} | {count} |")
    add("")
    add("### Normalizations actually applied")
    add("")
    add("| Flag | Count |")
    add("| --- | --- |")
    for flag, count in sorted(summary["normalization_flags"].items(), key=lambda kv: -kv[1]):
        add(f"| `{flag}` | {count} |")
    add("")
    add("Deliberately **not** implemented: " +
        ", ".join(f"`{x}`" for x in summary["methodology"]["not_implemented_deliberately"]))
    add("")
    add("## 3. Canonical candidates")
    add("")
    for key, value in summary["canonical"].items():
        add(f"- **{key}**: {value}")
    add("")
    add("## 4. Conflicts requiring human review")
    add("")
    add("| Conflict | Count |")
    add("| --- | --- |")
    for kind, count in sorted(summary["conflicts"].items(), key=lambda kv: -kv[1]):
        add(f"| `{kind}` | {count} |")
    add("")
    add("### Highest-risk examples")
    add("")
    risky = [c for c in canonicals if "PACK_SIZE_CONFLICT" in c.conflicts][:5]
    if risky:
        add("**Pack-size conflicts** — one product, disagreeing units-per-case. No winner was chosen:")
        add("")
        for candidate in risky:
            add(f"- `{candidate.identity_key}` — units seen: "
                f"{', '.join(candidate.units_per_case_values)} · "
                f"descriptions: {' | '.join(candidate.descriptions[:2])} · "
                f"sources: {len(candidate.source_files)}")
        add("")
    manufacturer = [c for c in canonicals if "MANUFACTURER_CONFLICT" in c.conflicts][:5]
    if manufacturer:
        add("**Manufacturer conflicts** — same barcode, different supplier product id:")
        add("")
        for candidate in manufacturer:
            add(f"- `{candidate.identity_key}` — ids: {', '.join(candidate.manufacturer_ids)} · "
                f"{' | '.join(candidate.descriptions[:2])}")
        add("")
    described = sorted((c for c in canonicals if c.description_count > 1),
                       key=lambda c: -c.description_count)[:5]
    if described:
        add("**Description variants** — all preserved as aliases; none promoted to canonical:")
        add("")
        for candidate in described:
            add(f"- `{candidate.identity_key}` — {candidate.description_count} descriptions: "
                f"{' | '.join(candidate.descriptions[:4])}")
        add("")
    add("## 5. 11-digit vs 12-digit identifiers")
    add("")
    add(f"- 11-digit: **{summary['eleven_vs_twelve_digit']['eleven']}**")
    add(f"- 12-digit: **{summary['eleven_vs_twelve_digit']['twelve']}**")
    add(f"- Leading-zero cases preserved: **{summary['leading_zero_cases']}**")
    add("")
    add(f"`{len(possible)}` 11-digit codes reconstruct (via UPC-A check digit) onto a "
        "12-digit UPC that exists elsewhere in the corpus. These are reported in "
        "`possible_matches.csv` as **POSSIBLE_MATCH** and were **not merged** — the PDI "
        "11-digit convention is not applied as a generic deduplication rule.")
    add("")
    if possible:
        add("| 11-digit | Reconstructed UPC-12 | 11-digit description | UPC-12 description |")
        add("| --- | --- | --- | --- |")
        for match in possible[:8]:
            add(f"| `{match['identifier11']}` | `{match['derived_upc12']}` | "
                f"{match['id11_descriptions'][:44]} | {match['upc12_descriptions'][:44]} |")
        add("")
    add("### The 11-digit question — two populations, opposite repairs")
    add("")
    evidence = summary["eleven_digit_reconstruction_evidence"]
    add(evidence["why_this_matters"])
    add("")
    add("| Population | Rows | Prepend `0` passes UPC check | Prepend `0` found in corpus | Append check digit found in corpus |")
    add("| --- | --- | --- | --- | --- |")
    for name, stats in evidence["populations"].items():
        if not stats.get("population"):
            continue
        add(f"| {name} | {stats['population']} | "
            f"{stats['prepend_zero_passes_upc_check']} ({stats['prepend_zero_passes_pct']}%) | "
            f"{stats['prepend_zero_found_in_corpus']} | {stats['append_check_digit_found_in_corpus']} |")
    add("")
    for name, text in evidence["interpretation"].items():
        add(f"- **{name}** — {text}")
    add("")
    add(f"- **False-merge check**: `{evidence['false_merge_check']['mixed_groups']}` "
        "`id11` group(s) mix the two populations. "
        f"**{evidence['rule_status']}**")
    add("")
    add("### 12-digit check-digit outcome by source")
    add("")
    add("| Source type | Outcome |")
    add("| --- | --- |")
    for source_type, counts in summary["twelve_digit_check_digit_outcome_by_source"].items():
        add(f"| {source_type} | {counts} |")
    add("")
    add("## 6. Manufacturer 28476 — Identifier Pattern Investigation")
    add("")
    investigation = summary["manufacturer_28476"]
    for key, value in investigation.items():
        add(f"- **{key}**: {value}")
    add("")
    overlap_path = OUTPUT_DIR / "existing_master_overlap.json"
    if overlap_path.exists():
        overlap = json.loads(overlap_path.read_text(encoding="utf-8"))
        add("## 7. Overlap with the master data the application already holds")
        add("")
        add("Read-only counts (see `scripts/compare_master_with_existing.py`). None of these "
            "rows were modified, and nothing in this phase proposes changing them.")
        add("")
        for key, value in overlap["existing_master_data"].items():
            add(f"- **{key}**: {value}")
        add("")
        for key, value in overlap["overlap_with_excel_corpus"].items():
            add(f"- **{key}**: {value}")
        add("")
        add(f"> {overlap['namespace_finding']}")
        add("")
        bridge = overlap["if_check_digit_bridge_were_applied"]
        add(f"If the 11-to-12 digit reconstruction were approved, **{bridge['would_gain_pack_evidence']}** "
            f"governed case mappings would gain independent pack-size evidence from the corpus: "
            f"**{bridge['agrees_with_governed_units']}** agree with the governed units-per-case and "
            f"**{bridge['disagrees_with_governed_units']}** disagree. "
            f"*{overlap['bridge_status']}*")
        add("")

    add("## 8. What needs human review")
    add("")
    add("1. Every `DUPLICATE_WITH_CONFLICT` row in `deduplication_conflicts.csv`.")
    add("2. Every `POSSIBLE_MATCH` in `possible_matches.csv` — confirm or reject the "
        "11-digit to 12-digit relationship before any rule is written.")
    add("3. Pack-size conflicts, which belong in the existing governed case-mapping "
        "proposal workflow rather than being resolved here.")
    add("4. `unresolved_identifiers.csv` — rows with no trustworthy identifier. None of "
        "these were given an identity from their description.")
    add("")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
