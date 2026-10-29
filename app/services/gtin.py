"""
Printed product codes — app/services/gtin.py

What a code printed on an invoice line IS, before anything keys a mapping on
it or writes it into an EDI file. Standard library only and no application
imports, so the export path, the mapping services and the importers can all
use it without an import cycle.

PDI's item-code field is 11 digits: a UPC-A without its check digit. A code
can be written there only when it IS (or plainly contains) a UPC-A:

    kind               printed                      item code (PDI key)   exportable
    PDI_ITEM_CODE      11 digits                    as printed            yes
    UPC_A              12 digits, check digit OK    first 11              yes
    UPC_A_BAD_CHECK    12 digits, check digit wrong first 11 (key only)   no  — likely misread
    EAN13_UPC          13 digits, "0" + a UPC-A     the UPC-A's first 11  yes
    GTIN14_UPC         14 digits, "00" + a UPC-A    the UPC-A's first 11  yes
    EAN_13             13 digits, not a UPC-A       none — kept whole     no
    GTIN_14            14 digits, not a UPC-A       none — kept whole     no  — a case/pack code
    BAD_CHECK          13/14 digits, check wrong    none — kept whole     no
    SHORT              1-10 digits                  none — kept whole     no  — a vendor item number
    TOO_LONG           15+ digits                   none — kept whole     no

Rules this module keeps:
  * leading zeros are significant and never stripped or added;
  * nothing is truncated to make it fit — a code that is not a UPC-A keeps
    every digit, so two different EAN-13s can never collapse onto one key;
  * no check digit is computed to make a value valid; a wrong one stays wrong;
  * a GTIN-14 case code is not turned into its unit UPC (the two can differ).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PDI_ITEM_CODE_WIDTH = 11

KIND_NONE = "NONE"
KIND_PLACEHOLDER = "PLACEHOLDER"
KIND_PDI_ITEM_CODE = "PDI_ITEM_CODE"
KIND_UPC_A = "UPC_A"
KIND_UPC_A_BAD_CHECK = "UPC_A_BAD_CHECK"
KIND_EAN13_UPC = "EAN13_UPC"
KIND_GTIN14_UPC = "GTIN14_UPC"
KIND_EAN_13 = "EAN_13"
KIND_GTIN_14 = "GTIN_14"
KIND_BAD_CHECK = "BAD_CHECK"
KIND_SHORT = "SHORT"
KIND_TOO_LONG = "TOO_LONG"

EXPORTABLE_KINDS = frozenset({KIND_PDI_ITEM_CODE, KIND_UPC_A, KIND_EAN13_UPC, KIND_GTIN14_UPC})

# Why a code that keys a line cannot be sent to PDI as its item code.
REASONS = {
    KIND_UPC_A_BAD_CHECK: "its check digit does not match (likely misread)",
    KIND_EAN_13: "it is a 13-digit EAN, which PDI's 11-digit item code cannot hold",
    KIND_GTIN_14: "it is a 14-digit case/pack code, not the product's UPC",
    KIND_BAD_CHECK: "its check digit does not match (likely misread)",
    KIND_SHORT: "it is a vendor item number, not a barcode",
    KIND_TOO_LONG: "it is longer than any barcode",
}

_NON_DIGITS = re.compile(r"\D")


def gtin_check_digit(body: str) -> str:
    """The GS1 check digit for `body` (every digit but the check), any GTIN length."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)


def has_valid_gtin_check(digits: str) -> bool:
    return len(digits) >= 2 and digits.isdigit() and gtin_check_digit(digits[:-1]) == digits[-1]


@dataclass(frozen=True)
class PrintedCode:
    """One printed code, classified."""

    raw: str | None
    digits: str
    kind: str
    item_code: str | None          # the 11-digit PDI item code it stands for, when it is one

    @property
    def exportable(self) -> bool:
        return self.kind in EXPORTABLE_KINDS

    @property
    def key(self) -> str | None:
        """The value mappings and inventory are keyed on: the item code, or every digit kept."""
        if self.kind in (KIND_NONE, KIND_PLACEHOLDER):
            return None
        return self.item_code or self.digits

    @property
    def reason(self) -> str | None:
        return REASONS.get(self.kind)

    @property
    def upc_a(self) -> str | None:
        """The full 12-digit UPC-A, when the printed value carries its check digit."""
        if self.kind == KIND_UPC_A:
            return self.digits
        if self.kind == KIND_EAN13_UPC:
            return self.digits[1:]
        if self.kind == KIND_GTIN14_UPC:
            return self.digits[2:]
        return None


def classify(code: str | None) -> PrintedCode:
    """Classify a printed code. Separators and spaces are ignored; nothing else is changed."""
    digits = _NON_DIGITS.sub("", code or "")
    if not digits:
        return PrintedCode(code, digits, KIND_NONE, None)
    if not digits.strip("0"):
        # "000000000000" is a placeholder some vendors print on charge rows.
        return PrintedCode(code, digits, KIND_PLACEHOLDER, None)
    n = len(digits)
    if n < PDI_ITEM_CODE_WIDTH:
        return PrintedCode(code, digits, KIND_SHORT, None)
    if n == 11:
        return PrintedCode(code, digits, KIND_PDI_ITEM_CODE, digits)
    if n == 12:
        if has_valid_gtin_check(digits):
            return PrintedCode(code, digits, KIND_UPC_A, digits[:11])
        # Kept as the historical key (first 11) so existing mappings still match;
        # never exportable until a person corrects the code.
        return PrintedCode(code, digits, KIND_UPC_A_BAD_CHECK, digits[:11])
    if n == 13:
        if not has_valid_gtin_check(digits):
            return PrintedCode(code, digits, KIND_BAD_CHECK, None)
        if digits[0] == "0":
            return PrintedCode(code, digits, KIND_EAN13_UPC, digits[1:12])
        return PrintedCode(code, digits, KIND_EAN_13, None)
    if n == 14:
        if not has_valid_gtin_check(digits):
            return PrintedCode(code, digits, KIND_BAD_CHECK, None)
        if digits[:2] == "00":
            return PrintedCode(code, digits, KIND_GTIN14_UPC, digits[2:13])
        return PrintedCode(code, digits, KIND_GTIN_14, None)
    return PrintedCode(code, digits, KIND_TOO_LONG, None)


def item_code_for(code: str | None) -> str | None:
    """The mapping/inventory key for a printed code (see PrintedCode.key)."""
    return classify(code).key


def normalize_vendor_item_code(value: str | None) -> str | None:
    """
    A vendor's own item number as a crosswalk key: trimmed, inner whitespace
    removed, upper-cased. Leading zeros and punctuation are kept — "0123" and
    "123", or "12-34" and "1234", may be different items to that vendor.
    """
    cleaned = re.sub(r"\s+", "", value or "").upper()
    return cleaned or None
