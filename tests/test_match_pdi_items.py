"""The matching rules of scripts/match_pdi_items.py (PDI scan codes vs a list's full barcodes)."""
import pytest

from scripts.match_pdi_items import list_item_code, pdi_item_code, strip_prefix


@pytest.mark.parametrize("raw, expected", [
    ("01200000129", "01200000129"),          # 11 digits: a UPC-A without its check digit
    ("1200000129", "01200000129"),           # leading zero lost in the export
    ("01200000129.0", "01200000129"),        # Excel float text
    ("4011", None),                          # PLU / store code
    ("012000001291", None),                  # 12 digits is not how PDI writes a scan code
    ("ABC", None),
    (None, None),
])
def test_pdi_scan_code_to_item_code(raw, expected):
    assert pdi_item_code(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("012000001291", "01200000129"),         # UPC-A with a valid check digit
    ("012000001292", None),                  # wrong check digit: never matched
    ("0012000001291", "01200000129"),        # EAN-13 starting with 0
    ("4006381333931", None),                 # a foreign EAN-13 PDI cannot hold
    ("12000001291", "01200000129"),          # UPC-A that lost its leading 0 (check digit validates)
    ("12000001292", None),                   # ...unless the check digit then fails
    ("01234565", None),                      # 8-digit UPC-E is not expanded
    ("8.5e+11", None),                       # Excel scientific notation: digits are lost
])
def test_list_barcode_to_item_code(raw, expected):
    assert list_item_code(raw) == expected


def test_strip_prefix_and_spacing():
    assert strip_prefix("[EBT] COKE  12OZ ", "[EBT]") == "COKE 12OZ"
    assert strip_prefix("[ebt]COKE", "[EBT]") == "COKE"
    assert strip_prefix("COKE [EBT]", "[EBT]") == "COKE [EBT]"   # only a leading tag
    assert strip_prefix("COKE", None) == "COKE"
