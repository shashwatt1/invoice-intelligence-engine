"""
tests/test_reconciliation_rule_c.py — discount AND deposit on one line.

Rocco J. Testani 228245 prints PRICE, DISC and DEP as separate columns
and extends them as:

    EXT = (PRICE - DISC + DEP) x QTY

verified against four independent printed controls on that invoice
(Cases 86, Total Deposit 80.10, Total Sales 2,053.02, Invoice Total
2,058.02). Rules A and B each explain one component, so nineteen rows
were left unresolved even though the document had already supplied every
figure needed to prove the net cost.

These tests pin the rule ladder itself: which rule fires for which shape
of line, and — just as important — that a line the arithmetic cannot
explain stays unresolved rather than being quietly repaired.
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.normalized import NormalizedInvoice, NormalizedLineItem
from app.services.validation.reconciliation import reconcile_invoice

TOLERANCE = Decimal("0.02")


def line(qty, price, total, discount=None, deposit=None, sort_order=0):
    return NormalizedLineItem(
        description="ITEM",
        quantity=Decimal(str(qty)),
        unit_price=Decimal(str(price)),
        line_total=Decimal(str(total)),
        unit_discount=None if discount is None else Decimal(str(discount)),
        unit_deposit=None if deposit is None else Decimal(str(deposit)),
        sort_order=sort_order,
    )


def run(*items, subtotal=None):
    invoice = NormalizedInvoice(line_items=tuple(items), subtotal=subtotal)
    return reconcile_invoice(invoice, TOLERANCE)


def names(checks):
    return [c.name for c in checks]


class TestRuleLadder:
    def test_price_only_needs_no_repair(self):
        # BEATBOX MALT MYSTIC: 2 x 34.50 = 69.00, no discount, no deposit.
        result, checks = run(line(2, "34.50", "69.00", discount="0", deposit="0"))
        assert result.line_items[0].unit_price == Decimal("34.50")
        assert result.line_items[0].unit_price_reconciled is False
        assert "UNIT_PRICE_RECONCILED" not in names(checks)

    def test_price_plus_discount_uses_rule_a(self):
        # Balkan shape: gross 19.41 - 0.45 = 18.96, no deposit.
        result, checks = run(line(1, "19.41", "18.96", discount="0.45"))
        assert result.line_items[0].unit_price == Decimal("18.96")
        assert result.line_items[0].unit_price_reconciled is True
        assert "UNIT_PRICE_RECONCILED" in names(checks)

    def test_price_plus_deposit_uses_rule_b_and_keeps_the_price(self):
        # Balkan shape: 50.20 cost + 1.20 deposit = 51.40 extended.
        result, checks = run(line(1, "50.20", "51.40", discount="0", deposit="1.20"))
        assert result.line_items[0].unit_price == Decimal("50.20")  # NOT altered
        assert result.line_items[0].unit_price_reconciled is False
        assert "LINE_TOTAL_INCLUDES_DEPOSIT" in names(checks)
        assert "UNIT_PRICE_RECONCILED" not in names(checks)

    def test_price_discount_and_deposit_together_uses_rule_c(self):
        # Testani BUD 2/12 NR: (24.45 - 1.70 + 1.20) x 1 = 23.95.
        result, checks = run(line(1, "24.45", "23.95", discount="1.70", deposit="1.20"))
        assert result.line_items[0].unit_price == Decimal("22.75")  # net of discount only
        assert result.line_items[0].unit_price_reconciled is True
        assert "UNIT_PRICE_RECONCILED" in names(checks)
        # The deposit is reported separately: it is not part of product cost.
        assert "LINE_TOTAL_INCLUDES_DEPOSIT" in names(checks)

    def test_rule_c_across_a_quantity_greater_than_one(self):
        # Testani BUD LT 3/8 16Z: (30.05 - 4.90 + 1.20) x 5 = 131.75.
        result, _ = run(line(5, "30.05", "131.75", discount="4.90", deposit="1.20"))
        assert result.line_items[0].unit_price == Decimal("25.15")

    def test_deposit_is_never_folded_into_the_product_cost(self):
        # The value that becomes the PDI case cost must be PRICE - DISC,
        # never PRICE - DISC + DEP, and never the extended total.
        result, _ = run(line(8, "22.70", "155.20", discount="4.05", deposit="0.75"))
        net = result.line_items[0].unit_price
        assert net == Decimal("18.65")
        assert net != Decimal("19.40")          # would be net + deposit
        assert net != Decimal("22.70")          # would be gross
        assert net != Decimal("155.20")         # would be the extended total


class TestToleranceAndRefusal:
    def test_rounding_inside_tolerance_still_reconciles(self):
        # Testani RITA: (30.04 - 2.32 + 0.60) x 1 = 28.32 exactly; nudge
        # the printed total by a cent to prove tolerance is honoured.
        result, _ = run(line(1, "30.04", "28.33", discount="2.32", deposit="0.60"))
        assert result.line_items[0].unit_price == Decimal("27.72")

    def test_a_difference_beyond_tolerance_stays_unresolved(self):
        result, checks = run(line(1, "30.04", "29.50", discount="2.32", deposit="0.60"))
        assert result.line_items[0].unit_price == Decimal("30.04")   # untouched
        assert "LINE_ITEM_UNRECONCILED" in names(checks)

    def test_a_genuine_extraction_error_is_not_repaired(self):
        # Testani LAB ICE: the model copied the previous row's extended
        # total (48.40 instead of 134.55). No combination of the printed
        # discount and deposit explains that, so it must stay unresolved
        # and route to review rather than be massaged into agreement.
        result, checks = run(line(9, "14.35", "48.40", discount="0", deposit="0.60"))
        assert result.line_items[0].unit_price == Decimal("14.35")
        assert result.line_items[0].unit_price_reconciled is False
        assert "LINE_ITEM_UNRECONCILED" in names(checks)
        assert "UNIT_PRICE_RECONCILED" not in names(checks)

    def test_missing_discount_and_deposit_cannot_invent_a_repair(self):
        # Nothing on the document to prove anything with.
        result, checks = run(line(1, "30.05", "26.35"))
        assert result.line_items[0].unit_price == Decimal("30.05")
        assert "LINE_ITEM_UNRECONCILED" in names(checks)

    def test_a_null_price_is_left_alone(self):
        item = NormalizedLineItem(
            description="LAB 30 PACK CANS", quantity=Decimal("1"),
            unit_price=None, line_total=None, sort_order=0,
        )
        result, checks = run(item)
        assert result.line_items[0].unit_price is None
        assert "LINE_ITEM_UNRECONCILED" not in names(checks)


class TestTestaniInvoiceShape:
    def test_the_real_invoice_reconciles_and_sums_to_the_printed_control(self):
        # Nine representative rows carrying every shape on 228245.
        rows = [
            (2, "34.50", "69.00", "0", "0"),        (1, "34.50", "35.70", "0", "1.20"),
            (4, "16.45", "66.60", "0.70", "0.90"),  (1, "29.25", "26.85", "3.15", "0.75"),
            (5, "30.05", "131.75", "4.90", "1.20"), (7, "18.10", "123.20", "1.40", "0.90"),
            (8, "22.70", "155.20", "4.05", "0.75"), (1, "34.40", "33.84", "1.31", "0.75"),
            (3, "20.30", "62.70", "0", "0.60"),
        ]
        result, checks = run(*(
            line(q, p, t, discount=d, deposit=dep, sort_order=i)
            for i, (q, p, t, d, dep) in enumerate(rows)
        ))
        assert "LINE_ITEM_UNRECONCILED" not in names(checks)

        # Every reconciled cost equals the printed PRICE - DISC.
        for item, (_, price, _, discount, _) in zip(result.line_items, rows, strict=True):
            assert item.unit_price == Decimal(price) - Decimal(discount)

        # And the net goods total is the extended total less the deposits,
        # which is what "product cost" has to mean for the EDI.
        net_goods = sum(
            (i.unit_price * i.quantity for i in result.line_items), Decimal("0")
        )
        deposits = sum(
            (Decimal(dep) * q for q, _, _, _, dep in rows), Decimal("0")
        )
        extended = sum((Decimal(t) for _, _, t, _, _ in rows), Decimal("0"))
        assert net_goods + deposits == extended
