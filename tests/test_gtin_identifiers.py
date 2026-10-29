"""
Printed code classification and the PDI item-code key — tests/test_gtin_identifiers.py

Synthetic codes only. The first group pins the keys that existing mappings,
inventory rows and golden EDI files rest on; they must not move.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import gtin
from app.services.export_service import (
    _pdi_item_code,
    items_with_unusable_item_code,
    normalize_item_code,
    pdi_export_eligibility,
)

UPC_A = "036000291452"            # valid UPC-A
UPC_A_BAD = "036000291453"        # same body, wrong check digit
EAN13_UPC = "0" + UPC_A           # the same product as a zero-padded EAN-13
GTIN14_UPC = "00" + UPC_A         # ... and as a zero-padded GTIN-14
EAN13 = "4006381333931"           # a real EAN-13 shape, not a UPC
GTIN14_CASE = "10036000291459"    # indicator 1 + the UPC body, valid check


class TestHistoricalKeysAreUnchanged:
    @pytest.mark.parametrize("printed, key", [
        (UPC_A, "03600029145"),
        ("0-36000-29145-2", "03600029145"),          # dashes and spaces are separators
        ("03600029145", "03600029145"),              # 11 digits: the PDI item code itself
        ("12345", "12345"),                          # a short vendor number keeps its old key
        (UPC_A_BAD, "03600029145"),                  # a bad check digit keeps the old key ...
        ("000000000000", None),
        ("", None),
        (None, None),
    ])
    def test_key(self, printed, key):
        assert normalize_item_code(printed) == key

    def test_leading_zeros_are_never_stripped_or_added(self):
        assert normalize_item_code("00012345678") == "00012345678"
        assert normalize_item_code("0012") == "0012"


class TestLongCodesAreNoLongerTruncated:
    def test_a_zero_padded_ean13_keys_on_the_upc_it_carries(self):
        assert normalize_item_code(EAN13_UPC) == normalize_item_code(UPC_A) == "03600029145"

    def test_a_zero_padded_gtin14_keys_on_the_upc_it_carries(self):
        assert normalize_item_code(GTIN14_UPC) == "03600029145"

    def test_a_real_ean13_keeps_every_digit(self):
        assert normalize_item_code(EAN13) == EAN13

    def test_two_ean13s_differing_after_position_11_stay_distinct(self):
        a, b = "4006381333931", "4006381333948"
        assert gtin.has_valid_gtin_check(a) and gtin.has_valid_gtin_check(b)
        assert normalize_item_code(a) != normalize_item_code(b)

    def test_a_case_gtin14_is_not_turned_into_the_unit_upc(self):
        assert normalize_item_code(GTIN14_CASE) == GTIN14_CASE

    def test_a_long_code_with_a_wrong_check_digit_is_kept_whole(self):
        assert normalize_item_code("4006381333932") == "4006381333932"
        assert gtin.classify("4006381333932").kind == gtin.KIND_BAD_CHECK


class TestClassification:
    @pytest.mark.parametrize("printed, kind, exportable", [
        ("03600029145", gtin.KIND_PDI_ITEM_CODE, True),
        (UPC_A, gtin.KIND_UPC_A, True),
        (UPC_A_BAD, gtin.KIND_UPC_A_BAD_CHECK, False),
        (EAN13_UPC, gtin.KIND_EAN13_UPC, True),
        (GTIN14_UPC, gtin.KIND_GTIN14_UPC, True),
        (EAN13, gtin.KIND_EAN_13, False),
        (GTIN14_CASE, gtin.KIND_GTIN_14, False),
        ("12345678", gtin.KIND_SHORT, False),
        ("1234567890123456", gtin.KIND_TOO_LONG, False),
        ("000000000000", gtin.KIND_PLACEHOLDER, False),
    ])
    def test_kind(self, printed, kind, exportable):
        c = gtin.classify(printed)
        assert (c.kind, c.exportable) == (kind, exportable)

    def test_the_full_upc_is_recovered_only_when_the_check_digit_was_printed(self):
        assert gtin.classify(UPC_A).upc_a == UPC_A
        assert gtin.classify(EAN13_UPC).upc_a == UPC_A
        assert gtin.classify("03600029145").upc_a is None      # no check digit is computed

    def test_check_digit_matches_the_upc_a_rule(self):
        assert gtin.gtin_check_digit("03600029145") == "2"

    @pytest.mark.parametrize("raw, key", [(" ab 12 ", "AB12"), ("0123", "0123"), ("12-34", "12-34"), ("", None)])
    def test_vendor_item_numbers_keep_leading_zeros_and_punctuation(self, raw, key):
        assert gtin.normalize_vendor_item_code(raw) == key


def _item(code, n=1, line_type="product"):
    return SimpleNamespace(product_sku=code, description=f"ITEM {n}", quantity=1, line_type=line_type,
                           sort_order=n, unit_price=1, line_total=1)


def _invoice(*codes):
    return SimpleNamespace(items=[_item(c, i) for i, c in enumerate(codes, 1)], status="VALIDATED")


class TestExportGate:
    def test_barcodes_pass(self):
        assert items_with_unusable_item_code(_invoice(UPC_A, EAN13_UPC, GTIN14_UPC, "03600029145")) == []

    def test_vendor_numbers_long_codes_and_bad_check_digits_are_named(self):
        found = items_with_unusable_item_code(_invoice("12345678", EAN13, GTIN14_CASE, UPC_A_BAD, UPC_A))
        assert [d for d, _ in found] == ["ITEM 1", "ITEM 2", "ITEM 3", "ITEM 4"]
        assert "vendor item number" in found[0][1]
        assert "check digit" in found[3][1]

    def test_charge_rows_and_codeless_rows_are_not_this_gates_concern(self):
        inv = SimpleNamespace(items=[_item("123", 1, "charge"), _item(None, 2)], status="VALIDATED")
        assert items_with_unusable_item_code(inv) == []

    def test_an_invoice_with_a_vendor_number_cannot_export(self):
        result = pdi_export_eligibility(_invoice(UPC_A, "12345678"))
        assert not result.allowed
        assert "cannot be sent as a PDI item code" in result.blocked_reason

    def test_the_formatter_refuses_to_overflow_the_field(self):
        assert _pdi_item_code(UPC_A) == "03600029145"
        assert _pdi_item_code("12345") == "00000012345"
        with pytest.raises(ValueError):
            _pdi_item_code(EAN13)
