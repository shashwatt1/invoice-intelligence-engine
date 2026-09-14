"""
Item Sales → pricing rows — app/services/item_sales_pricing_import.py

Turns an Item Sales Summary export into source-aware product_pricing
records (pricing_basis = period_average), one per source row, with the
report period as the effective range.

Why a second path for the same file format
------------------------------------------
store_product_references is one row per product and was built from the
September 2026 exports. A later export for a different period carries a
different Avg Cost and Avg Price for the same UPC; importing it there
would overwrite the earlier figure and lose which period said what.
product_pricing keeps one row per (file, sheet, row), so two periods
coexist and a rule can choose between them by date.

What this file contributes that the others do not
-------------------------------------------------
Avg Price — what the scanned unit actually sold for. Even where the
store has no cost on file (Avg Cost = 0), the retail figure says what
the sellable unit IS: a UPC scanning at $25.72 against a $22.70 case is
sold as the case. That is direct evidence for units-per-case, from the
store's own till, and it is the reason this import exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.models.product_reference import BASIS_PERIOD_AVERAGE, KIND_RETAIL_UPC_RAW
from app.services.beer_inventory_import import Identifier, ReferenceRecord
from app.services.store_reference_import import _sheet_rows, parse_item_sales_summary

_REPORT_RANGE = re.compile(
    r"Report Date is\s+(\d{1,2})/(\d{1,2})/(\d{4})\s*-\s*(\d{1,2})/(\d{1,2})/(\d{4})", re.IGNORECASE
)


@dataclass
class ItemSalesPricingReport:
    records: list[ReferenceRecord]
    store_number: str | None
    period_start: date | None
    period_end: date | None
    skipped: int
    with_retail: int = 0
    with_cost: int = 0
    notes: list[str] = field(default_factory=list)


def _report_period(path: Path) -> tuple[date | None, date | None]:
    for row in _sheet_rows(path)[:6]:
        for cell in row:
            match = _REPORT_RANGE.search(str(cell or ""))
            if match:
                m1, d1, y1, m2, d2, y2 = (int(x) for x in match.groups())
                return date(y1, m1, d1), date(y2, m2, d2)
    return None, None


def parse_item_sales_pricing(path: str | Path) -> ItemSalesPricingReport:
    """
    Reuse the proven Item Sales parser, then express each row as a
    dated, provenance-carrying pricing record.

    Every source row is kept, including a scan code the file lists
    twice: the POS can hold two item records under one code (one
    costed, one not; two spellings of the description), and this table
    is one row per source row precisely so that both survive with their
    own row numbers. The catalogue path still keeps the first only.
    """
    path = Path(path)
    parsed = parse_item_sales_summary(path, keep_duplicates=True)
    start, end = _report_period(path)

    records: list[ReferenceRecord] = []
    with_retail = with_cost = 0
    for product in parsed.rows:
        rec = ReferenceRecord(
            sheet="data", row=product.source_row, distributor="store",
            raw_identifier=product.scan_code_raw, item_code=product.item_code,
            description=product.description, pricing_basis=BASIS_PERIOD_AVERAGE,
            unit_cost=product.avg_cost, effective_from=start, effective_to=end,
        )
        rec.unit_retail = product.avg_price
        rec.identifiers.append(Identifier(KIND_RETAIL_UPC_RAW, product.scan_code_raw, None))
        with_retail += product.avg_price is not None
        with_cost += product.avg_cost is not None
        records.append(rec)

    return ItemSalesPricingReport(
        records=records, store_number=parsed.store_number,
        period_start=start, period_end=end,
        skipped=parsed.skipped_no_code + parsed.skipped_duplicate,
        with_retail=with_retail, with_cost=with_cost,
    )
