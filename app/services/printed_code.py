"""
Printed product codes that are visibly incomplete.

Some layouts wrap a dashed UPC across two print lines, so the first line
ends in a separator ("8-10030-51002-") or stops partway through the dashed
layout ("0-12000-1263"). Read alone, that fragment is not the product's
UPC. `normalize_item_code()` would still turn it into a plausible-looking
item code, which could silently match a different product.

This module only answers "is this printed value visibly cut off?". It
never completes, repairs or normalises a value: the raw printed text is
kept as it is, and only a person can supply the missing digits.

The rule is deliberately narrow, and claims a value only on unambiguous
formatting evidence:

* it ends in a separator; or
* it is printed in the dashed UPC-A layout 1-5-5-1 (number system,
  manufacturer, product, check digit), with the first two groups whole and
  the product group cut short ("0-12000-1263").

An 11-digit "0-12000-12630" is not claimed. Printing a UPC without its
check digit is a normal style, and `normalize_item_code()` drops the check
digit anyway.

Plain digit strings are never judged by length. Short codes are legitimate
for many suppliers (PLUs, vendor item numbers, UPC-E). Anything containing
letters is also left alone.
"""

from __future__ import annotations

import re

_TRAILING_SEPARATOR = re.compile(r"[-\u2013\u2014]\s*$")
# 1-5-5-1 layout with the product group cut short: N-MMMMM-PPPP (1-4 digits).
_PARTIAL_UPC_LAYOUT = re.compile(r"^\d\s*-\s*\d{5}\s*-\s*\d{1,4}$")

TRAILING_SEPARATOR = "ends in a separator, so the rest of the code is missing"
PARTIAL_UPC_LAYOUT = "stops partway through a dashed UPC (1-5-5-1) layout"


def printed_code_incompleteness(raw: str | None) -> str | None:
    """
    Why the printed code is visibly incomplete, or None if there is no
    such evidence.

    None does not mean "verified complete". It only means this rule makes
    no claim about the value.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value or re.search(r"[A-Za-z]", value):
        return None
    if _TRAILING_SEPARATOR.search(value) and re.search(r"\d", value):
        return TRAILING_SEPARATOR
    if _PARTIAL_UPC_LAYOUT.match(value):
        return PARTIAL_UPC_LAYOUT
    return None
