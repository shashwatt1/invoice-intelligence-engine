"""
Beer Inventory Import — app/services/beer_inventory_import.py

Parses the store's "Beer Inventory.xlsx" into source-aware reference
records. Pure: reads the workbook, returns dataclasses. No database.

The workbook is not one dataset. It is three distributors' price lists
(Zink, Monarch x4) and three hand-built Testani worksheets, pasted
together, each with its own header row, columns and meaning. Each sheet
therefore has its own parser below. Nothing is flattened: every record
says which sheet and row it came from, what the source called the
figure, and how any items-per-case number was arrived at.

UPC spellings seen in this one file, all normalised to the 11-digit key
the formatter and the mapping table use:

    018200001154        plain 12-digit
    8-51133-00676-2     dashed
    01820096539 5       11 digits, a space, the check digit
    1.8200250002E10     Excel ate the leading zero — must zero-pad to 12
                        before dropping the check digit, or the wrong 11
                        digits survive

Items per case
--------------
Three different strengths of evidence, kept apart on the record:

  stated   — Sheet1/2/3 have a typed `items/case` cell.
  package  — Zink prints "24/12OZ 2/12 CANS": 24 cans in 2 twelve-packs.
             The LAST fraction's numerator is the sellable-pack count.
  ratio    — Monarch computes unit cost as `=promo/4`; the divisor is
             their items per case. Recovered as case_cost / unit_cost.

Where a Monarch row's formula divisor and its own case/unit ratio
disagree, someone hand-edited a cell after the formula was set. Those
rows are marked conflicted and carry both numbers; neither is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.models.product_reference import (
    BASIS_FRONTLINE,
    BASIS_PRICE_CHANGE,
    BASIS_PROMO,
    DERIVED_FROM_PACKAGE,
    DERIVED_FROM_RATIO,
    KIND_DISTRIBUTOR_ITEM_CODE,
    KIND_DISTRIBUTOR_PRODUCT_ID,
    KIND_RETAIL_UPC_RAW,
    KIND_UNIT_UPC,
)
from app.services.export_service import normalize_item_code
from app.services.xlsx_reader import Sheet, read_workbook

DISTRIBUTOR_TESTANI = "Testani"
DISTRIBUTOR_ZINK = "Zink"
DISTRIBUTOR_MONARCH = "Monarch"

# Excel serial date origin.
_EXCEL_EPOCH = date(1899, 12, 30)
_NULL = frozenset({"", "nan", "none", "null", "-", "#div/0!"})


@dataclass(frozen=True)
class Identifier:
    kind: str
    value: str
    distributor: str | None


@dataclass
class ReferenceRecord:
    """One source row, fully described."""

    sheet: str
    row: int                       # 1-based, as Excel shows it
    distributor: str
    raw_identifier: str | None
    item_code: str | None          # normalised; None when the row has no usable UPC
    description: str | None
    brand: str | None = None
    supplier: str | None = None
    product_class: str | None = None
    package: str | None = None
    pricing_basis: str = BASIS_PRICE_CHANGE
    case_cost: Decimal | None = None
    previous_case_cost: Decimal | None = None
    unit_cost: Decimal | None = None
    unit_retail: Decimal | None = None       # Item Sales "Avg Price"; not in Beer Inventory
    items_per_case_stated: int | None = None
    items_per_case_derived: int | None = None
    items_per_case_derivation: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    is_conflicted: bool = False
    conflict_detail: dict[str, Any] | None = None
    identifiers: list[Identifier] = field(default_factory=list)


@dataclass
class ParseReport:
    records: list[ReferenceRecord]
    per_sheet: dict[str, int]
    skipped_no_upc: int
    conflicted: int

    @property
    def with_upc(self) -> list[ReferenceRecord]:
        return [r for r in self.records if r.item_code]


# ---------------------------------------------------------------------------
# cell helpers
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _NULL else text


def _money(value: Any) -> Decimal | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError):
        return None


def _int(value: Any) -> int | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def normalize_upc(raw: Any) -> str | None:
    """
    The workbook's four UPC spellings -> our 11-digit key.

    Scientific notation is the dangerous one: Excel has already dropped
    the leading zero, so the integer must be zero-padded back to 12
    digits before the check digit is removed.
    """
    text = _clean(raw)
    if text is None or text == "000000000000":
        return None
    if re.fullmatch(r"[\d.]+E[+-]?\d+", text, re.IGNORECASE):
        digits = str(int(float(text))).zfill(12)
        return normalize_item_code(digits)
    return normalize_item_code(text)


def _excel_date(value: Any) -> date | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        serial = float(text)
        if 30000 < serial < 80000:
            return _EXCEL_EPOCH + timedelta(days=int(serial))
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        m, d, y = (int(x) for x in match.groups())
        return date(y, m, d)
    return None


def _effective_from_preamble(sheet: Sheet, up_to_row: int) -> date | None:
    for row in sheet.rows[:up_to_row]:
        for cell in row:
            text = _clean(cell) or ""
            match = re.search(r"Effective\s+(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
            if match:
                return _excel_date(match.group(1))
    return None


def _header_text(cell: Any) -> str:
    """Header cells arrive with embedded newlines ("Product \nID"); one token."""
    return re.sub(r"\s+", " ", _clean(cell) or "").strip()


def _find_header(sheet: Sheet, anchor: str) -> int | None:
    for i, row in enumerate(sheet.rows):
        if any(_header_text(c) == anchor for c in row):
            return i
    return None


def _columns(sheet: Sheet, header_index: int) -> dict[str, int]:
    return {
        _header_text(c): i
        for i, c in enumerate(sheet.rows[header_index])
        if _clean(c)
    }


# ---------------------------------------------------------------------------
# items-per-case decoding
# ---------------------------------------------------------------------------

_TWO_FRACTIONS = re.compile(
    r"(?<!\d)\d{1,3}\s*/\s*[\d.]+.*?(?<!\d)(\d{1,3})\s*/\s*\d{1,3}"
)


def items_per_case_from_package(package: str | None) -> int | None:
    """
    Read the sellable-pack count out of a package descriptor — but ONLY
    from the one form the data proves reliable.

    "24/12OZ 2/12 CANS" — 24 cans shipped as 2 twelve-packs. The second
    fraction is packs/units-per-pack and its numerator is the number of
    sellable units. Checked against Zink's own case/unit arithmetic on
    every such row in the workbook: 378 of 378 agree.

    Everything else is deliberately NOT decoded:

      "18/12OZ CANS"   — one fraction. The 18 counts containers, and
                         whether the store sells 18 singles or one
                         18-pack is a per-product fact: Zink's own
                         arithmetic says 1 for this row, 15 for a
                         "15/25OZ CANS" row. 48 of 176 single-fraction
                         rows disagree with the numerator.
      "C24 12OZ 6P"    — Monarch's suffix is inconsistent: "6P" on a
                         24-case means six-packs (÷4) on one row, while
                         "4P" means ÷24 (per can) on another.

    For those, the package string is kept as printed and the source's
    own case_cost / unit_cost ratio carries the units-per-case meaning.
    """
    text = _clean(package)
    if text is None:
        return None
    match = _TWO_FRACTIONS.search(text)
    if not match:
        return None
    numerator = int(match.group(1))
    return numerator if 1 <= numerator <= 999 else None


def _ratio(case_cost: Decimal | None, unit_cost: Decimal | None) -> int | None:
    if not case_cost or not unit_cost or unit_cost <= 0:
        return None
    ratio = float(case_cost) / float(unit_cost)
    nearest = round(ratio)
    return nearest if nearest >= 1 and abs(ratio - nearest) < 0.02 else None


def _formula_divisor(sheet: Sheet, ref: str) -> int | None:
    """
    The divisor in a `=X/N` formula, following a shared-formula reference
    back to the anchor cell that defines it. Returns None when the cell
    has no formula (a typed-over value) or the formula is not a division.
    """
    formula = sheet.formulas.get(ref, "")
    shared = re.fullmatch(r"\(shared:(\d+)\)", formula)
    if shared:
        # The anchor is the first cell in the sheet whose formula text is
        # not itself a shared marker and that carries this group's index.
        # Workbooks record the group on the anchor's <f si="n"> too, but the
        # reader keeps only the text; the anchor is the nearest row above
        # in the same column with a real formula.
        column = re.match(r"[A-Z]+", ref).group(0)
        row = int(ref[len(column):])
        for r in range(row - 1, 0, -1):
            candidate = sheet.formulas.get(f"{column}{r}", "")
            if candidate and not candidate.startswith("(shared:"):
                formula = candidate
                break
    match = re.search(r"/\s*(\d+)\s*$", formula)
    return int(match.group(1)) if match else None


def _resolve_items(
    record: ReferenceRecord, *, stated: int | None, package: int | None,
    ratio: int | None, divisor: int | None,
) -> None:
    """Set the items-per-case fields, flagging any self-disagreement."""
    record.items_per_case_stated = stated
    candidates = {
        "stated": stated, "package": package, "ratio": ratio, "formula_divisor": divisor,
    }
    present = {k: v for k, v in candidates.items() if v is not None}
    distinct = set(present.values())
    if len(distinct) > 1:
        record.is_conflicted = True
        record.conflict_detail = {"items_per_case": present}
        return
    if stated is not None:
        return
    if package is not None:
        record.items_per_case_derived = package
        record.items_per_case_derivation = DERIVED_FROM_PACKAGE
    elif ratio is not None:
        record.items_per_case_derived = ratio
        record.items_per_case_derivation = DERIVED_FROM_RATIO


# ---------------------------------------------------------------------------
# per-sheet parsers
# ---------------------------------------------------------------------------

def _parse_testani_sheet1(sheet: Sheet) -> list[ReferenceRecord]:
    """Brand | Package | Item # | UPC | Current Promo | New Promo | Increase | items/case | cost/item"""
    h = _find_header(sheet, "Brand")
    if h is None:
        return []
    c = _columns(sheet, h)
    out = []
    for i in range(h + 2, len(sheet.rows) + 1):
        raw = sheet.cell(i - 1, c["UPC"])
        rec = ReferenceRecord(
            sheet=sheet.name, row=i, distributor=DISTRIBUTOR_TESTANI,
            raw_identifier=_clean(raw), item_code=normalize_upc(raw),
            description=None, brand=_clean(sheet.cell(i - 1, c["Brand"])),
            package=_clean(sheet.cell(i - 1, c["Package"])),
            pricing_basis=BASIS_PROMO,
            case_cost=_money(sheet.cell(i - 1, c["New Promo"])),
            previous_case_cost=_money(sheet.cell(i - 1, c["Current Promo"])),
            unit_cost=_money(sheet.cell(i - 1, c["cost/item"])),
        )
        item = _clean(sheet.cell(i - 1, c["Item #"]))
        if item:
            rec.identifiers.append(Identifier(KIND_DISTRIBUTOR_ITEM_CODE, str(_int(item)), DISTRIBUTOR_TESTANI))
        if rec.raw_identifier:
            rec.identifiers.append(Identifier(KIND_RETAIL_UPC_RAW, rec.raw_identifier, None))
        _resolve_items(rec, stated=_int(sheet.cell(i - 1, c["items/case"])),
                       package=None, ratio=_ratio(rec.case_cost, rec.unit_cost), divisor=None)
        out.append(rec)
    return out


