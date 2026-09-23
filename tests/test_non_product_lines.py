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
from app.services.validation.checks import (
    check_grand_total_math,
    check_line_item_math,
    check_subtotal,
)
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

    def test_supplier_item_id_never_becomes_the_canonical_product_code(self):
        """
        A real photographed invoice (Red Bull Distribution Company Inc.,
        RCM / Red Cliff Texaco, 09/17/2026) printed a vendor item code in
        the ID column ("RB248904") and, separately, the actual retail
        barcode below the description ("611269002461"). Extraction must
        keep them as two distinct fields all the way through
        normalization: product_code carries the canonical UPC, never the
        vendor code, and supplier_item_id survives alongside it for audit.
        """
        extracted = ExtractedInvoice(
            vendor=ExtractedVendor(name="Red Bull Distribution Company Inc."),
            invoice_number="2035546957", invoice_date="2026-09-17",
            subtotal=329.53, grand_total=329.53,
            line_items=[
                ExtractedLineItem(description="SF ICED 8.40Z", product_code="611269002461",
                                  supplier_item_id="RB248904", quantity=1,
                                  unit_price=37.99, line_total=37.99),
            ],
        )
        norm, _ = normalize_invoice(extracted)
        item = norm.line_items[0]
        assert item.product_code == "611269002461"
        assert item.product_code != "RB248904"
        assert item.supplier_item_id == "RB248904"

    def test_all_seven_red_bull_identifiers_normalize_correctly(self):
        """
        The full set of products where the source invoice supports a
        confident supplier-id / UPC pairing (see OCR evidence quoted
        above). Proves normalization never lets any of the seven
        ID-column codes leak into product_code.
        """
        pairs = [
            ("SF ICED 8.40Z", "RB248904", "611269002461"),
            ("SUGARFREE 8.40Z 4PK", "RB2860", "611269109009"),
            ("COCONUT 120Z LS", "RB221027", "611269032120"),
            ("SF ICED 120Z", "RB248897", "611269002447"),
            ("SF W PEACH 120Z", "RB249729", "611269002768"),
            ("ICED 120Z", "RB248898", "611269001846"),
            ("RED BULL 120Z LS", "RB4816", "611269818994"),
        ]
        extracted = ExtractedInvoice(
            vendor=ExtractedVendor(name="Red Bull Distribution Company Inc."),
            invoice_number="2035546957", invoice_date="2026-09-17",
            subtotal=329.53, grand_total=329.53,
            line_items=[
                ExtractedLineItem(description=desc, product_code=upc, supplier_item_id=supplier,
                                  quantity=1, unit_price=37.99, line_total=37.99)
                for desc, supplier, upc in pairs
            ],
        )
        norm, _ = normalize_invoice(extracted)
        for (desc, supplier, upc), item in zip(pairs, norm.line_items, strict=True):
            assert item.description == desc
            assert item.product_code == upc, f"{desc}: expected UPC {upc}, got {item.product_code}"
            assert item.product_code != supplier
            assert item.supplier_item_id == supplier


class TestRevalidationKeepsLineTypes:
    """
    Revalidation (after a correction or a duplicate decision) re-judges
    the STORED invoice. It must carry the stored line types and photo
    provenance across, or a delivery charge would be re-summed as a
    product and a resolved duplicate re-counted.
    """

    def test_charge_and_duplicate_rows_stay_out_of_the_subtotal(self):
        from app.services.revalidation_service import build_report, normalized_from_persisted

        inv = sheehan_like()
        inv.items.append(item(5, "BUD ICE C-15 25OZ", "018200250064", "1", "23.90", "24.65",
                              deposit="0.75", discount="6.35", pack="C-15 25OZ", line_type="duplicate"))
        inv.items[5].source_pages = [2]
        inv.items[5].duplicate_candidate = {"of_sort_order": 4, "reason": "seen twice",
                                            "resolution": "same_row", "decided_by": "r"}
        normalized = normalized_from_persisted(inv)
        assert [i.line_type for i in normalized.line_items] == [
            "product", "product", "product", "charge", "product", "duplicate"]
        assert normalized.line_items[5].source_pages == (2,)
        assert normalized.line_items[5].possible_duplicate_of is None      # resolved: not a question
        # the duplicate row changes nothing about the subtotal judgement:
        # the report reads exactly as for the same invoice without it
        with_dup = build_report(inv, ocr_confidence=0.95, ai_confidence=0.95)
        inv.items.pop()
        without = build_report(inv, ocr_confidence=0.95, ai_confidence=0.95)
        failed = lambda r: sorted((c.name, c.field) for c in r.checks if c.status.value == "FAILED")  # noqa: E731
        assert failed(with_dup) == failed(without)
        assert not any(name == "CROSS_PHOTO_DUPLICATES" for name, _ in failed(with_dup))

    def test_an_unresolved_candidate_still_blocks(self):
        from app.services.revalidation_service import build_report, normalized_from_persisted

        inv = sheehan_like()
        inv.items[4].duplicate_candidate = {"of_sort_order": 1, "reason": "unsure", "resolution": None}
        assert normalized_from_persisted(inv).line_items[4].possible_duplicate_of == 1
        report = build_report(inv, ocr_confidence=0.95, ai_confidence=0.95)
        assert report.decision.value == "REVIEW_REQUIRED"
        assert any(c.name == "CROSS_PHOTO_DUPLICATES" and c.status.value == "FAILED" for c in report.checks)


