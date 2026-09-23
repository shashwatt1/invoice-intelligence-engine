"""
tests/test_quantity_reconciliation.py — Rule E: a quantity the invoice's
own arithmetic proves is corrupted.

UniFirst 2310090549 (RCM, 18 Sep 2026) is read by Vision as a columnar
stream. Two OCR artifacts corrupt the QTY column:

  * five vertically adjacent single-digit cells (2, 4, 1, 3, 2) merge into
    one word token, "24132";
  * the LAUNDRY BAGS row's printed TOTAL of 2.02 loses its decimal point
    and becomes "202", which sits immediately before the next row's item
    code — exactly where this layout prints a quantity.

Neither is recoverable from the text alone, so the model emits quantities
that contradict their own rows. The printed figures, however, prove what
the quantity must have been: line_total / unit_price is a whole number,
and the line totals themselves are corroborated by the printed subtotal.

Rule E corrects a quantity only on that double proof, and only after
Rules A-D have had their say. Arithmetic alone is never enough: for
quantity 1, unit_price 10.22, line_total 40.88 the quantity could be 4 or
the line total could be 10.22. The invoice-level control is what breaks
the tie — without it Rule E declines.
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.normalized import NormalizedInvoice, NormalizedLineItem
from app.services.validation.reconciliation import reconcile_invoice

TOL = Decimal("0.02")


def line(sort_order, desc, qty, price, total, *, line_type="product", deposit=None, discount=None):
    return NormalizedLineItem(
        sort_order=sort_order, description=desc, line_type=line_type,
        quantity=None if qty is None else Decimal(str(qty)),
        unit_price=None if price is None else Decimal(str(price)),
        line_total=None if total is None else Decimal(str(total)),
        unit_deposit=None if deposit is None else Decimal(str(deposit)),
        unit_discount=None if discount is None else Decimal(str(discount)),
    )


def unifirst(*, subtotal="191.04", quantities=(24132, 1, 1, 1, 1), charge_qty=202):
    """
    The real shape: ten product rows whose line totals sum to 159.04, two
    charge rows summing to 32.00, and a printed subtotal of 191.04 that
    already contains the charges. `quantities` are the corrupted values
    the model emitted for the five rows the merged token covered.
    """
    q = list(quantities)
    products = [
        line(0, "2PLY MINI TWIN TT(EACH)ROLL #4", 12, "7.16", "85.92"),
        line(1, "JUMBO BATH TISSUE DISPENSER", q[0], "0.7106", "1.42"),
        line(2, "MAT-4X6 GREAT IMP 2.0", q[1], "10.22", "40.88"),
        line(3, "MAT-3X10 GREAT IMP 2.0", q[2], "14.62", "14.62"),
        line(4, "WET MOP LARGE WITH RED BAND 24", q[3], "3.07", "9.21"),
        line(5, "MOPS-HANDLE 1 1/8 X 60", q[4], "0.3553", "0.71"),
        line(6, "DISPN-METERED AEROSAL AIR FRES", 2, "0.0562", "0.11"),
        line(7, "AIR FRESH METERD SPRAY-LINEN 9", 2, "1.97", "3.94"),
        line(8, "BAG RACK", 1, "0.3398", "0.34"),
        line(9, "LAUNDRY BAGS-SPECIAL", 1, "1.89", "1.89"),
    ]
    charges = [
        line(10, "DEFE Charge Fixed", charge_qty, "29.00", "29.00", line_type="charge"),
        line(11, "Energy Surcharge", 1, "3.00", "3.00", line_type="charge"),
    ]
    return NormalizedInvoice(
        line_items=tuple(products + charges),
        subtotal=None if subtotal is None else Decimal(subtotal),
        tax_amount=Decimal("11.26"), grand_total=Decimal("202.30"),
    )


def quantities_of(invoice):
    return {i.sort_order: i.quantity for i in invoice.line_items}


class TestRuleERecoversAProvenQuantity:
    def test_the_merged_token_row_and_its_neighbours_are_recovered(self):
        out, checks = reconcile_invoice(unifirst(), TOL)
        q = quantities_of(out)
        assert q[1] == Decimal("2")     # 1.42  / 0.7106 -> 2
        assert q[2] == Decimal("4")     # 40.88 / 10.22  -> 4
        assert q[3] == Decimal("1")     # 14.62 / 14.62  -> 1
        assert q[4] == Decimal("3")     # 9.21  / 3.07   -> 3
        assert q[5] == Decimal("2")     # 0.71  / 0.3553 -> 2
        # Four corrections, not five: the model's 1 for MAT-3X10 happened to
        # be the true quantity, so that row already balanced and Rule E left
        # it alone rather than "correcting" it to itself.
        assert [c.name for c in checks].count("QUANTITY_RECONCILED") == 4

    def test_rows_that_already_balance_are_untouched(self):
        out, _ = reconcile_invoice(unifirst(), TOL)
        q = quantities_of(out)
        assert q[0] == Decimal("12") and q[6] == Decimal("2") and q[7] == Decimal("2")
        assert q[8] == Decimal("1") and q[9] == Decimal("1")

    def test_the_check_records_the_old_value_the_new_value_and_the_proof(self):
        _, checks = reconcile_invoice(unifirst(), TOL)
        proven = [c for c in checks if c.name == "QUANTITY_RECONCILED"]
        mat = next(c for c in proven if c.field == "line_items[2].quantity")
        assert mat.actual == "1" and mat.expected == "4"
        assert "line_total / unit_price" in mat.message
        assert "subtotal" in mat.message
        assert mat.status.value == "PASSED"

    def test_a_second_pass_changes_nothing(self):
        once, first = reconcile_invoice(unifirst(), TOL)
        twice, second = reconcile_invoice(once, TOL)
        assert quantities_of(twice) == quantities_of(once)
        assert [c.name for c in second].count("QUANTITY_RECONCILED") == 0
        assert [c.name for c in first].count("QUANTITY_RECONCILED") == 4

    def test_the_repaired_invoice_now_reconciles_against_its_printed_subtotal(self):
        out, _ = reconcile_invoice(unifirst(), TOL)
        products = sum((i.line_total for i in out.line_items if i.line_type == "product"), Decimal("0"))
        charges = sum((i.line_total for i in out.line_items if i.line_type == "charge"), Decimal("0"))
        assert products == Decimal("159.04") and charges == Decimal("32.00")
        assert products + charges == out.subtotal
        for i in out.line_items:
            if i.line_type == "product" and i.quantity:
                assert abs(i.quantity * i.unit_price - i.line_total) <= TOL


class TestRuleEDeclinesWithoutProof:
    def test_no_printed_subtotal_means_no_correction(self):
        out, checks = reconcile_invoice(unifirst(subtotal=None), TOL)
        assert quantities_of(out)[2] == Decimal("1")
        assert "QUANTITY_RECONCILED" not in [c.name for c in checks]

    def test_a_subtotal_that_does_not_corroborate_the_line_totals_means_no_correction(self):
        # The tie-break: with the line totals unproven, the corrupted field
        # could be the line total rather than the quantity.
        out, checks = reconcile_invoice(unifirst(subtotal="999.99"), TOL)
        assert quantities_of(out)[2] == Decimal("1")
        assert "QUANTITY_RECONCILED" not in [c.name for c in checks]

    def test_a_non_integer_implied_quantity_is_never_rounded_into_place(self):
        # 95.55 / 18.11 = 5.2761 — observed on a real unresolved row.
        inv = NormalizedInvoice(
            line_items=(line(0, "X", 5, "18.11", "95.55"),),
            subtotal=Decimal("95.55"), grand_total=Decimal("95.55"),
        )
        out, checks = reconcile_invoice(inv, TOL)
        assert out.line_items[0].quantity == Decimal("5")
        assert "QUANTITY_RECONCILED" not in [c.name for c in checks]

    def test_a_charge_row_never_has_a_quantity_invented_or_corrected(self):
        out, checks = reconcile_invoice(unifirst(), TOL)
        charge = next(i for i in out.line_items if i.description == "DEFE Charge Fixed")
        assert charge.quantity == Decimal("202")          # left exactly as extracted
        assert charge.line_total == Decimal("29.00")
        assert not [c for c in checks if c.name == "QUANTITY_RECONCILED"
                    and c.field == "line_items[10].quantity"]

    def test_a_genuine_zero_quantity_row_is_never_given_a_quantity(self):
        inv = NormalizedInvoice(
            line_items=(line(0, "DELIVERED", 2, "10.00", "20.00"),
                        line(1, "SHORT ON TRUCK", 0, "10.00", "0.00")),
            subtotal=Decimal("20.00"), grand_total=Decimal("20.00"),
        )
        out, checks = reconcile_invoice(inv, TOL)
        assert out.line_items[1].quantity == Decimal("0")
        assert out.line_items[1].line_total == Decimal("0.00")
        assert "QUANTITY_RECONCILED" not in [c.name for c in checks]

    def test_a_missing_unit_price_or_line_total_is_never_filled_in(self):
        inv = NormalizedInvoice(
            line_items=(line(0, "NO PRICE", 1, None, "40.88"),
                        line(1, "NO TOTAL", 1, "10.22", None)),
            subtotal=Decimal("40.88"), grand_total=Decimal("40.88"),
        )
        out, checks = reconcile_invoice(inv, TOL)
        assert out.line_items[0].quantity == Decimal("1") and out.line_items[0].unit_price is None
        assert out.line_items[1].quantity == Decimal("1") and out.line_items[1].line_total is None
        assert "QUANTITY_RECONCILED" not in [c.name for c in checks]

    def test_a_unit_price_too_small_to_identify_one_integer_is_declined(self):
        # With unit_price at or below the money tolerance, several integers
        # satisfy the arithmetic; none of them is proven.
        inv = NormalizedInvoice(
            line_items=(line(0, "TINY", 1, "0.01", "1.00"),),
            subtotal=Decimal("1.00"), grand_total=Decimal("1.00"),
        )
        out, checks = reconcile_invoice(inv, TOL)
        assert out.line_items[0].quantity == Decimal("1")
        assert "QUANTITY_RECONCILED" not in [c.name for c in checks]


class TestRulesAToDKeepPrecedence:
    def test_a_row_rule_a_explains_is_not_touched_by_rule_e(self):
        # gross price read instead of net: 2 x (21.05 - 1.65) = 38.80
        inv = NormalizedInvoice(
            line_items=(line(0, "BUSCH LIGHT", 2, "21.05", "38.80", discount="1.65"),),
            subtotal=Decimal("38.80"), grand_total=Decimal("38.80"),
        )
        out, checks = reconcile_invoice(inv, TOL)
        names = [c.name for c in checks]
        assert "UNIT_PRICE_RECONCILED" in names and "QUANTITY_RECONCILED" not in names
        assert out.line_items[0].quantity == Decimal("2")
        assert out.line_items[0].unit_price == Decimal("19.40")

    def test_a_row_rule_b_explains_is_not_touched_by_rule_e(self):
        # deposit folded into the extended total: 2 x (21.05 + 0.75) = 43.60
        inv = NormalizedInvoice(
            line_items=(line(0, "NATTY DADDY", 2, "21.05", "43.60", deposit="0.75"),),
            subtotal=Decimal("42.10"), deposit_total=Decimal("1.50"), grand_total=Decimal("43.60"),
        )
        out, checks = reconcile_invoice(inv, TOL)
        names = [c.name for c in checks]
        assert "LINE_TOTAL_INCLUDES_DEPOSIT" in names and "QUANTITY_RECONCILED" not in names
        assert out.line_items[0].quantity == Decimal("2")
        assert out.line_items[0].unit_price == Decimal("21.05")


class TestTheWholeUniFirstShapeThroughValidation:
    """
    The extraction shape as the model returns it, judged by the real
    ValidationService: v8 column semantics (AMOUNT as the pre-tax line
    total, TAX on its own), charges inside the printed subtotal, and the
    two corrupted quantities. Rule E must recover the products, leave the
    charge alone, and the invoice must then reconcile end to end.
    """

    def _extracted(self, *, charge_quantity=202):
        from app.schemas.extraction import ExtractedInvoice, ExtractedLineItem, ExtractedVendor

        def row(desc, code, qty, price, total, tax, line_type="product"):
            return ExtractedLineItem(description=desc, product_code=code, quantity=qty,
                                     unit_price=price, line_total=total, line_tax=tax,
                                     line_type=line_type)
        return ExtractedInvoice(
            vendor=ExtractedVendor(name="A SERVICE SUPPLIER"), invoice_number="2310090549",
            invoice_date="2026-09-18", subtotal=191.04, tax_amount=11.26, grand_total=202.30,
            amount_due=202.30,
            line_items=[
                row("2PLY MINI TWIN TT(EACH)ROLL #4", "622107", 12, 7.16, 85.92, 6.09),
                row("2 PLY JUMBO TT (EACH) ROLL #50", "622507", 0, 14.85, 0.00, 0.00),
                row("JUMBO BATH TISSUE DISPENSER", "625107", 24132, 0.7106, 1.42, 0.10),
                row("MAT-4X6 GREAT IMP 2.0", "76GB03", 1, 10.22, 40.88, 2.90),
                row("MAT-3X10 GREAT IMP 2.0", "76GC03", 1, 14.62, 14.62, 1.04),
                row("WET MOP LARGE WITH RED BAND 24", "811602", 1, 3.07, 9.21, 0.65),
                row("MOPS-HANDLE 1 1/8 X 60", "813107", 1, 0.3553, 0.71, 0.04),
                row("DISPN-METERED AEROSAL AIR FRES", "870007", 2, 0.0562, 0.11, 0.01),
                row("AIR FRESH METERD SPRAY-LINEN 9", "870100", 2, 1.97, 3.94, 0.27),
                row("BAG RACK", "895612", 1, 0.3398, 0.34, 0.03),
                row("LAUNDRY BAGS-SPECIAL", "907523", 1, 1.89, 1.89, 0.13),
                row("DEFE Charge Fixed", None, charge_quantity, 29.00, 29.00, 0.00, line_type="charge"),
                row("Energy Surcharge", None, 1, 3.00, 3.00, 0.00, line_type="charge"),
            ],
        )

    def test_the_corrupted_quantities_are_recovered_and_the_invoice_reconciles(self):
        from app.services.validation.service import ValidationService

        # As v9 returns it: the charge prints no quantity, so it is null.
        result = ValidationService().validate_invoice(self._extracted(charge_quantity=None))
        by_desc = {i.description: i for i in result.invoice.line_items}
        assert by_desc["JUMBO BATH TISSUE DISPENSER"].quantity == Decimal("2")
        assert by_desc["MAT-4X6 GREAT IMP 2.0"].quantity == Decimal("4")
        assert by_desc["MAT-3X10 GREAT IMP 2.0"].quantity == Decimal("1")
        assert by_desc["WET MOP LARGE WITH RED BAND 24"].quantity == Decimal("3")
        assert by_desc["MOPS-HANDLE 1 1/8 X 60"].quantity == Decimal("2")

        statuses = {c.name: c.status.value for c in result.report.checks}
        assert statuses["SUBTOTAL_MATCHES_ITEMS"] == "PASSED"
        assert statuses["GRAND_TOTAL_MATH"] == "PASSED"
        assert not [c for c in result.report.checks
                    if c.name == "LINE_ITEM_MATH" and c.status.value == "FAILED"]

    def test_a_charge_with_no_printed_quantity_is_null_and_costs_nothing(self):
        from app.services.validation.service import ValidationService

        # v9's shape: a fixed charge counts nothing, so quantity is null.
        # Nothing is invented, and the row does not fail the math check.
        result = ValidationService().validate_invoice(self._extracted(charge_quantity=None))
        charge = next(i for i in result.invoice.line_items if i.description == "DEFE Charge Fixed")
        assert charge.quantity is None
        assert charge.line_type == "charge"
        assert charge.unit_price == Decimal("29.00") and charge.line_total == Decimal("29.00")
        assert not [c for c in result.report.checks
                    if c.name == "LINE_ITEM_MATH" and c.status.value == "FAILED"]

    def test_a_borrowed_quantity_on_a_charge_is_never_adopted_or_silently_repaired(self):
        from app.services.validation.service import ValidationService

        # The pre-v9 shape: 202 is the previous row's printed 2.02 with its
        # decimal point lost. Rule E must not touch a charge row, so the
        # value survives exactly as extracted — nothing is adopted as a
        # count and nothing is quietly corrected away.
        #
        # The row is not FAILED for it either: quantity x unit price is a
        # statement about goods and does not describe a fixed charge (see
        # TestAFixedChargeIsNotMeasuredLikeAProduct). Nothing downstream
        # reads a charge's quantity — charges are excluded from pdi_items,
        # from Rule E and from the unresolved-quantity export gate — so a
        # wrong number there cannot reach the money, the EDI or a mapping.
        result = ValidationService().validate_invoice(self._extracted(charge_quantity=202))
        charge = next(i for i in result.invoice.line_items if i.description == "DEFE Charge Fixed")
        assert charge.quantity == Decimal("202")          # untouched, not repaired
        assert charge.line_total == Decimal("29.00")
        assert not [c for c in result.report.checks
                    if c.name == "QUANTITY_RECONCILED" and c.field == "line_items[11].quantity"]
        [math] = [c for c in result.report.checks
                  if c.name == "LINE_ITEM_MATH" and c.field == "line_items[11]"]
        assert math.status.value == "SKIPPED"
        # and the borrowed number changes none of the invoice's money
        assert result.invoice.subtotal == Decimal("191.04")
        assert result.invoice.grand_total == Decimal("202.30")

    def test_a_genuinely_shorted_row_keeps_quantity_zero(self):
        from app.services.validation.service import ValidationService

        result = ValidationService().validate_invoice(self._extracted())
        shorted = next(i for i in result.invoice.line_items
                       if i.description == "2 PLY JUMBO TT (EACH) ROLL #50")
        assert shorted.quantity == Decimal("0") and shorted.line_total == Decimal("0.00")