def _parse_testani_change(sheet: Sheet, upc_col: str, ipc_col: str) -> list[ReferenceRecord]:
    """Testani Item Code | UPC | Size | items/case | Description | Old | New | Increase | per item"""
    h = _find_header(sheet, "Testani Item Code")
    if h is None:
        return []
    c = _columns(sheet, h)
    unit_col = next(k for k in c if "per item" in k.lower())   # "per item cost" / "cost per item"
    out = []
    for i in range(h + 2, len(sheet.rows) + 1):
        raw = sheet.cell(i - 1, c[upc_col])
        rec = ReferenceRecord(
            sheet=sheet.name, row=i, distributor=DISTRIBUTOR_TESTANI,
            raw_identifier=_clean(raw), item_code=normalize_upc(raw),
            description=_clean(sheet.cell(i - 1, c["Description"])),
            package=_clean(sheet.cell(i - 1, c["Size"])),
            pricing_basis=BASIS_PRICE_CHANGE,
            case_cost=_money(sheet.cell(i - 1, c["New Case Cost"])),
            previous_case_cost=_money(sheet.cell(i - 1, c["Old Case Cost"])),
            unit_cost=_money(sheet.cell(i - 1, c[unit_col])),
        )
        item = _clean(sheet.cell(i - 1, c["Testani Item Code"]))
        if item:
            rec.identifiers.append(Identifier(KIND_DISTRIBUTOR_ITEM_CODE, str(_int(item)), DISTRIBUTOR_TESTANI))
        if rec.raw_identifier:
            rec.identifiers.append(Identifier(KIND_RETAIL_UPC_RAW, rec.raw_identifier, None))
        _resolve_items(rec, stated=_int(sheet.cell(i - 1, c[ipc_col])),
                       package=None, ratio=_ratio(rec.case_cost, rec.unit_cost), divisor=None)
        out.append(rec)
    return out