class TestChargeRowsInsideThePrintedSubtotal:
    """
    The other real convention for charge rows.

    T.J. Sheehan prints its delivery charge OUTSIDE the goods subtotal
    (subtotal 1,219.57 = Σ products; the 5.00 row is added on top), and
    the class above pins that. A service invoice — UniFirst 2310090549,
    RCM, 18 Sep 2026 — prints the opposite: its "DEFE Charge Fixed"
    29.00 and "Energy Surcharge" 3.00 are rows of the item table AND are
    already inside the printed Invoice Total of 191.04:

        Σ product rows          159.04
        + DEFE Charge Fixed      29.00
        + Energy Surcharge        3.00
        = Invoice Total         191.04   <- the printed subtotal
        + TAX                    11.26
        = Total                 202.30

    Nothing on the document says which convention it follows, exactly as
    with the deposit-inside/outside axis the grand-total check already
    tries both ways. Adding the charge rows on top of a subtotal that
    already contains them double-counts them and reports an expected
    grand total of 234.30 for an invoice whose own totals reconcile.
    """

    def _service_invoice(self, *, subtotal="191.04", grand_total="202.30", tax="11.26"):
        products = [
            NormalizedLineItem(sort_order=0, description="2PLY MINI TWIN TT(EACH)ROLL #4", quantity=Decimal(12),
                               unit_price=Decimal("7.16"), line_total=Decimal("85.92")),
            NormalizedLineItem(sort_order=1, description="MAT-4X6 GREAT IMP 2.0", quantity=Decimal(4),
                               unit_price=Decimal("10.22"), line_total=Decimal("40.88")),
            NormalizedLineItem(sort_order=2, description="MAT-3X10 GREAT IMP 2.0", quantity=Decimal(1),
                               unit_price=Decimal("14.62"), line_total=Decimal("14.62")),
            NormalizedLineItem(sort_order=3, description="WET MOP LARGE WITH RED BAND 24", quantity=Decimal(3),
                               unit_price=Decimal("3.07"), line_total=Decimal("9.21")),
            NormalizedLineItem(sort_order=4, description="AIR FRESH METERD SPRAY-LINEN 9", quantity=Decimal(2),
                               unit_price=Decimal("1.97"), line_total=Decimal("3.94")),
            NormalizedLineItem(sort_order=5, description="LAUNDRY BAGS-SPECIAL", quantity=Decimal(1),
                               unit_price=Decimal("1.89"), line_total=Decimal("1.89")),
            NormalizedLineItem(sort_order=6, description="JUMBO BATH TISSUE DISPENSER", quantity=Decimal(2),
                               unit_price=Decimal("0.7106"), line_total=Decimal("1.42")),
            NormalizedLineItem(sort_order=7, description="MOPS-HANDLE 1 1/8 X 60", quantity=Decimal(2),
                               unit_price=Decimal("0.3553"), line_total=Decimal("0.71")),
            NormalizedLineItem(sort_order=8, description="DISPN-METERED AEROSAL AIR FRES", quantity=Decimal(2),
                               unit_price=Decimal("0.0562"), line_total=Decimal("0.11")),
            NormalizedLineItem(sort_order=9, description="BAG RACK", quantity=Decimal(1),
                               unit_price=Decimal("0.3398"), line_total=Decimal("0.34")),
        ]
        charges = [
            NormalizedLineItem(sort_order=10, line_type="charge", description="DEFE Charge Fixed",
                               quantity=Decimal(1), unit_price=Decimal("29.00"), line_total=Decimal("29.00")),
            NormalizedLineItem(sort_order=11, line_type="charge", description="Energy Surcharge",
                               quantity=Decimal(1), unit_price=Decimal("3.00"), line_total=Decimal("3.00")),
        ]
        return NormalizedInvoice(
            line_items=tuple(products + charges),
            subtotal=Decimal(subtotal), tax_amount=Decimal(tax), grand_total=Decimal(grand_total),
        )

    def test_the_product_rows_alone_do_not_make_the_printed_subtotal(self):
        # The premise: 159.04 of goods against a printed subtotal of 191.04.
        inv = self._service_invoice()
        products = sum((i.line_total for i in inv.line_items if i.line_type == "product"), Decimal("0"))
        charges = sum((i.line_total for i in inv.line_items if i.line_type == "charge"), Decimal("0"))
        assert products == Decimal("159.04")
        assert charges == Decimal("32.00")
        assert products + charges == inv.subtotal

    def test_the_subtotal_check_accepts_charges_that_are_inside_it(self):
        [result] = check_subtotal(self._service_invoice(), TOL)
        assert result.status.value == "PASSED"

    def test_the_grand_total_reconciles_without_adding_the_charges_twice(self):
        [result] = check_grand_total_math(self._service_invoice(), TOL)
        assert result.status.value == "PASSED", result.message
        assert "charge" in result.message.lower()

    def test_a_grand_total_that_matches_no_convention_still_fails(self):
        # Guard: accepting a second charge convention must not accept anything.
        [result] = check_grand_total_math(self._service_invoice(grand_total="999.99"), TOL)
        assert result.status.value == "FAILED"

    def test_charges_outside_the_subtotal_are_still_added_on_top(self):
        # The T.J. Sheehan convention must keep working: same rows, but the
        # printed subtotal covers goods only, so the charges are added.
        inv = self._service_invoice(subtotal="159.04", grand_total="202.30")
        [sub] = check_subtotal(inv, TOL)
        assert sub.status.value == "PASSED"
        [grand] = check_grand_total_math(inv, TOL)
        assert grand.status.value == "PASSED", grand.message


