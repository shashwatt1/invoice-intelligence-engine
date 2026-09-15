"""
tests/test_reconciliation.py — Deterministic reconciliation engine.

Every case here is drawn from a real observed failure, not a hypothetical
one. The headline case is the production bug this engine exists for: on a
real 7-line invoice the model read unit_price from the gross
(pre-discount) column while reading line_total from the net column, so
the line totals summed to the invoice's printed "Total Sales" ($288.32)
instead of its "Total Content" ($263.86) — a 9.3% cost overstatement
exactly equal to the printed total discount.
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.normalized import NormalizedInvoice, NormalizedLineItem
from app.services.validation.reconciliation import reconcile_invoice

TOLERANCE = Decimal("0.02")


def _item(**overrides) -> NormalizedLineItem:
    base = {
        "description": "TEST ITEM",
        "quantity": Decimal("1.0000"),
        "unit_price": Decimal("19.4100"),   # gross — the bug
        "unit_discount": Decimal("0.45"),
        "unit_deposit": Decimal("0.00"),
        "line_total": Decimal("18.96"),     # net
        "sort_order": 0,
    }
    base.update(overrides)
    return NormalizedLineItem(**base)


def _names(checks) -> set[str]:
    return {c.name for c in checks}


class TestGrossPriceReconciliation:
    """Rule A — a gross price mistaken for the net one."""

    def test_replaces_gross_unit_price_with_proven_net_price(self):
        invoice = NormalizedInvoice(line_items=(_item(),))
        result, checks = reconcile_invoice(invoice, TOLERANCE)

        assert result.line_items[0].unit_price == Decimal("18.96")
        assert result.line_items[0].unit_price_reconciled is True
        assert "UNIT_PRICE_RECONCILED" in _names(checks)

    def test_correction_makes_the_line_balance(self):
        invoice = NormalizedInvoice(line_items=(_item(),))
        result, _ = reconcile_invoice(invoice, TOLERANCE)
        item = result.line_items[0]
        assert item.quantity * item.unit_price == item.line_total

    def test_the_full_seven_line_production_failure_is_repaired(self):
        # The exact invoice, with the exact wrong values, that motivated
        # this engine. Correct answer: line sum == 263.86, not 288.32.
        rows = [
            ("NESQ MILK 12/14 CHO", "19.41", "0.45", "0.00", "18.96", "18.96"),
            ("NESQ MILK 12/14 STR", "19.41", "0.45", "0.00", "18.96", "18.96"),
            ("RB AMBER APRCT 24/1", "56.50", "6.30", "1.20", "50.20", "50.20"),
            ("RB COCONUT 24/120Z",  "56.50", "6.30", "1.20", "50.20", "50.20"),
            ("RB RED WTRMEL 24/12", "56.50", "6.30", "1.20", "50.20", "50.20"),
            ("RED BULL 12/16OZ CN", "36.50", "3.39", "0.60", "33.11", "33.11"),
            ("RED BULL 12/200Z CN", "43.50", "1.27", "0.60", "42.23", "42.23"),
        ]
        invoice = NormalizedInvoice(
            subtotal=Decimal("263.86"),
            line_items=tuple(
                _item(
                    description=desc,
                    unit_price=Decimal(gross),
                    unit_discount=Decimal(disc),
                    unit_deposit=Decimal(dep),
                    line_total=Decimal(total),
                    sort_order=i,
                )
                for i, (desc, gross, disc, dep, total, _) in enumerate(rows)
            ),
        )
        result, checks = reconcile_invoice(invoice, TOLERANCE)

        assert all(i.unit_price_reconciled for i in result.line_items)
        for item, (_, _, _, _, _, expected_net) in zip(result.line_items, rows, strict=True):
            assert item.unit_price == Decimal(expected_net)

        line_sum = sum(i.unit_price * i.quantity for i in result.line_items)
        assert line_sum == Decimal("263.86")   # net, correct
        assert line_sum != Decimal("288.32")   # gross, the bug
        assert "SUBTOTAL_RECONCILED" in _names(checks)


class TestDepositInclusiveTotals:
    """Rule B — the extended total already contains the deposit."""

    def test_recognizes_deposit_inclusive_line_total_without_changing_price(self):
        # (50.20 + 1.20) x 1 = 51.40. The unit price is already correct
        # and must not be "corrected" into something else.
        item = _item(
            unit_price=Decimal("50.20"),
            unit_discount=None,
            unit_deposit=Decimal("1.20"),
            line_total=Decimal("51.40"),
        )
        result, checks = reconcile_invoice(
            NormalizedInvoice(line_items=(item,)), TOLERANCE
        )
        assert result.line_items[0].unit_price == Decimal("50.20")
        assert result.line_items[0].unit_price_reconciled is False
        assert "LINE_TOTAL_INCLUDES_DEPOSIT" in _names(checks)


class TestNeverFabricates:
    """Reconciliation must repair only what arithmetic proves."""

    def test_leaves_values_untouched_and_flags_when_unexplained(self):
        item = _item(
            unit_price=Decimal("10.00"),
            unit_discount=None,
            unit_deposit=None,
            line_total=Decimal("99.99"),  # nothing on the document explains this
        )
        result, checks = reconcile_invoice(
            NormalizedInvoice(line_items=(item,)), TOLERANCE
        )
        assert result.line_items[0].unit_price == Decimal("10.00")
        assert result.line_items[0].line_total == Decimal("99.99")
        assert result.line_items[0].unit_price_reconciled is False
        assert "LINE_ITEM_UNRECONCILED" in _names(checks)

    def test_does_not_touch_a_line_that_already_balances(self):
        item = _item(
            unit_price=Decimal("10.00"),
            unit_discount=Decimal("2.00"),
            line_total=Decimal("10.00"),
        )
        result, checks = reconcile_invoice(
            NormalizedInvoice(line_items=(item,)), TOLERANCE
        )
        assert result.line_items[0].unit_price == Decimal("10.00")
        assert "UNIT_PRICE_RECONCILED" not in _names(checks)

    def test_handles_missing_values_without_inventing_them(self):
        for missing in ("quantity", "unit_price", "line_total"):
            item = _item(**{missing: None})
            result, checks = reconcile_invoice(
                NormalizedInvoice(line_items=(item,)), TOLERANCE
            )
            assert getattr(result.line_items[0], missing) is None
            assert "UNIT_PRICE_RECONCILED" not in _names(checks)

    def test_zero_quantity_does_not_divide_or_crash(self):
        item = _item(quantity=Decimal("0"), line_total=Decimal("0.00"))
        result, _ = reconcile_invoice(NormalizedInvoice(line_items=(item,)), TOLERANCE)
        assert result.line_items[0].quantity == Decimal("0")


class TestSubtotalCrossCheck:
    def test_flags_when_line_totals_do_not_reach_the_printed_subtotal(self):
        invoice = NormalizedInvoice(
            subtotal=Decimal("999.00"),
            line_items=(_item(unit_discount=None, unit_deposit=None),),
        )
        _, checks = reconcile_invoice(invoice, TOLERANCE)
        assert "SUBTOTAL_UNRECONCILED" in _names(checks)


def _deposit_invoice(prices, *, deposits=("0.60", "0.60", "1.20", "0.90"), qtys=(4, 6, 1, 1),
                     subtotal="164.70", deposit_total="8.10", line_totals=None):
    """Invoice 1000540's shape: four lines, per-line deposits, EXT = NET x qty."""
    items = []
    for i, (p, d, q) in enumerate(zip(prices, deposits, qtys, strict=True)):
        lt = (Decimal(p) * q).quantize(Decimal("0.01")) if line_totals is None else Decimal(line_totals[i])
        items.append(_item(sort_order=i, quantity=Decimal(q), unit_price=Decimal(p),
                           unit_discount=Decimal("0.00"), unit_deposit=Decimal(d), line_total=lt))
    return NormalizedInvoice(
        line_items=tuple(items), subtotal=Decimal(subtotal),
        deposit_total=Decimal(deposit_total) if deposit_total is not None else None,
    )


