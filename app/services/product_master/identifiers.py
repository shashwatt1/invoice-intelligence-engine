"""
Source-aware identifier resolution — app/services/product_master/identifiers.py

Turns a raw value from one source into a canonical identifier, and records
which named transformation did it.

The rule this module exists to enforce: **length never decides the repair.**
Two sources in this corpus both write 11-digit identifiers and they need
opposite corrections.

    Item Sales Summary   "01820000115"      text, check digit omitted
                         -> append the computed check digit -> 018200001154

    Monarch / Zink       "1.8200001154E10"  Excel float, leading zero lost
                         -> restore the leading zero          -> 018200001154

Applying either rule to the other population produces a valid-looking
barcode for a different product, which is why the correction is chosen
from the *source profile* rather than from the digits. A future
manufacturer-specific derivation (the unproven 28476 behaviour, for
instance) belongs here as one more named rule on a profile — not as
another branch inside a generic normalizer.

Resolution is per-identifier and independent: a raw value resolves on its
own, and identity is whatever two raw values share afterwards. There is no
pairwise bridge between source formats to keep in step.

This module is pure. It opens no database connection and is not reachable
from invoice processing or the EDI writer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.models.product_master import (
    BASIS_EAN_13,
    BASIS_SUPPLIER_ID,
    BASIS_UNRESOLVED,
    BASIS_UPC_A,
    DERIVE_APPEND_CHECK_DIGIT,
    DERIVE_EXPAND_SCIENTIFIC,
    DERIVE_NONE,
    DERIVE_RESTORE_LEADING_ZERO,
    DERIVE_STRIP_EXCEL_DECIMAL,
    DERIVE_STRIP_SEPARATORS,
    ID_EAN_13,
    ID_PDI_ITEM_CODE,
    ID_SHORT_CODE,
    ID_SUPPLIER_ITEM_ID,
    ID_UNRESOLVED,
    ID_UPC_A,
    STATE_AUTO_MATCHED,
    STATE_REVIEW_REQUIRED,
    STATE_UNRESOLVED,
)

# How a source writes numbers. An Excel float cannot carry a leading zero;
# a text column can.
REPRESENTATION_TEXT = "TEXT"
REPRESENTATION_EXCEL_FLOAT = "EXCEL_FLOAT"

# Whether a source's barcodes carry their check digit.
BARCODE_FULL = "FULL_BARCODE"
BARCODE_CHECK_DIGIT_OMITTED = "CHECK_DIGIT_OMITTED"

# What a source's identifier column means when it is not a barcode at all.
ROLE_BARCODE = "BARCODE"
ROLE_SUPPLIER_ID = "SUPPLIER_ID"
ROLE_DISTRIBUTOR_CODE = "DISTRIBUTOR_CODE"

_SCIENTIFIC = re.compile(r"^\d+(?:\.\d+)?[Ee][+-]?\d+$")
_EXCEL_INT = re.compile(r"^(\d+)\.0+$")
_SEPARATED = re.compile(r"^\d+(?:[-\s]\d+)+$")
_NULLISH = {"", "nan", "none", "null", "n/a", "-", "totals", "total"}


@dataclass(frozen=True)
class SourceProfile:
    """
    How one source system writes identifiers.

    This is the whole point of the module: the profile, not the value,
    selects the derivation.
    """

    name: str
    representation: str = REPRESENTATION_TEXT
    barcode_convention: str = BARCODE_FULL
    role: str = ROLE_BARCODE
    distributor: str | None = None


# The profiles the reference corpus actually contains.
ITEM_SALES_SUMMARY = SourceProfile(
    name="item_sales_summary",
    representation=REPRESENTATION_TEXT,
    # Scan codes are PDI item codes: the leading zero survives, the check
    # digit does not.
    barcode_convention=BARCODE_CHECK_DIGIT_OMITTED,
)
DISTRIBUTOR_WORKBOOK = SourceProfile(
    name="distributor_workbook",
    # Values reach Excel as numbers, so a leading zero may be missing.
    representation=REPRESENTATION_EXCEL_FLOAT,
    barcode_convention=BARCODE_FULL,
)


def upc_a_check_digit(digits11: str) -> str:
    """Standard UPC-A check digit over the first eleven digits."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(digits11))
    return str((10 - total % 10) % 10)