def _parse_zink(sheet: Sheet) -> list[ReferenceRecord]:
    """RETAIL UPC | ITEM CODE | UNIT UPC | BRAND | PRODUCT NAME | PACKAGE | <date> | <date> | Change | NEW UNIT COST | ..."""
    h = _find_header(sheet, "RETAIL UPC")
    if h is None:
        return []
    header = sheet.rows[h]
    c = _columns(sheet, h)
    # The two price columns are headed by Excel date serials.
    dates = [(i, _excel_date(v)) for i, v in enumerate(header) if _excel_date(v)]
    old_col, new_col = (dates[0][0], dates[1][0]) if len(dates) >= 2 else (None, None)
    effective = dates[1][1] if len(dates) >= 2 else None
    out = []
    for i in range(h + 2, len(sheet.rows) + 1):
        raw = sheet.cell(i - 1, c["RETAIL UPC"])
        if _clean(raw) is None:
            continue
        package = _clean(sheet.cell(i - 1, c["PACKAGE"]))
        rec = ReferenceRecord(
            sheet=sheet.name, row=i, distributor=DISTRIBUTOR_ZINK,
            raw_identifier=_clean(raw), item_code=normalize_upc(raw),
            description=_clean(sheet.cell(i - 1, c["PRODUCT NAME"])),
            brand=_clean(sheet.cell(i - 1, c["BRAND"])), package=package,
            pricing_basis=BASIS_PRICE_CHANGE,
            case_cost=_money(sheet.cell(i - 1, new_col)) if new_col is not None else None,
            previous_case_cost=_money(sheet.cell(i - 1, old_col)) if old_col is not None else None,
            unit_cost=_money(sheet.cell(i - 1, c["NEW UNIT COST"])),
            effective_from=effective,
        )
        item = _clean(sheet.cell(i - 1, c["ITEM CODE"]))
        if item:
            rec.identifiers.append(Identifier(KIND_DISTRIBUTOR_ITEM_CODE, str(_int(item)), DISTRIBUTOR_ZINK))
        unit = normalize_upc(sheet.cell(i - 1, c["UNIT UPC"]))
        if unit and unit != rec.item_code:
            rec.identifiers.append(Identifier(KIND_UNIT_UPC, unit, None))
        if rec.raw_identifier:
            rec.identifiers.append(Identifier(KIND_RETAIL_UPC_RAW, rec.raw_identifier, None))
        _resolve_items(rec, stated=None, package=items_per_case_from_package(package),
                       ratio=_ratio(rec.case_cost, rec.unit_cost), divisor=None)
        out.append(rec)
    return out