class TestDepositFoldedIntoUnitPrice:
    """Rule D — unit_price read from a NET column that is PRICE + DEP."""

    def test_the_real_1000540_extraction_is_repaired_from_its_own_totals(self):
        # As the model read it: 15.10 / 9.60 / 32.25 / 22.55 (PRICE + DEP).
        invoice = _deposit_invoice(("15.10", "9.60", "32.25", "22.55"))
        result, checks = reconcile_invoice(invoice, TOLERANCE)
        assert [i.unit_price for i in result.line_items] == [Decimal("14.50"), Decimal("9.00"), Decimal("31.05"), Decimal("21.65")]
        assert all(i.unit_price_reconciled for i in result.line_items)
        proofs = [c for c in checks if c.name == "UNIT_PRICE_INCLUDED_DEPOSIT"]
        assert len(proofs) == 4 and all(c.status.value == "PASSED" for c in proofs)
        assert Decimal(proofs[0].actual) == Decimal("15.10") and Decimal(proofs[0].expected) == Decimal("14.50")
        assert "UNIT_PRICE_MAY_INCLUDE_DEPOSIT" not in _names(checks)
        # the line totals were never touched — they are the document's EXT
        assert [i.line_total for i in result.line_items] == [Decimal("60.40"), Decimal("57.60"), Decimal("32.25"), Decimal("22.55")]
        assert "SUBTOTAL_RECONCILED" in _names(checks)

    def test_goods_only_prices_are_left_alone_even_with_deposits(self):
        # Balkan's shape: unit_price ex-deposit, EXT includes the deposit,
        # Σ unit_price x qty == subtotal. Nothing to prove, nothing touched.
        invoice = _deposit_invoice(("14.50", "9.00", "31.05", "21.65"),
                                   line_totals=("60.40", "57.60", "32.25", "22.55"))
        result, checks = reconcile_invoice(invoice, TOLERANCE)
        assert [i.unit_price for i in result.line_items] == [Decimal("14.50"), Decimal("9.00"), Decimal("31.05"), Decimal("21.65")]
        assert not any(i.unit_price_reconciled for i in result.line_items)
        assert not {"UNIT_PRICE_INCLUDED_DEPOSIT", "UNIT_PRICE_MAY_INCLUDE_DEPOSIT"} & _names(checks)
        assert "LINE_TOTAL_INCLUDES_DEPOSIT" in _names(checks)           # Rule B explains EXT

    def test_no_deposits_means_no_rule(self):
        invoice = _deposit_invoice(("14.50", "9.00", "31.05", "21.65"), deposits=("0.00",) * 4,
                                   deposit_total=None)
        _, checks = reconcile_invoice(invoice, TOLERANCE)
        assert not {"UNIT_PRICE_INCLUDED_DEPOSIT", "UNIT_PRICE_MAY_INCLUDE_DEPOSIT"} & _names(checks)

    def test_a_half_proof_changes_nothing_and_fails_for_review(self):
        # Σ unit_price x qty = subtotal + deposits holds, but one line's
        # deposit is wrong, so Σ (price - deposit) x qty misses the subtotal.
        invoice = _deposit_invoice(("15.10", "9.60", "32.25", "22.55"),
                                   deposits=("0.60", "0.60", "1.20", "0.50"))
        result, checks = reconcile_invoice(invoice, TOLERANCE)
        assert [i.unit_price for i in result.line_items] == [Decimal("15.10"), Decimal("9.60"), Decimal("32.25"), Decimal("22.55")]
        [flag] = [c for c in checks if c.name == "UNIT_PRICE_MAY_INCLUDE_DEPOSIT"]
        assert flag.status.value == "FAILED"
        assert "does not equal the subtotal" in flag.message
        assert "UNIT_PRICE_INCLUDED_DEPOSIT" not in _names(checks)

    def test_the_other_half_proof_also_only_flags(self):
        # Σ (price - deposit) x qty = subtotal, but the printed deposit
        # total disagrees with the per-line deposits.
        invoice = _deposit_invoice(("15.10", "9.60", "32.25", "22.55"), deposit_total="9.00")
        result, checks = reconcile_invoice(invoice, TOLERANCE)
        assert not any(i.unit_price_reconciled for i in result.line_items)
        [flag] = [c for c in checks if c.name == "UNIT_PRICE_MAY_INCLUDE_DEPOSIT"]
        assert "does not equal subtotal + deposits" in flag.message

    def test_a_discounted_line_reconciled_by_rule_a_is_not_double_corrected(self):
        # Rule A repairs a gross price on one line; the invoice as a whole
        # then sums to its goods subtotal, so Rule D has nothing to do.
        items = (
            _item(sort_order=0, quantity=Decimal(1), unit_price=Decimal("19.41"),
                  unit_discount=Decimal("0.45"), unit_deposit=Decimal("0.00"), line_total=Decimal("18.96")),
            _item(sort_order=1, quantity=Decimal(1), unit_price=Decimal("50.20"),
                  unit_discount=Decimal("0.00"), unit_deposit=Decimal("1.20"), line_total=Decimal("51.40")),
        )
        invoice = NormalizedInvoice(line_items=items, subtotal=Decimal("69.16"), deposit_total=Decimal("1.20"))
        result, checks = reconcile_invoice(invoice, TOLERANCE)
        assert [i.unit_price for i in result.line_items] == [Decimal("18.96"), Decimal("50.20")]
        assert "UNIT_PRICE_RECONCILED" in _names(checks)
        assert not {"UNIT_PRICE_INCLUDED_DEPOSIT", "UNIT_PRICE_MAY_INCLUDE_DEPOSIT"} & _names(checks)

    def test_a_line_without_a_price_disables_the_proof(self):
        invoice = _deposit_invoice(("15.10", "9.60", "32.25", "22.55"))
        items = list(invoice.line_items)
        items[2] = items[2].model_copy(update={"unit_price": None})
        invoice = invoice.model_copy(update={"line_items": tuple(items)})
        result, checks = reconcile_invoice(invoice, TOLERANCE)
        assert result.line_items[0].unit_price == Decimal("15.10")
        assert not {"UNIT_PRICE_INCLUDED_DEPOSIT", "UNIT_PRICE_MAY_INCLUDE_DEPOSIT"} & _names(checks)
