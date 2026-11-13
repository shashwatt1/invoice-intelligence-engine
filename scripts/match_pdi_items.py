"""
PDI-matched item list — scripts/match_pdi_items.py — READ-ONLY, writes only the output files.

    python scripts/match_pdi_items.py --list data/reference/<list>.csv --out-dir /tmp/out \
        [--pdi-glob 'data/reference/store_*/Item_Sales_Summary*.xlsx' --pdi-glob 'data/reference/store_*/Mckinley*.xlsx'] \
        [--name-source list|pdi] [--strip-prefix '[EBT]']

Keeps only the items present in BOTH the PDI POS exports ('Item Sales Summary' style: Scan code, Description)
and an item list CSV (Item, Barcodes, Units per case), matched on the 11-digit PDI item code. Writes:

  * pdi-matched.csv      UPC, Description, Units - the input for scripts/import_item_master.py with
                         scripts/item_master_mappings/pdi_matched_xo.json (units become review candidates)
  * canonical-names.json [{"upc", "name"}] - the input for scripts/apply_canonical_names.py
  * left-out.csv         items the list states two different pack sizes for (never guessed)

Rules (measured on the 2026-11-13 data, see docs/reference-data-2026-11-13.md):
  * PDI scan code = UPC-A without its check digit (11 digits; 9-10 digits = leading zeros lost).
  * List barcodes are full codes: a check-digit-valid 12-digit UPC-A, a 13-digit EAN starting with 0, or an
    11-digit UPC that lost its leading 0 (restored only when the check digit then validates). Cells holding
    several barcodes separated by spaces are split. Anything else is not matched.
  * Name: the list's name (owner decision 2026-11-13) or PDI's; when a source spells one item several ways,
    the most frequent wording is used. --strip-prefix removes a leading tag such as "[EBT]".
  * Units per case: the list's stated value, 1 included (owner decision 2026-11-13).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.gtin import has_valid_gtin_check  # noqa: E402
from app.services.product_master.identifiers import upc_a_check_digit  # noqa: E402


def pdi_item_code(scan_code) -> str | None:
    """The 11-digit item code a PDI scan code holds, or None (PLU / store codes, unexpected lengths)."""
    text = str(scan_code or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit():
        return None
    if len(text) == 11:
        return text
    if len(text) in (9, 10):
        return text.zfill(11)
    return None


def list_item_code(barcode: str) -> str | None:
    """The 11-digit item code of one full barcode from the item list, or None."""
    d = (barcode or "").strip()
    if not d.isdigit():
        return None
    if len(d) == 12 and has_valid_gtin_check(d):
        return d[:11]
    if len(d) == 13 and d[0] == "0" and has_valid_gtin_check(d):
        return d[1:12]
    if len(d) == 11 and has_valid_gtin_check("0" + d):
        return ("0" + d)[:11]
    return None


def strip_prefix(name: str, prefix: str | None) -> str:
    name = " ".join((name or "").split())
    if prefix and name.upper().startswith(prefix.upper()):
        name = name[len(prefix):].strip()
    return name


def read_pdi(paths: list[str]) -> dict[str, Counter]:
    from app.services.xlsx_reader import read_workbook

    names: dict[str, Counter] = defaultdict(Counter)
    for path in paths:
        sheet = next(iter(read_workbook(path).values()))
        rows = sheet.rows
        header_at = next(i for i, r in enumerate(rows[:12]) if r and any(str(c or "").strip().lower() == "scan code" for c in r))
        col = {str(c or "").strip(): i for i, c in enumerate(rows[header_at])}
        for r in rows[header_at + 1:]:
            if not r or col["Scan code"] >= len(r):
                continue
            code = pdi_item_code(r[col["Scan code"]])
            if code:
                names[code][" ".join(str(r[col["Description"]] or "").split())] += 1
    return names


def read_list(path: str, name_col: str, barcode_col: str, units_col: str, prefix: str | None):
    units: dict[str, Counter] = defaultdict(Counter)
    names: dict[str, Counter] = defaultdict(Counter)
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            stated = (row.get(units_col) or "").strip()
            for part in (row.get(barcode_col) or "").split():
                code = list_item_code(part)
                if not code:
                    continue
                if stated.isdigit() and int(stated) >= 1:
                    units[code][int(stated)] += 1
                names[code][strip_prefix(row.get(name_col), prefix)] += 1
    return units, names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--pdi-glob", action="append")
    parser.add_argument("--name-source", choices=("list", "pdi"), default="list")
    parser.add_argument("--strip-prefix", default="[EBT]")
    parser.add_argument("--name-col", default="Item")
    parser.add_argument("--barcode-col", default="Barcodes")
    parser.add_argument("--units-col", default="Units per case")
    args = parser.parse_args()

    patterns = args.pdi_glob or ["data/reference/store_*/Item_Sales_Summary*.xlsx", "data/reference/store_*/Mckinley*.xlsx"]
    pdi_paths = sorted({p for pattern in patterns for p in glob.glob(pattern)})
    pdi = read_pdi(pdi_paths)
    units, list_names = read_list(args.list, args.name_col, args.barcode_col, args.units_col, args.strip_prefix)

    both = sorted(set(pdi) & set(units))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    matched, left_out, names = [], [], []
    for code in both:
        upc = code + upc_a_check_digit(code)
        stated = units[code]
        name = (list_names[code] if args.name_source == "list" else pdi[code]).most_common(1)[0][0]
        if len(stated) > 1:
            left_out.append((upc, name, ", ".join(str(u) for u in sorted(stated))))
            continue
        matched.append((upc, name, next(iter(stated))))
        names.append({"upc": upc, "name": name})
    with open(out / "pdi-matched.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["UPC", "Description", "Units"])
        writer.writerows(matched)
    with open(out / "left-out.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["UPC", "Name", "Pack sizes the list states"])
        writer.writerows(left_out)
    (out / "canonical-names.json").write_text(json.dumps(names, indent=1) + "\n")

    print(f"PDI exports read: {len(pdi_paths)} ({len(pdi)} item codes) | list item codes with a pack size: {len(units)}")
    print(f"in both: {len(both)} | written: {len(matched)} | left out (two pack sizes): {len(left_out)} | names from: {args.name_source}")
    print(f"pack size 1: {sum(1 for m in matched if m[2] == 1)} | above 1: {sum(1 for m in matched if m[2] > 1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