class TestHeaderFuelAlsoPrintedAsAChargeRow:
    """
    A fuel/service amount can be printed twice: once in the totals block
    (fuel_surcharge) and once as a row of the item table. It must count
    once, and `charges_not_in_fuel` has always handled the case where the
    charge rows AS A WHOLE equal the header figure.

    A service invoice that prints SEVERAL charge rows breaks that: with
    rows of 29.00 and 3.00 against a header fuel of 3.00, the totals no
    longer match row-for-row, so the 3.00 was added twice and the grand
    total came out 3.00 high. The fix compares the header figure against
    each row as well as against their sum, and — when the rows sit inside
    the printed subtotal — treats the fuel they represent as inside it too.

    Three placements have to stay distinguishable:
      A. the header fuel is one of the charge rows, and those rows are
         already inside the printed subtotal;
      B. the header fuel is one of the charge rows sitting outside it;
      C. the header fuel is not represented by any charge row.
    """

    def _invoice(self, *, subtotal, grand_total, charge_rows, fuel=None, tax="0",
                 deposit=None, products=("100.00",)):
        items = [
            NormalizedLineItem(sort_order=i, description=f"GOODS {i}", quantity=Decimal(1),
                               unit_price=Decimal(p), line_total=Decimal(p))
            for i, p in enumerate(products)
        ]
        items += [
            NormalizedLineItem(sort_order=len(items) + j, line_type="charge",
                               description=f"CHARGE {j}", quantity=Decimal(1),
                               unit_price=Decimal(c), line_total=Decimal(c))
            for j, c in enumerate(charge_rows)
        ]
        return NormalizedInvoice(
            line_items=tuple(items), subtotal=Decimal(subtotal), tax_amount=Decimal(tax),
            deposit_total=None if deposit is None else Decimal(deposit),
            fuel_surcharge=None if fuel is None else Decimal(fuel),
            grand_total=Decimal(grand_total),
        )

    def test_a_case_the_header_fuel_is_one_of_several_rows_inside_the_subtotal(self):
        # 159.04 goods + 29.00 + 3.00 = 191.04 printed subtotal; fuel 3.00 is
        # the second row printed again in the totals block. 191.04 + 11.26 tax.
        invoice = self._invoice(products=("159.04",), charge_rows=("29.00", "3.00"),
                                fuel="3.00", tax="11.26", subtotal="191.04",
                                grand_total="202.30")
        [result] = check_grand_total_math(invoice, TOL)
        assert result.status.value == "PASSED", result.message

    def test_b_case_the_header_fuel_is_a_row_sitting_outside_the_subtotal(self):
        # goods alone make the subtotal; both charge rows are added on top,
        # and the 5.00 fuel is the same money as one of them.
        invoice = self._invoice(products=("100.00",), charge_rows=("10.00", "5.00"),
                                fuel="5.00", subtotal="100.00", grand_total="115.00")
        [result] = check_grand_total_math(invoice, TOL)
        assert result.status.value == "PASSED", result.message

    def test_c_case_the_header_fuel_is_not_represented_by_any_charge_row(self):
        # Balkan's shape: fuel and deposit in the totals block, no charge rows.
        invoice = self._invoice(products=("263.86",), charge_rows=(), fuel="5.00",
                                deposit="4.80", subtotal="263.86", grand_total="273.66")
        [result] = check_grand_total_math(invoice, TOL)
        assert result.status.value == "PASSED", result.message

    def test_the_sheehan_shape_keeps_working_unchanged(self):
        # products = subtotal, one delivery charge printed as a row AND in the
        # totals block, deposits already inside the subtotal.
        invoice = self._invoice(products=("1219.57",), charge_rows=("5.00",), fuel="5.00",
                                deposit="41.25", subtotal="1219.57", grand_total="1224.57")
        [result] = check_grand_total_math(invoice, TOL)
        assert result.status.value == "PASSED", result.message

    def test_only_the_duplicated_row_is_discounted_not_every_charge(self):
        # Two rows, one of which is the header fuel: the other must still be
        # added. 100 + 10 + 7 = 117, not 100 + 7 and not 100 + 24.
        invoice = self._invoice(products=("100.00",), charge_rows=("10.00", "7.00"),
                                fuel="7.00", subtotal="100.00", grand_total="117.00")
        [result] = check_grand_total_math(invoice, TOL)
        assert result.status.value == "PASSED", result.message
        for wrong in ("107.00", "124.00"):
            bad = self._invoice(products=("100.00",), charge_rows=("10.00", "7.00"),
                                fuel="7.00", subtotal="100.00", grand_total=wrong)
            [other] = check_grand_total_math(bad, TOL)
            assert other.status.value == "FAILED", wrong

    def test_no_header_fuel_leaves_charge_handling_untouched(self):
        outside = self._invoice(products=("100.00",), charge_rows=("10.00", "3.00"),
                                subtotal="100.00", grand_total="113.00")
        assert check_grand_total_math(outside, TOL)[0].status.value == "PASSED"
        inside = self._invoice(products=("100.00",), charge_rows=("10.00", "3.00"),
                               subtotal="113.00", grand_total="113.00")
        assert check_grand_total_math(inside, TOL)[0].status.value == "PASSED"

    def test_a_grand_total_matching_no_placement_still_fails(self):
        invoice = self._invoice(products=("159.04",), charge_rows=("29.00", "3.00"),
                                fuel="3.00", tax="11.26", subtotal="191.04",
                                grand_total="999.99")
        assert check_grand_total_math(invoice, TOL)[0].status.value == "FAILED"


