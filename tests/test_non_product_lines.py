"""
tests/test_non_product_lines.py — charge rows and shorted rows on a real
invoice layout (T.J. Sheehan 101497).

That invoice prints "MISCELLANEOUS DELIVERY CHARGE 5.00" as a row of the
item table with a placeholder UPC of twelve zeros, and three product rows
with quantity 0 ("SHORT ON TRUCK", "Out of Stock"). None of those is
merchandise PDI should receive: the charge is not a product and the
shorted rows delivered nothing. They stay on the invoice for audit and
totals; the PDI selection, the export gate, the audit and the review
rows skip them — and the formatter never learns about either case.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.schemas.extraction import ExtractedInvoice, ExtractedLineItem, ExtractedVendor
from app.schemas.normalized import NormalizedInvoice, NormalizedLineItem
from app.services.case_mapping_service import build_case_mapping_status
from app.services.export_service import (
    build_pdi_export,
    normalize_item_code,
    pdi_export_eligibility,
    pdi_items,
    unmapped_item_codes,
)
from app.services.pdi_audit import audit_pdi_export
from app.services.validation.checks import check_grand_total_math, check_subtotal
from app.services.validation.normalization import normalize_invoice
from app.services.validation.reconciliation import reconcile_invoice

TOL = Decimal("0.02")


def item(sort_order, description, sku, qty, price, line_total, *, deposit="0.00",
         discount="0.00", line_type="product", pack=None):
    return InvoiceItem(
        description=description, product_sku=sku, quantity=Decimal(qty), unit_price=Decimal(price),
        line_total=Decimal(line_total), deposit=Decimal(deposit), discount=Decimal(discount),
        pack_size=pack, line_type=line_type, sort_order=sort_order,
    )


def sheehan_like(status="VALIDATED"):
    """A 5-row cut of 101497: 3 delivered, 1 shorted, 1 delivery charge."""
    inv = Invoice(id=uuid.uuid4(), document_id=uuid.uuid4(), invoice_number="101497",
                  currency="USD", subtotal=Decimal("69.60"), deposit_total=Decimal("2.25"),
                  fuel_surcharge=Decimal("5.00"), grand_total=Decimal("76.85"), status=status)
    inv.items = [
        item(0, "BUSCH LIGHT C-15 25OZ", "018200250040", "1", "21.05", "21.80", deposit="0.75", discount="1.65", pack="C-15 25OZ"),
        item(1, "NATTY DADDY C-15 25OZ", "018200250132", "2", "21.30", "44.10", deposit="0.75", discount="1.40", pack="C-15 25OZ"),
        item(2, "BUSCH ICE C-15 25OZ", "018200250071", "0", "21.05", "0.00", deposit="0.75", discount="0.55", pack="C-15 25OZ"),
        item(3, "MISCELLANEOUS DELIVERY CHARGE", None, "1", "5.00", "5.00", line_type="charge"),
        item(4, "BUD ICE C-15 25OZ", "018200250064", "1", "23.90", "24.65", deposit="0.75", discount="6.35", pack="C-15 25OZ"),
    ]
    return inv


UNITS = {"01820025004": 15, "01820025013": 15, "01820025006": 15}


class TestPlaceholderUpc:
    def test_twelve_zeros_is_no_code(self):
        assert normalize_item_code("000000000000") is None
        assert normalize_item_code("0000") is None
        assert normalize_item_code("00027920") == "00027920"       # leading zeros are still a code


class TestPdiSelection:
    def test_charges_and_shorted_rows_are_not_pdi_items(self):
        chosen = [i.description for i in pdi_items(sheehan_like())]
        assert chosen == ["BUSCH LIGHT C-15 25OZ", "NATTY DADDY C-15 25OZ", "BUD ICE C-15 25OZ"]

    def test_the_gate_ignores_them(self):
        inv = sheehan_like()
        assert unmapped_item_codes(inv, {}) == ["01820025004", "01820025013", "01820025006"]
        assert unmapped_item_codes(inv, UNITS) == []
        assert pdi_export_eligibility(inv, UNITS).allowed is True

    def test_an_invoice_of_only_charges_has_nothing_to_export(self):
        inv = sheehan_like()
        inv.items = [inv.items[3]]
        assert pdi_export_eligibility(inv, {}).allowed is False
        assert "no product line items" in pdi_export_eligibility(inv, {}).blocked_reason

    def test_the_edi_carries_only_delivered_products(self):
        inv = sheehan_like()
        edi = build_pdi_export(inv, UNITS)
        records = edi.split("\r\n")
        b = [r for r in records if r.startswith("B")]
        assert len(b) == 3
        assert all("00000      " not in r for r in b)                # no blank-code record for the charge
        assert not any("+0000" in r[57:62] for r in b)               # no zero-quantity record
        assert b[0][43:49] == "002105" and b[0][53:57] == "0015" and b[0][58:62] == "0001"
        assert "DELIVERY" not in edi

    def test_the_audit_counts_the_same_rows(self):
        inv = sheehan_like()
        audit = audit_pdi_export(build_pdi_export(inv, UNITS), inv)
        assert audit.ok, [c for c in audit.checks if not c.passed]

    def test_the_review_rows_skip_them_too(self):
        rows = build_case_mapping_status(sheehan_like(), {})
        assert [r.item_code for r in rows] == ["01820025004", "01820025013", "01820025006"]

    def test_an_item_built_without_the_new_fields_still_exports(self):
        # In-memory items from older tests carry neither line_type nor a
        # quantity; they are products and are kept, exactly as before.
        inv = sheehan_like()
        legacy = InvoiceItem(description="X", product_sku="012345678905", unit_price=Decimal("1"),
                             line_total=Decimal("1"), sort_order=9)
        inv.items.append(legacy)
        assert legacy in pdi_items(inv)


class TestValidationWithCharges:
    def _normalized(self, *, fuel, charge_row=True):
        items = [
            NormalizedLineItem(sort_order=0, description="BUSCH LIGHT", quantity=Decimal(1), unit_price=Decimal("21.05"),
                               unit_discount=Decimal("1.65"), unit_deposit=Decimal("0.75"), line_total=Decimal("21.80")),
            NormalizedLineItem(sort_order=1, description="NATTY DADDY", quantity=Decimal(2), unit_price=Decimal("21.30"),
                               unit_discount=Decimal("1.40"), unit_deposit=Decimal("0.75"), line_total=Decimal("44.10")),
            NormalizedLineItem(sort_order=2, description="BUSCH ICE", quantity=Decimal(0), unit_price=Decimal("21.05"),
                               unit_discount=Decimal("0.55"), unit_deposit=Decimal("0.75"), line_total=Decimal("0.00")),
        ]
        if charge_row:
            items.append(NormalizedLineItem(sort_order=3, line_type="charge", description="MISCELLANEOUS DELIVERY CHARGE",
                                            quantity=Decimal(1), unit_price=Decimal("5.00"), line_total=Decimal("5.00")))
        return NormalizedInvoice(line_items=tuple(items), subtotal=Decimal("63.65"), deposit_total=Decimal("2.25"),
                                 fuel_surcharge=Decimal(fuel) if fuel is not None else None, grand_total=Decimal("70.90"))

    def test_the_charge_row_is_outside_the_subtotal_and_inside_the_grand_total(self):
        inv = self._normalized(fuel=None)
        [sub] = check_subtotal(inv, TOL)
        assert sub.status.value == "PASSED"                      # 65.90 = 63.65 + 2.25 deposits; charge excluded
        [grand] = check_grand_total_math(inv, TOL)
        assert grand.status.value == "PASSED"                    # 63.65 + 2.25 + 5.00 charge row

    def test_a_charge_printed_both_as_a_row_and_in_the_totals_counts_once(self):
        [grand] = check_grand_total_math(self._normalized(fuel="5.00"), TOL)
        assert grand.status.value == "PASSED"

    def test_a_charge_row_that_differs_from_the_header_fuel_is_added_in_full(self):
        # header says 3.00 fuel, the row says 5.00 delivery: 63.65 + 2.25 + 3 + 5 = 73.90 != 70.90
        [grand] = check_grand_total_math(self._normalized(fuel="3.00"), TOL)
        assert grand.status.value == "FAILED"

    def test_reconciliation_rule_b_explains_every_delivered_row_and_rule_d_stays_quiet(self):
        inv = self._normalized(fuel="5.00")
        out, checks = reconcile_invoice(inv, TOL)
        names = [c.name for c in checks]
        assert names.count("LINE_TOTAL_INCLUDES_DEPOSIT") == 2                 # the two delivered rows
        assert "UNIT_PRICE_INCLUDED_DEPOSIT" not in names
        assert "UNIT_PRICE_MAY_INCLUDE_DEPOSIT" not in names
        assert "SUBTOTAL_RECONCILED" in names
        assert [i.unit_price for i in out.line_items][:2] == [Decimal("21.05"), Decimal("21.30")]   # gross, as printed
        assert out.line_items[2].quantity == 0 and out.line_items[2].line_total == 0

    def test_the_discount_is_recorded_and_not_applied(self):
        out, _ = reconcile_invoice(self._normalized(fuel="5.00"), TOL)
        assert out.line_items[0].unit_discount == Decimal("1.65")
        assert out.line_items[0].unit_price == Decimal("21.05")


class TestExtractionToCanonical:
    def test_line_type_and_zero_quantity_survive_normalization(self):
        extracted = ExtractedInvoice(
            vendor=ExtractedVendor(name="T.J. SHEEHAN"), invoice_number="101497", invoice_date="2026-09-10",
            subtotal=1039.16, deposit_total=36.60, fuel_surcharge=5.0, grand_total=1080.76,
            line_items=[
                ExtractedLineItem(description="BUSCH ICE C-15 25OZ", product_code="018200250071", quantity=0,
                                  unit_price=21.05, unit_discount=0.55, unit_deposit=0.75, line_total=0.0),
                ExtractedLineItem(line_type="charge", description="MISCELLANEOUS DELIVERY CHARGE",
                                  product_code=None, quantity=1, unit_price=5.0, line_total=5.0),
            ],
        )
        norm = normalize_invoice(extracted)
        norm = norm[0] if isinstance(norm, tuple) else norm
        assert [i.line_type for i in norm.line_items] == ["product", "charge"]
        assert norm.line_items[0].quantity == 0 and norm.line_items[0].unit_price == Decimal("21.05")
        assert norm.line_items[1].product_code is None

    def test_a_charge_is_not_allowed_to_carry_a_placeholder_code_into_a_mapping(self):
        with pytest.raises(ValueError):
            ExtractedLineItem(line_type="fee", description="x")           # only product / charge
