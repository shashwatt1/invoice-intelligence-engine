"""
Workbook reader — app/services/xlsx_reader.py

Reads every sheet of an .xlsx, keeping both the cached cell value and,
where present, the formula that produced it. Standard library only: an
.xlsx is a zip of XML and this is a read-only path for known export
shapes, which does not justify a spreadsheet dependency.

Formulas matter here. In the Beer Inventory workbook a distributor's
items-per-case is not typed into a cell — it is the divisor in
`=I5/4`. Reading only the cached value would lose the one thing that
says how the number was arrived at, and would make a hand-edited cell
indistinguishable from a computed one.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


@dataclass
class Sheet:
    name: str
    rows: list[list[str | None]] = field(default_factory=list)
    # cell reference ("I5") -> formula text ("I5/4"). Shared-formula
    # members are recorded as "(shared:<n>)" so a caller can tell a
    # copied formula from an absent one.
    formulas: dict[str, str] = field(default_factory=dict)

    def cell(self, row_index: int, col_index: int) -> str | None:
        row = self.rows[row_index] if row_index < len(self.rows) else None
        return row[col_index] if row and col_index < len(row) else None


def column_index(reference: str) -> int:
    match = re.match(r"([A-Z]+)", reference or "")
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def read_workbook(path: str | Path) -> dict[str, Sheet]:
    """All sheets, in workbook order, keyed by sheet name."""
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(node.text or "" for node in item.iter(f"{_NS}t"))
                for item in root.findall(f"{_NS}si")
            ]
        rels = {
            rel.get("Id"): rel.get("Target")
            for rel in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets: dict[str, Sheet] = {}
        for entry in workbook.find(f"{_NS}sheets"):
            name = entry.get("name")
            target = rels[entry.get(f"{_RNS}id")].lstrip("/")
            member = target if target.startswith("xl/") else f"xl/{target}"
            sheet = Sheet(name=name)
            for _, element in ET.iterparse(archive.open(member), events=("end",)):
                if element.tag != f"{_NS}row":
                    continue
                cells: dict[int, str | None] = {}
                for cell in element.findall(f"{_NS}c"):
                    ref = cell.get("r")
                    kind = cell.get("t")
                    formula = cell.find(f"{_NS}f")
                    if formula is not None:
                        sheet.formulas[ref] = formula.text or f"(shared:{formula.get('si')})"
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
                    cells[column_index(ref)] = value
                width = max(cells) + 1 if cells else 0
                sheet.rows.append([cells.get(i) for i in range(width)])
                element.clear()
            sheets[name] = sheet
    return sheets
