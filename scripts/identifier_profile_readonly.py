#!/usr/bin/env python
"""
Product Master identifier profile — READ-ONLY (Phase 1, V14/V16).

    python scripts/identifier_profile_readonly.py

Reports, and changes nothing:

  V14  identifier formats: length by identifier type, check-digit validity
       of UPC-A values, spreadsheet artefacts (scientific notation, '.0',
       non-digits) in raw values, and the derivations applied.
  V16  normalized identifiers that map to more than one product.

The session is READ ONLY and autocommit; the only statements are SELECTs.
It never normalizes, re-keys or corrects a record — findings are for the
data team (decision D13), not for automatic repair.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.product_master.identifiers import has_valid_upc_check  # noqa: E402

SCIENTIFIC = re.compile(r"\d(?:\.\d+)?[eE][+-]?\d+")

SELECT_IDENTIFIERS = (
    "SELECT product_id, identifier_type, raw_value, normalized_value, derivation, source_system "
    "FROM master_product_identifiers"
)


def profile(rows: list[tuple]) -> dict:
    """V14 + V16 from (product_id, type, raw, normalized, derivation, source) rows."""
    lengths: Counter = Counter()
    derivations: Counter = Counter()
    artefacts = {"scientific_notation": 0, "trailing_dot_zero": 0, "non_digit_raw": 0}
    upc_checked = upc_valid = 0
    owners: dict[tuple, set] = defaultdict(set)
    for product_id, identifier_type, raw, normalized, derivation, source in rows:
        raw = raw or ""
        lengths[(identifier_type, len(normalized) if normalized else None)] += 1
        derivations[(derivation, source)] += 1
        if SCIENTIFIC.search(raw):
            artefacts["scientific_notation"] += 1
        if raw.endswith(".0"):
            artefacts["trailing_dot_zero"] += 1
        if raw and not raw.isdigit():
            artefacts["non_digit_raw"] += 1
        if identifier_type == "UPC_A" and normalized:
            upc_checked += 1
            upc_valid += has_valid_upc_check(normalized)
        if normalized:
            owners[(identifier_type, normalized)].add(str(product_id))
    shared = {f"{t}:{v}": sorted(p) for (t, v), p in owners.items() if len(p) > 1}
    return {
        "identifiers": len(rows),
        "v14_length_by_type": {f"{t}|{n}": c for (t, n), c in sorted(lengths.items(), key=str)},
        "v14_upc_a_check_valid": {"checked": upc_checked, "valid": upc_valid},
        "v14_raw_artefacts": artefacts,
        "v14_derivation_by_source": {f"{d}|{s}": c for (d, s), c in sorted(derivations.items(), key=str)},
        "v16_normalized_shared_by_products": {"count": len(shared), "examples": dict(list(shared.items())[:20])},
    }


def main() -> None:
    import psycopg2

    from app.core.config import get_settings

    dsn = get_settings().database_url_sync.replace("postgresql+psycopg2://", "postgresql://")
    connection = psycopg2.connect(dsn, connect_timeout=10)
    try:
        connection.set_session(readonly=True, autocommit=True)
        cursor = connection.cursor()
        cursor.execute(SELECT_IDENTIFIERS)
        rows = cursor.fetchall()
    finally:
        connection.close()
    print(json.dumps(profile(rows), indent=2))


if __name__ == "__main__":
    main()