class TestUnknownProductQuantityCannotBeExported:
    """
    A product quantity extraction could not read is null, and persistence
    stores it as 0 because the column is NOT NULL. `pdi_items` then drops
    it exactly like a shorted row — so an unknown quantity would leave the
    invoice silently, while its money stayed in the printed subtotal and
    the AMOUNT header.

    The document itself separates the two cases without any schema change:
    a genuine short delivered nothing and its line total is 0; a row whose
    quantity could not be read still carries the money it was billed for.
    A product row with quantity 0 and a non-zero line total is therefore
    unresolved, not shorted, and the export is blocked until a person
    settles it. This is an export gate, not an extraction correction —
    nothing here infers a quantity (that is Rule E's job, on proof).
    """

    def _invoice(self, *rows):
        invoice = Invoice(id=uuid.uuid4(), document_id=uuid.uuid4(), invoice_number="GATE")
        invoice.items = list(rows)
        return invoice

    def _units(self, invoice):
        return {normalize_item_code(i.product_sku): 24 for i in invoice.items if i.product_sku}

    def test_a_genuine_shorted_row_is_excluded_but_never_blocks(self):
        invoice = self._invoice(
            item(0, "DELIVERED", "012345678905", "2", "10.00", "20.00"),
            item(1, "SHORT ON TRUCK", "012345678912", "0", "10.00", "0.00"),
        )
        assert [i.description for i in pdi_items(invoice)] == ["DELIVERED"]
        gate = pdi_export_eligibility(invoice, self._units(invoice))
        assert gate.allowed is True and gate.blocked_reason is None

    def test_a_zero_quantity_row_that_still_carries_money_blocks_the_export(self):
        invoice = self._invoice(
            item(0, "DELIVERED", "012345678905", "2", "10.00", "20.00"),
            item(1, "UNKNOWN QTY", "012345678912", "0", "10.22", "40.88"),
        )
        gate = pdi_export_eligibility(invoice, self._units(invoice))
        assert gate.allowed is False
        assert "quantity" in gate.blocked_reason.lower()
        assert "UNKNOWN QTY" in gate.blocked_reason
        # and it is never quietly emitted as a PDI record
        assert "UNKNOWN QTY" not in [i.description for i in pdi_items(invoice)]

    def test_the_reason_says_unresolved_not_shorted(self):
        invoice = self._invoice(
            item(0, "DELIVERED", "012345678905", "2", "10.00", "20.00"),
            item(1, "UNKNOWN QTY", "012345678912", "0", "10.22", "40.88"),
        )
        reason = pdi_export_eligibility(invoice, self._units(invoice)).blocked_reason
        assert "short" not in reason.lower()

    def test_a_negative_line_total_on_a_zero_quantity_row_also_blocks(self):
        invoice = self._invoice(
            item(0, "DELIVERED", "012345678905", "2", "10.00", "20.00"),
            item(1, "CREDIT?", "012345678912", "0", "10.00", "-40.88"),
        )
        assert pdi_export_eligibility(invoice, self._units(invoice)).allowed is False

    def test_rows_with_a_real_quantity_are_unaffected(self):
        invoice = self._invoice(
            item(0, "A", "012345678905", "2", "10.00", "20.00"),
            item(1, "B", "012345678912", "1", "5.00", "5.00"),
        )
        gate = pdi_export_eligibility(invoice, self._units(invoice))
        assert gate.allowed is True and gate.blocked_reason is None

    def test_a_charge_row_is_not_an_unknown_product_quantity(self):
        # A fixed charge prints no quantity, so it persists as 0 with money
        # on the row — the same shape, but it is not a product and never
        # becomes a PDI record, so it must not block the export.
        invoice = self._invoice(
            item(0, "DELIVERED", "012345678905", "2", "10.00", "20.00"),
            item(1, "DEFE CHARGE", None, "0", "29.00", "29.00", line_type="charge"),
        )
        gate = pdi_export_eligibility(invoice, self._units(invoice))
        assert gate.allowed is True and gate.blocked_reason is None

    def test_the_gate_blocks_even_when_every_product_is_mapped(self):
        invoice = self._invoice(
            item(0, "DELIVERED", "012345678905", "2", "10.00", "20.00"),
            item(1, "UNKNOWN QTY", "012345678912", "0", "10.22", "40.88"),
        )
        units = self._units(invoice)                       # both products mapped
        assert not unmapped_item_codes(invoice, units)
        assert pdi_export_eligibility(invoice, units).allowed is False


