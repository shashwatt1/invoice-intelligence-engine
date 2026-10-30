"""
The opening state is derived, never invented — tests/test_inventory_opening_state.py

A store with neither a zero start nor a count has unknown stock (NOT_SET), which is not zero; a zero start
is an administrator's declaration; only person-entered counts make a store COUNTED.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import inventory_service as inv


@pytest.mark.parametrize("has_baseline, counted, expected", [
    (False, 0, inv.OPENING_NOT_SET),
    (True, 0, inv.OPENING_ZERO_START),
    (False, 3, inv.OPENING_COUNTED),
    (True, 3, inv.OPENING_COUNTED),            # a physical count outranks the declaration
])
def test_opening_state(has_baseline, counted, expected):
    assert inv.opening_state(has_baseline, counted) == expected


def test_the_states_are_the_three_the_page_names():
    assert inv.OPENING_STATES == (inv.OPENING_NOT_SET, inv.OPENING_ZERO_START, inv.OPENING_COUNTED)


def balance(on_hand):
    return SimpleNamespace(on_hand=None if on_hand is None else Decimal(on_hand))


class TestOnHandUnits:
    def test_no_figure_at_all_is_unknown_not_zero(self):
        assert inv.on_hand_units([]) is None
        assert inv.on_hand_units([balance(None), balance(None)]) is None

    def test_a_real_zero_is_zero(self):
        assert inv.on_hand_units([balance("0")]) == Decimal("0")

    def test_only_products_with_a_figure_are_summed_and_a_negative_never_reduces_the_total(self):
        assert inv.on_hand_units([balance("10"), balance(None), balance("-4"), balance("2.5")]) == Decimal("12.5")


class TestAZeroStartOnlyChangesWhatWasUnknown:
    def test_a_store_that_started_at_zero_has_zero_available_even_with_no_product_yet(self):
        assert inv.on_hand_units([], has_baseline=True) == Decimal("0")
        assert inv.on_hand_units([], has_baseline=False) is None

    @staticmethod
    def rows(count, quantity="3", day=date(2026, 9, 1)):
        return [{"item_code": f"888{i:08d}", "movement_type": "RECEIPT", "business_date": day,
                 "quantity": Decimal(quantity), "description": f"P{i}"} for i in range(count)]

    def test_twenty_received_products_need_an_opening_count_until_the_store_starts_at_zero(self):
        before = inv.fold_balances(self.rows(20))
        assert len(before) == 20 and all(b.status == inv.STATUS_OPENING_REQUIRED and b.on_hand is None for b in before)
        assert inv.on_hand_units(before) is None
        after = inv.fold_balances(self.rows(20), date(2026, 9, 1))
        assert len(after) == 20                                         # the catalogue is the same twenty products
        assert all(b.opening_quantity == 0 and b.opening_source == inv.OPENING_SOURCE_BASELINE for b in after)
        assert all(b.status == inv.STATUS_IN_STOCK and b.on_hand == 3 for b in after)
        assert inv.on_hand_units(after, True) == Decimal("60")

    def test_a_receipt_dated_before_the_start_still_counts_exactly_once(self):
        [balance] = inv.fold_balances(self.rows(1, "5", date(2026, 8, 1)), date(2026, 9, 1))
        assert (balance.received, balance.on_hand) == (5, 5)

    def test_a_products_own_count_outranks_the_zero_start(self):
        rows = self.rows(2) + [{"item_code": "88800000000", "movement_type": "OPENING_COUNT", "business_date": date(2026, 9, 1),
                                "quantity": Decimal("10"), "description": "P0"}]
        counted, started = inv.fold_balances(rows, date(2026, 9, 1))
        assert (counted.opening_source, counted.on_hand) == (inv.OPENING_SOURCE_COUNT, 10)
        assert (started.opening_source, started.on_hand) == (inv.OPENING_SOURCE_BASELINE, 3)


class TestCatalogueProductsHaveNoMovementButAreBalances:
    CATALOGUE = {f"777{i:08d}": f"ITEM {i}" for i in range(20)}

    def test_twenty_catalogue_products_need_a_count_until_the_store_starts_at_zero(self):
        before = inv.fold_balances([], None, self.CATALOGUE)
        assert len(before) == 20
        assert all(b.status == inv.STATUS_OPENING_REQUIRED and b.on_hand is None and b.last_movement is None for b in before)
        assert inv.on_hand_units(before) is None
        after = inv.fold_balances([], date(2026, 10, 30), self.CATALOGUE)
        assert len(after) == 20
        assert all(b.opening_quantity == 0 and b.on_hand == 0 and b.status == inv.STATUS_OUT_OF_STOCK
                   and b.opening_source == inv.OPENING_SOURCE_BASELINE for b in after)
        assert inv.on_hand_units(after, True) == Decimal("0")

    def test_a_product_with_movements_and_a_catalogue_entry_is_one_balance_and_counted_once(self):
        rows = [{"item_code": "77700000000", "movement_type": "RECEIPT", "business_date": date(2026, 10, 1),
                 "quantity": Decimal("12"), "description": "FROM MOVEMENT"}]
        balances = inv.fold_balances(rows, date(2026, 10, 1), self.CATALOGUE)
        assert len(balances) == 20
        first = next(b for b in balances if b.item_code == "77700000000")
        assert (first.received, first.on_hand, first.description) == (12, 12, "FROM MOVEMENT")
        assert inv.on_hand_units(balances, True) == Decimal("12")                    # the receipt is not added twice

    def test_a_catalogue_description_fills_in_when_no_movement_names_the_product(self):
        [balance] = inv.fold_balances([], None, {"77700000001": "SOLD AS LAGER"})
        assert balance.description == "SOLD AS LAGER"

    def test_no_catalogue_changes_nothing(self):
        assert inv.fold_balances([], None, None) == inv.fold_balances([], None, {}) == []