def has_valid_upc_check(digits12: str) -> bool:
    return len(digits12) == 12 and upc_a_check_digit(digits12[:11]) == digits12[11]


@dataclass
class DerivedIdentifier:
    """One raw value, resolved."""

    raw_value: str
    normalized_value: str | None
    identifier_type: str
    derivation: str
    derivation_detail: dict = field(default_factory=dict)
    evidence_state: str = STATE_AUTO_MATCHED
    canonical_upc: str | None = None
    identity_basis: str = BASIS_UNRESOLVED

    @property
    def resolves_identity(self) -> bool:
        """Whether this identifier is strong enough to ground a product."""
        return self.identity_basis in {BASIS_UPC_A, BASIS_EAN_13, BASIS_SUPPLIER_ID}


def _clean(raw: str) -> tuple[str, list[str], dict]:
    """Strip the spreadsheet's formatting, recording what was stripped."""
    applied: list[str] = []
    detail: dict = {}
    text = raw.strip()

    if _SCIENTIFIC.match(text):
        try:
            number = Decimal(text)
        except InvalidOperation:
            return text, applied, detail
        if number == number.to_integral_value():
            detail["scientific_source"] = text
            text = str(int(number))
            applied.append(DERIVE_EXPAND_SCIENTIFIC)
    elif (match := _EXCEL_INT.match(text)) is not None:
        text = match.group(1)
        applied.append(DERIVE_STRIP_EXCEL_DECIMAL)

    if _SEPARATED.match(text):
        detail["separated_source"] = text
        text = re.sub(r"[-\s]", "", text)
        applied.append(DERIVE_STRIP_SEPARATORS)

    return text, applied, detail


