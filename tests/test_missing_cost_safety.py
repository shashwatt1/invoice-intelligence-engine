"""
tests/test_missing_cost_safety.py — "unknown" must never become 0.00.

On Rocco J. Testani 228245 the model returned unit_price=null at
confidence 0.5 for two rows it genuinely could not read (LAB 30 PACK
CANS and LAB LIGHT 30 PACK CA, both printed at $22.70). Persistence
coerced those nulls to Decimal("0"), which was worse than losing them:

  - 0 x anything = 0, so LINE_ITEM_MATH passed and nothing flagged it;
  - the formatter would encode 000000 as the case cost, telling PDI the
    goods were free.

These tests pin the whole chain — schema, formatter, and export gate —
so a missing cost cannot reach a downloadable file by any route.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.services.export_service import (
    build_pdi_export,
    items_missing_cost,
    pdi_export_eligibility,
)

CODE_A = "06206705146"
CODE_B = "06206705162"
UNITS = {CODE_A: 30, CODE_B: 12}


def make_invoice(*items, status="VALIDATED"):
    invoice = Invoice(
        id=uuid.uuid4(), document_id=uuid.uuid4(), invoice_number="228245",
        currency="USD", grand_total=Decimal("2058.02"), status=status,
    )
    invoice.items = list(items)
    return invoice


def make_item(sku, description, unit_price, quantity="1", sort_order=0):
    return InvoiceItem(
        description=description, product_sku=sku,
        quantity=Decimal(quantity),
        unit_price=None if unit_price is None else Decimal(unit_price),
        line_total=None if unit_price is None else Decimal(unit_price),
        sort_order=sort_order,
    )


class TestTheModelCanSayItDoesNotKnow:
    def test_a_null_cost_is_representable_and_not_zero(self):
        item = make_item(CODE_A, "LAB 30 PACK CANS", None)
        assert item.unit_price is None
        assert item.unit_price != Decimal("0")

    def test_a_legitimate_zero_is_still_a_real_value(self):
        # A genuinely free line (promo/sample) is not the same thing as an
        # unreadable one, and must remain exportable.
        invoice = make_invoice(make_item(CODE_A, "PROMO SAMPLE", "0.00"))
        assert items_missing_cost(invoice) == []
        assert pdi_export_eligibility(invoice, UNITS).allowed is True
        line = build_pdi_export(invoice, UNITS).splitlines()[1]
        assert line[43:49] == "000000"      # a real zero, deliberately sent


class TestExportIsBlocked:
    def test_a_missing_cost_blocks_the_export(self):
        invoice = make_invoice(make_item(CODE_A, "LAB 30 PACK CANS", None))
        result = pdi_export_eligibility(invoice, UNITS)

        assert result.allowed is False
        assert "no extracted unit cost" in result.blocked_reason
        assert "LAB 30 PACK CANS" in result.blocked_reason

    def test_the_missing_lines_are_named_for_the_operator(self):
        invoice = make_invoice(
            make_item(CODE_A, "LAB 30 PACK CANS", None, sort_order=0),
            make_item(CODE_B, "LAB 12/24 OZ CAN", "20.30", sort_order=1),
            make_item(CODE_A, "LAB LIGHT 30 PACK CA", None, sort_order=2),
        )
        assert items_missing_cost(invoice) == ["LAB 30 PACK CANS", "LAB LIGHT 30 PACK CA"]

    def test_missing_cost_outranks_the_mapping_gate(self):
        # Confirming mappings must not appear to be the only thing left.
        invoice = make_invoice(make_item(CODE_A, "LAB 30 PACK CANS", None))
        assert "no extracted unit cost" in pdi_export_eligibility(invoice, {}).blocked_reason


class TestNoFabricatedZeroReachesTheFile:
    def test_the_formatter_refuses_rather_than_encoding_000000(self):
        invoice = make_invoice(make_item(CODE_A, "LAB 30 PACK CANS", None))
        with pytest.raises(ValueError, match="No unit cost was extracted"):
            build_pdi_export(invoice, UNITS)

    def test_a_priced_invoice_still_exports_normally(self):
        invoice = make_invoice(make_item(CODE_B, "LAB 12/24 OZ CAN", "20.30", quantity="3"))
        line = build_pdi_export(invoice, UNITS).splitlines()[1]

        assert line[43:49] == "002030"
        assert line[53:57] == "0012"
        assert line[58:62] == "0003"
        assert len(line) == 70
