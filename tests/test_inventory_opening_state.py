"""
The opening state is derived, never invented — tests/test_inventory_opening_state.py

A store with neither a zero start nor a count has unknown stock (NOT_SET), which is not zero; a zero start
is an administrator's declaration; only person-entered counts make a store COUNTED.
"""

from __future__ import annotations

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
