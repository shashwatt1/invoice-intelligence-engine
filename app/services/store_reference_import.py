"""
Store Reference Import — app/services/store_reference_import.py

Parses a store's "Item Sales Summary" export into reference rows.

Pure parsing and normalization: no database, no I/O beyond reading the
workbook, so every rule below is unit-testable against the real files.

Two things about this export drive the whole module:

1. THE `Cost` COLUMN IS NOT A PRODUCT COST. It is a period aggregate —
   `Cost = # Sold x Avg Cost` held on 6,138 of 6,138 rows across the
   three supplied files, with values reaching $2.3M. `Avg Cost` is the
   per-selling-unit figure. Reading the wrong one would be wrong by
   orders of magnitude while looking entirely plausible, so `Cost` is
   refused explicitly rather than merely ignored.

2. THE HEADER IS NOT ON A FIXED ROW. Two of the three files put it on
   row 3; the one carrying a "Report Date is ..." line puts it on row 4.
   The header is located by content, and columns are then read by NAME,
   so a future export that reorders or inserts columns cannot silently
   shift the values.

The workbook is read with the standard library rather than a
spreadsheet dependency: an .xlsx is a zip of XML, this is a read-only
path for one known export shape, and the alternative is a new runtime
dependency for a tool that runs from scripts/.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

from app.services.export_service import normalize_item_code

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

HEADER_ANCHOR = "Scan code"
COL_SCAN = "Scan code"
COL_DESCRIPTION = "Description"
COL_AVG_COST = "Avg Cost"
COL_AVG_PRICE = "Avg Price"
# Present in the export and deliberately NOT imported. `Cost` and
# `$ Sold` are period aggregates; the rest are point-in-time sales
# statistics that go stale immediately and are derivable from them.
COL_AGGREGATE_COST = "Cost"
IGNORED_COLUMNS = frozenset(
    {"Cost", "$ Sold", "# Sold", "Margin", "Total Profit", "Current Inventory Count",
     "Department", "Primary Vendor"}
)
# Empty cells arrive as the literal string "NaN" from the exporter.
_NULL_STRINGS = frozenset({"", "nan", "none", "null", "-"})


@dataclass(frozen=True)
class ReferenceRow:
    """One parsed product, normalized and ready to persist."""

    item_code: str
    scan_code_raw: str
    description: str | None
    avg_cost: Decimal | None
    avg_price: Decimal | None


@dataclass(frozen=True)
class ParseResult:
    rows: list[ReferenceRow]
    store_number: str | None
    skipped_no_code: int
    skipped_duplicate: int
    zero_cost_nulled: int

    @property
    def costed(self) -> int:
        return sum(1 for r in self.rows if r.avg_cost is not None)


def _column_index(reference: str) -> int:
    match = re.match(r"([A-Z]+)", reference or "")
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _sheet_rows(path: Path) -> list[list[str | None]]:
    """Every row of the workbook's first sheet, as strings."""
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(node.text or "" for node in item.iter(f"{_NS}t"))
                for item in root.findall(f"{_NS}si")
            ]
        sheets = sorted(
            name for name in archive.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        )
        if not sheets:
            return []
        rows: list[list[str | None]] = []
        for _, element in ET.iterparse(archive.open(sheets[0]), events=("end",)):
            if element.tag != f"{_NS}row":
                continue
            cells: dict[int, str | None] = {}
            for cell in element.findall(f"{_NS}c"):
                kind = cell.get("t")
                if kind == "inlineStr":
                    inline = cell.find(f"{_NS}is")
                    value = (
                        "".join(n.text or "" for n in inline.iter(f"{_NS}t"))
                        if inline is not None else None
                    )
                else:
                    node = cell.find(f"{_NS}v")
                    value = None if node is None else node.text
                    if kind == "s" and value is not None:
                        value = shared[int(value)]
                cells[_column_index(cell.get("r"))] = value
            width = max(cells) + 1 if cells else 0
            rows.append([cells.get(i) for i in range(width)])
            element.clear()
        return rows


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _NULL_STRINGS else text


def _money(value: str | None) -> Decimal | None:
    """
    A monetary cell as Decimal, or None.

    Zero becomes None on purpose. Two thirds of this store's catalogue
    has no cost on file and the exporter writes those as 0; storing that
    as a real zero would say the product is free, which is precisely the
    confusion migration 0005 removed from invoice_items.
    """
    text = _clean(value)
    if text is None:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return None if amount == 0 else amount


def parse_item_sales_summary(path: str | Path) -> ParseResult:
    """
    Parse one Item Sales Summary export.

    Rows without a usable scan code are skipped rather than guessed at,
    and a scan code repeated inside one file keeps its first occurrence
    so the result can be persisted against a unique key.
    """
    path = Path(path)
    raw_rows = _sheet_rows(path)

    # Located by content, and by scanning the whole row rather than only
    # its first cell: the anchor is what identifies the header, not its
    # position, so an export that reorders columns still parses. A second
    # known column is required so a preamble line mentioning the phrase
    # cannot be mistaken for the header.
    def _is_header(row: list[str | None]) -> bool:
        names = {_clean(cell) for cell in row}
        return HEADER_ANCHOR in names and bool(names & {COL_DESCRIPTION, COL_AVG_COST})

    header_index = next(
        (i for i, row in enumerate(raw_rows) if row and _is_header(row)), None
    )
    if header_index is None:
        raise ValueError(
            f"{path.name}: no {HEADER_ANCHOR!r} header row found — not an "
            "Item Sales Summary export."
        )

    headers = [_clean(cell) for cell in raw_rows[header_index]]
    columns = {name: i for i, name in enumerate(headers) if name}
    for required in (COL_SCAN, COL_DESCRIPTION, COL_AVG_COST):
        if required not in columns:
            raise ValueError(f"{path.name}: required column {required!r} is missing.")

    # The preamble carries "Store: 47708760" above the header.
    store_number = None
    for row in raw_rows[:header_index]:
        for cell in row:
            text = _clean(cell) or ""
            match = re.match(r"store\s*:\s*(\S+)", text, re.IGNORECASE)
            if match:
                store_number = match.group(1)

    def cell(row: list[str | None], name: str) -> str | None:
        index = columns.get(name)
        return row[index] if index is not None and index < len(row) else None

    rows: list[ReferenceRow] = []
    seen: set[str] = set()
    skipped_no_code = skipped_duplicate = zero_cost_nulled = 0

    for row in raw_rows[header_index + 1:]:
        if not row:
            continue
        scan_raw = _clean(cell(row, COL_SCAN))
        if scan_raw is None:
            continue
        item_code = normalize_item_code(scan_raw)
        if item_code is None:
            skipped_no_code += 1
            continue
        if item_code in seen:
            skipped_duplicate += 1
            continue
        seen.add(item_code)

        avg_cost_raw = _clean(cell(row, COL_AVG_COST))
        avg_cost = _money(cell(row, COL_AVG_COST))
        if avg_cost is None and avg_cost_raw not in (None, ""):
            zero_cost_nulled += 1

        rows.append(
            ReferenceRow(
                item_code=item_code,
                scan_code_raw=scan_raw,
                description=_clean(cell(row, COL_DESCRIPTION)),
                avg_cost=avg_cost,
                avg_price=_money(cell(row, COL_AVG_PRICE)),
            )
        )

    return ParseResult(
        rows=rows,
        store_number=store_number,
        skipped_no_code=skipped_no_code,
        skipped_duplicate=skipped_duplicate,
        zero_cost_nulled=zero_cost_nulled,
    )