def _parse_monarch_frontline(sheet: Sheet) -> list[ReferenceRecord]:
    """Product | Retail UPC | Product ID | Current Front Line | New Front Line | Diff. | Start Date | End Date"""
    h = _find_header(sheet, "Product")
    if h is None:
        return []
    c = _columns(sheet, h)
    out = []
    for i in range(h + 2, len(sheet.rows) + 1):
        raw = sheet.cell(i - 1, c["Retail UPC"])
        if _clean(raw) is None or _clean(sheet.cell(i - 1, c["Product"])) is None:
            continue
        rec = ReferenceRecord(
            sheet=sheet.name, row=i, distributor=DISTRIBUTOR_MONARCH,
            raw_identifier=_clean(raw), item_code=normalize_upc(raw),
            description=_clean(sheet.cell(i - 1, c["Product"])),
            pricing_basis=BASIS_FRONTLINE,
            case_cost=_money(sheet.cell(i - 1, c["New Front Line"])),
            previous_case_cost=_money(sheet.cell(i - 1, c["Current Front Line"])),
            effective_from=_excel_date(sheet.cell(i - 1, c["Start Date"])),
            effective_to=_excel_date(sheet.cell(i - 1, c["End Date"])),
        )
        pid = _clean(sheet.cell(i - 1, c["Product ID"]))
        if pid:
            rec.identifiers.append(Identifier(KIND_DISTRIBUTOR_PRODUCT_ID, str(_int(pid)), DISTRIBUTOR_MONARCH))
        if rec.raw_identifier:
            rec.identifiers.append(Identifier(KIND_RETAIL_UPC_RAW, rec.raw_identifier, None))
        out.append(rec)
    return out