class TestAFixedChargeIsNotMeasuredLikeAProduct:
    """
    A charge row bills an amount, not a number of things.

    UniFirst 2310090549 prints "DEFE Charge Fixed 29.00" and "Energy
    Surcharge 3.00" with nothing in the QTY column, so extraction
    correctly reports quantity null. Persistence stores 0, because the
    column is NOT NULL — and the stored row then reads as a delivery of
    none at 29.00 each. Judged as a product that is a contradiction, and
    revalidating the invoice after any later correction flipped it from
    VALIDATED back to review on those two rows alone.

    quantity x unit_price = line_total is a statement about goods. It
    does not apply to a fixed charge, so the check reports SKIPPED with
    the reason rather than passing it silently or failing it wrongly.
    Product rows are untouched: their arithmetic, including the zero
    quantity rules, is exactly as before.
    """

    def _invoice(self, *items, **totals):
        return NormalizedInvoice(line_items=tuple(items), **{
            k: (Decimal(v) if isinstance(v, str) else v) for k, v in totals.items()})

    def _charge(self, sort_order, qty, price, total):
        return NormalizedLineItem(
            sort_order=sort_order, line_type="charge", description="DEFE Charge Fixed",
            quantity=Decimal(str(qty)), unit_price=Decimal(price), line_total=Decimal(total))

    def _product(self, sort_order, qty, price, total, description="GOODS"):
        return NormalizedLineItem(
            sort_order=sort_order, description=description,
            quantity=Decimal(str(qty)), unit_price=Decimal(price), line_total=Decimal(total))

    def test_a_fixed_charge_stored_with_quantity_zero_does_not_fail(self):
        invoice = self._invoice(
            self._product(0, 2, "10.00", "20.00"),
            self._charge(1, 0, "29.00", "29.00"),
            self._charge(2, 0, "3.00", "3.00"),
        )
        results = check_line_item_math(invoice, TOL)
        assert not [c for c in results if c.status.value == "FAILED"]
        charges = [c for c in results if c.field in ("line_items[1]", "line_items[2]")]
        assert [c.status.value for c in charges] == ["SKIPPED", "SKIPPED"]
        assert all("charge" in c.message.lower() for c in charges)

    def test_the_identity_is_not_required_of_a_charge_whatever_its_quantity(self):
        # 1 x 29.00 = 29.00 would balance; 2 x 29.00 would not. Neither is
        # the point: the identity simply does not describe a fixed charge.
        for quantity in (0, 1, 2):
            invoice = self._invoice(self._charge(0, quantity, "29.00", "29.00"))
            [result] = check_line_item_math(invoice, TOL)
            assert result.status.value == "SKIPPED", quantity

    def test_product_rows_still_have_their_arithmetic_checked(self):
        good = self._invoice(self._product(0, 2, "10.00", "20.00"))
        assert check_line_item_math(good, TOL)[0].status.value == "PASSED"
        bad = self._invoice(self._product(0, 2, "10.00", "99.00"))
        [result] = check_line_item_math(bad, TOL)
        assert result.status.value == "FAILED"
        assert result.expected == "20.00" and result.actual == "99.00"

    def test_a_product_with_quantity_zero_and_money_on_the_row_still_fails(self):
        # The unresolved-quantity shape the export gate blocks: a product
        # row must not be excused the way a charge is.
        invoice = self._invoice(self._product(0, 0, "10.22", "40.88", "UNKNOWN QTY"))
        [result] = check_line_item_math(invoice, TOL)
        assert result.status.value == "FAILED"

    def test_a_genuinely_shorted_product_row_still_passes(self):
        invoice = self._invoice(self._product(0, 0, "10.00", "0.00", "SHORT ON TRUCK"))
        [result] = check_line_item_math(invoice, TOL)
        assert result.status.value == "PASSED"

    def test_the_unifirst_shape_is_stable_when_judged_from_persisted_values(self):
        # Exactly what the database holds after the governed reprocess:
        # ten product rows summing to 159.04, two charges summing to 32.00
        # inside a printed subtotal of 191.04, and both charges stored with
        # quantity 0 because null could not be persisted.
        products = [
            self._product(0, 12, "7.16", "85.92"), self._product(1, 0, "14.85", "0.00"),
            self._product(2, 0, "14.61", "0.00"), self._product(3, 2, "0.7106", "1.42"),
            self._product(4, 4, "10.22", "40.88"), self._product(5, 1, "14.62", "14.62"),
            self._product(6, 3, "3.07", "9.21"), self._product(7, 2, "0.3553", "0.71"),
            self._product(8, 0, "0.00", "0.00"), self._product(9, 2, "0.0562", "0.11"),
            self._product(10, 2, "1.97", "3.94"), self._product(11, 0, "14.83", "0.00"),
            self._product(12, 1, "0.3398", "0.34"), self._product(13, 1, "1.89", "1.89"),
        ]
        invoice = self._invoice(
            *products, self._charge(14, 0, "29.00", "29.00"), self._charge(15, 0, "3.00", "3.00"),
            subtotal="191.04", tax_amount="11.26", grand_total="202.30",
        )
        math_results = check_line_item_math(invoice, TOL)
        assert not [c for c in math_results if c.status.value == "FAILED"]
        assert check_subtotal(invoice, TOL)[0].status.value == "PASSED"
        assert check_grand_total_math(invoice, TOL)[0].status.value == "PASSED"
