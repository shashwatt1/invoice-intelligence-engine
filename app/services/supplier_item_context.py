"""
Supplier ITEM numbers as display context — app/services/supplier_item_context.py

Extraction reads a vendor's own item number (Frito-Lay's 8-digit ITEM, for
example) into `supplier_item_id`, separate from the UPC. Invoice lines do not
store it; it survives only in the raw model response kept on the invoice
(`raw_extraction_json`). This module recovers it so a reviewer entering a
missing UPC can see it beside the empty field.

It is context only. A supplier ITEM number is never a UPC: nothing here
writes it, normalises it, or feeds it to the export.

It fails closed. A row gets an ITEM number only when the match is certain:
  * the row was extracted (not added by hand), and its sort_order is its
    position in the extracted list (persistence assigns sort_order = index);
  * the raw line's description equals the row's original description (the
    first recorded value when the description was later corrected).
Anything else (an unreadable response, a different shape, a count or
description mismatch) yields no ITEM number for that row.
"""

from __future__ import annotations

import json
from typing import Any


def _raw_line_items(raw: Any) -> list[dict[str, Any]] | None:
    """The extracted line items from a stored provider response, or None."""
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("line_items"), list):
        return raw["line_items"]
    try:
        content = raw["choices"][0]["message"]["content"]
        parsed = json.loads(content) if isinstance(content, str) else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    items = parsed.get("line_items") if isinstance(parsed, dict) else None
    return items if isinstance(items, list) else None


def _original_description(item: Any) -> str | None:
    """The description as extracted: the first recorded old value if it was corrected."""
    for entry in item.correction_history or []:
        if isinstance(entry, dict) and entry.get("field") == "description":
            return entry.get("old")
    return item.description


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def supplier_item_ids(invoice: Any) -> dict[int, str]:
    """sort_order -> supplier ITEM number, for rows where the match is certain."""
    raw_items = _raw_line_items(getattr(invoice, "raw_extraction_json", None))
    if not raw_items:
        return {}
    found: dict[int, str] = {}
    for item in invoice.items:
        if (item.entry_source or "extracted") != "extracted":
            continue
        index = item.sort_order
        if not isinstance(index, int) or not 0 <= index < len(raw_items):
            continue
        raw = raw_items[index]
        if not isinstance(raw, dict):
            continue
        supplier_id = _clean(raw.get("supplier_item_id"))
        if supplier_id is None:
            continue
        if _clean(raw.get("description")) != _clean(_original_description(item)):
            continue
        found[index] = supplier_id
    return found


def digits(value: str | None) -> str:
    """Only the ASCII digits 0-9 of a value: how a typed UPC is compared with an ITEM number.

    Not str.isdigit(): that also accepts fullwidth, Arabic-Indic and superscript digits.
    """
    return "".join(ch for ch in (value or "") if "0" <= ch <= "9")