def _parse_monarch_promo(
    sheet: Sheet, unit_cost_formula_col: str, current_unit_formula_col: str | None = None
) -> list[ReferenceRecord]:
    """
    Monarch Rung / Package / Singles share a promo-price shape; unit cost
    is =promo/N and N is Monarch's items per case.

    The row carries TWO such formulas — current unit cost and new unit
    cost — and on a handful of rows they divide by different numbers
    (row 57: current /24, new /1). That is the source changing its own
    unit definition mid-row; neither divisor can be trusted, so the row
    is flagged with both.
    """
    h = _find_header(sheet, "Product ID")
    if h is None:
        return []
    c = _columns(sheet, h)
    effective = _effective_from_preamble(sheet, h)
    out = []
    for i in range(h + 2, len(sheet.rows) + 1):
        raw = sheet.cell(i - 1, c["Retail UPC"])
        if _clean(raw) is None:
            continue
        rec = ReferenceRecord(
            sheet=sheet.name, row=i, distributor=DISTRIBUTOR_MONARCH,
            raw_identifier=_clean(raw), item_code=normalize_upc(raw),
            description=_clean(sheet.cell(i - 1, c["Product"])),
            supplier=_clean(sheet.cell(i - 1, c["Supplier"])) if "Supplier" in c else None,
            product_class=_clean(sheet.cell(i - 1, c["Product Class"])) if "Product Class" in c else None,
            package=_clean(sheet.cell(i - 1, c["Description"])),
            pricing_basis=BASIS_PROMO,
            case_cost=_money(sheet.cell(i - 1, c["New Promo Price"])),
            previous_case_cost=_money(sheet.cell(i - 1, c["Current Promo Price"])),
            unit_cost=_money(sheet.cell(i - 1, c["New Unit Cost"])),
            effective_from=effective,
        )
        pid = _clean(sheet.cell(i - 1, c["Product ID"]))
        if pid:
            rec.identifiers.append(Identifier(KIND_DISTRIBUTOR_PRODUCT_ID, str(_int(pid)), DISTRIBUTOR_MONARCH))
        if rec.raw_identifier:
            rec.identifiers.append(Identifier(KIND_RETAIL_UPC_RAW, rec.raw_identifier, None))
        divisor = _formula_divisor(sheet, f"{unit_cost_formula_col}{i}")
        current_divisor = (
            _formula_divisor(sheet, f"{current_unit_formula_col}{i}")
            if current_unit_formula_col else None
        )
        _resolve_items(rec, stated=None, package=items_per_case_from_package(rec.package),
                       ratio=_ratio(rec.case_cost, rec.unit_cost), divisor=divisor)
        if (current_divisor is not None and divisor is not None
                and current_divisor != divisor and not rec.is_conflicted):
            rec.is_conflicted = True
            rec.conflict_detail = {
                "items_per_case": {
                    "current_unit_cost_divisor": current_divisor,
                    "new_unit_cost_divisor": divisor,
                },
                "note": "The source's current and new unit-cost formulas divide the "
                        "case price by different numbers on this row.",
            }
            rec.items_per_case_derived = None
            rec.items_per_case_derivation = None
        out.append(rec)
    return out


SHEET_PARSERS = {
    "Sheet1": _parse_testani_sheet1,
    "Sheet2": lambda s: _parse_testani_change(s, "UPC", "items/case"),
    "Sheet3": lambda s: _parse_testani_change(s, "Retail UPC", "item/case"),
    "Zink - Tiki": _parse_zink,
    "Monarch Frontline": _parse_monarch_frontline,
    # (new unit cost column, current unit cost column) — both are =promo/N
    "Monarch Rung": lambda s: _parse_monarch_promo(s, "M", "I"),
    "Monarch Package": lambda s: _parse_monarch_promo(s, "O", "L"),
    "Monarch Singles": lambda s: _parse_monarch_promo(s, "P", "K"),
}


def parse_beer_inventory(path: str | Path) -> ParseReport:
    workbook = read_workbook(path)
    records: list[ReferenceRecord] = []
    per_sheet: dict[str, int] = {}
    for name, parser in SHEET_PARSERS.items():
        sheet = workbook.get(name)
        if sheet is None:
            per_sheet[name] = 0
            continue
        parsed = parser(sheet)
        per_sheet[name] = len(parsed)
        records.extend(parsed)
    return ParseReport(
        records=records,
        per_sheet=per_sheet,
        skipped_no_upc=sum(1 for r in records if not r.item_code),
        conflicted=sum(1 for r in records if r.is_conflicted),
    )