def derive_identifier(raw: str | None, profile: SourceProfile) -> DerivedIdentifier:
    """
    Resolve one raw source value to its canonical form under one profile.

    Nothing is padded to a length and no value is promoted to a barcode
    because it happens to have twelve digits' worth of characters. When the
    profile does not explain the value, it stays unresolved with its raw
    form intact.
    """
    if raw is None or str(raw).strip().lower() in _NULLISH:
        return DerivedIdentifier(str(raw or ""), None, ID_UNRESOLVED, DERIVE_NONE,
                                 evidence_state=STATE_UNRESOLVED)

    raw_value = str(raw).strip()
    text, applied, detail = _clean(raw_value)
    detail["source_profile"] = profile.name
    detail["representation"] = profile.representation

    def result(normalized, id_type, derivation, *, upc=None, basis=BASIS_UNRESOLVED,
               state=STATE_AUTO_MATCHED, **extra) -> DerivedIdentifier:
        detail.update(extra)
        chain = [*applied, derivation] if derivation != DERIVE_NONE else applied
        detail["derivation_chain"] = chain
        # `derivation` names the transformation that produced the canonical
        # value. When classification needed none, the last cleaning step is
        # still what changed the value, so the column must say so rather
        # than claim nothing happened.
        effective = derivation if derivation != DERIVE_NONE else (
            applied[-1] if applied else DERIVE_NONE
        )
        return DerivedIdentifier(
            raw_value=raw_value, normalized_value=normalized, identifier_type=id_type,
            derivation=effective, derivation_detail=detail,
            evidence_state=state, canonical_upc=upc, identity_basis=basis,
        )

    if not text.isdigit():
        return result(None, ID_UNRESOLVED, DERIVE_NONE, state=STATE_UNRESOLVED,
                      reason="non_numeric")
    if not text.strip("0"):
        # "000000000000" — a filler some sheets print on keg and charge rows.
        return result(text, ID_UNRESOLVED, DERIVE_NONE, state=STATE_UNRESOLVED,
                      reason="placeholder_zeros")

    # Non-barcode columns are typed by the profile, never by length.
    if profile.role == ROLE_SUPPLIER_ID:
        return result(text, ID_SUPPLIER_ITEM_ID, DERIVE_NONE, basis=BASIS_SUPPLIER_ID)
    if profile.role == ROLE_DISTRIBUTOR_CODE:
        from app.models.product_master import ID_DISTRIBUTOR_ITEM_CODE

        return result(text, ID_DISTRIBUTOR_ITEM_CODE, DERIVE_NONE)

    length = len(text)

    if length == 12 and has_valid_upc_check(text):
        return result(text, ID_UPC_A, DERIVE_NONE, upc=text, basis=BASIS_UPC_A)

    if length == 13:
        # Kept whole. Truncating an EAN-13 to eleven digits is the known
        # normalize_item_code() defect and is not repeated here.
        return result(text, ID_EAN_13, DERIVE_NONE, upc=text, basis=BASIS_EAN_13)

    if length == 11:
        if profile.barcode_convention == BARCODE_CHECK_DIGIT_OMITTED:
            # A PDI item code. The raw value stays eleven digits; the
            # canonical barcode is what the check digit completes.
            canonical = text + upc_a_check_digit(text)
            return result(text, ID_PDI_ITEM_CODE, DERIVE_APPEND_CHECK_DIGIT,
                          upc=canonical, basis=BASIS_UPC_A, reconstructed_upc=canonical)
        if profile.representation == REPRESENTATION_EXCEL_FLOAT:
            canonical = "0" + text
            if has_valid_upc_check(canonical):
                return result(canonical, ID_UPC_A, DERIVE_RESTORE_LEADING_ZERO,
                              upc=canonical, basis=BASIS_UPC_A, leading_zero_restored=True)
            # The padded form does not validate, so the assumption is wrong
            # for this row. Say so rather than keep it.
            return result(text, ID_UNRESOLVED, DERIVE_NONE, state=STATE_REVIEW_REQUIRED,
                          reason="leading_zero_restoration_failed_check_digit")
        return result(text, ID_UNRESOLVED, DERIVE_NONE, state=STATE_REVIEW_REQUIRED,
                      reason="eleven_digits_no_profile_rule")

    if length == 12:
        # Twelve digits that fail the UPC-A check. Under a check-digit-omitting
        # source this is consistent with an EAN-13 minus its check digit, but
        # that is an unproven hypothesis, so it resolves to nothing.
        return result(text, ID_UNRESOLVED, DERIVE_NONE, state=STATE_REVIEW_REQUIRED,
                      reason="twelve_digits_check_digit_failed")

    if length <= 5:
        # PLU-like. Being short does not make it a barcode.
        return result(text, ID_SHORT_CODE, DERIVE_NONE, state=STATE_REVIEW_REQUIRED)

    return result(text, ID_UNRESOLVED, DERIVE_NONE, state=STATE_REVIEW_REQUIRED,
                  reason=f"unexpected_length_{length}")


def canonical_key_for(derived: DerivedIdentifier, *, fallback: str) -> str:
    """
    The namespaced identity a resolved identifier belongs to.

    Two raw values from different sources land on the same key exactly when
    they resolved to the same canonical barcode — that equality *is* the
    bridge. A value that resolves to nothing gets a key of its own so it can
    never merge with another unresolved row by accident.
    """
    if derived.identity_basis in {BASIS_UPC_A, BASIS_EAN_13} and derived.canonical_upc:
        return f"upc:{derived.canonical_upc}"
    if derived.identity_basis == BASIS_SUPPLIER_ID and derived.normalized_value:
        return f"supplier:{derived.derivation_detail.get('source_profile', 'unknown')}:{derived.normalized_value}"
    return f"unresolved:{fallback}"
