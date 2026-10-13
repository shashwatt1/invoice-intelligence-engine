"""
tests/test_mapping_ux.py — mapping-screen UX (pure parts).

The review row carries the line itself so a reviewer can identify and map a
product without leaving the screen: its position (the correction key), its
product code as stored, the invoice's own prices (source values, never derived
or borrowed), and the original description once a description was corrected.
Display only: none of this changes the mapping rules or the export.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.case_mapping_service import build_case_mapping_status, original_description
from app.services.export_service import build_pdi_export
from tests.test_case_mapping import make_invoice, make_item


def _item(sku="018200250064", description="BUD ICE 25OZ", **values):
    item = make_item(sku, description, pack_size="C-15 25OZ", sort_order=values.pop("sort_order", 3))
    for name, value in values.items():
        setattr(item, name, value)
    return item


class TestTheRowCarriesTheLine:
    def test_source_prices_position_and_code(self):
        item = _item(quantity=Decimal("2.0000"), unit_price=Decimal("30.0000"), line_total=Decimal("60.00"),
                     discount=Decimal("0.5000"))
        [row] = build_case_mapping_status(make_invoice(item), {})
        assert (row.sort_order, row.product_code, row.item_code) == (3, "018200250064", "01820025006")
        assert (row.quantity, row.unit_price, row.line_total, row.unit_discount) == (2.0, 30.0, 60.0, 0.5)
        assert row.original_description is None

    def test_missing_prices_stay_missing(self):
        item = _item(unit_price=None, line_total=None, discount=None)
        [row] = build_case_mapping_status(make_invoice(item), {})
        assert (row.unit_price, row.line_total, row.unit_discount) == (None, None, None)

    def test_decimal_precision_is_kept(self):
        item = _item(unit_price=Decimal("0.4167"), line_total=Decimal("10.0008"))
        [row] = build_case_mapping_status(make_invoice(item), {})
        assert (row.unit_price, row.line_total) == (0.4167, 10.0008)

    def test_the_original_description_survives_corrections(self):
        item = _item(description="BUD ICE 25 OZ CAN")
        item.correction_history = [
            {"field": "unit_price", "old": 29.0, "new": 30.0, "by": "m", "at": "t1", "note": None},
            {"field": "description", "old": "BUD ICF 25OZ", "new": "BUD ICE 25OZ", "by": "m", "at": "t2", "note": None},
            {"field": "description", "old": "BUD ICE 25OZ", "new": "BUD ICE 25 OZ CAN", "by": "m", "at": "t3", "note": None},
        ]
        [row] = build_case_mapping_status(make_invoice(item), {})
        assert (row.description, row.original_description) == ("BUD ICE 25 OZ CAN", "BUD ICF 25OZ")
        assert original_description(_item()) is None

    def test_the_new_fields_do_not_change_mapping_state_or_the_export(self):
        item = _item(sort_order=0)
        invoice = make_invoice(item)
        invoice.invoice_date = None
        [unmapped] = build_case_mapping_status(invoice, {})
        [mapped] = build_case_mapping_status(invoice, {"01820025006": 15})
        assert (unmapped.mapped, unmapped.suggested_units_per_case, unmapped.suggestion_source) == (False, 15, "pack_size")
        assert (mapped.mapped, mapped.units_per_case) == (True, 15)
        assert build_pdi_export(invoice, {"01820025006": 15}) == build_pdi_export(invoice, {"01820025006": 15})
