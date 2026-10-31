"""
Typed additional charges and the grand-total reconciliation — tests/test_typed_charges.py

A state container deposit printed as "State/Local Charges" is neither sales tax nor merchandise nor the invoice's
own deposit figure. Without somewhere to put it the benchmark PepsiCo invoice could never balance:
merchandise 1,821.18 + sales tax 0.00 + state/local charges 23.30 = amount due 1,844.48.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.extraction import ExtractedCharge, ExtractedInvoice, ExtractedVendor
from app.schemas.normalized import NormalizedCharge, NormalizedInvoice
from app.services.validation.checks import check_grand_total_math, typed_other_charges
from app.services.validation.normalization import normalize_invoice
from app.services.validation.report import CheckStatus

TOL = Decimal("0.02")
D = Decimal


def invoice(**kw) -> NormalizedInvoice:
    base = {"subtotal": D("1821.18"), "tax_amount": D("0.00"), "grand_total": D("1844.48")}
    return NormalizedInvoice(**(base | kw))


def charge(kind="state_container_deposit", amount="23.30", label="NY St Deposit @ .05"):
    return NormalizedCharge(kind=kind, label=label, amount=D(amount))


class TestTheBenchmarkInvoiceBalances:
    def test_merchandise_plus_tax_plus_state_local_charges_is_the_amount_due(self):
        [result] = check_grand_total_math(invoice(other_charges=(charge(),)), TOL)
        assert result.status is CheckStatus.PASSED

    def test_without_the_typed_charge_it_cannot_balance(self):
        [result] = check_grand_total_math(invoice(), TOL)
        assert result.status is CheckStatus.FAILED
        assert result.expected == "1821.18" and result.actual == "1844.48"

    def test_a_wrong_charge_amount_still_fails(self):
        [result] = check_grand_total_math(invoice(other_charges=(charge(amount="20.00"),)), TOL)
        assert result.status is CheckStatus.FAILED

    def test_several_charges_add_up(self):
        charges = (charge(amount="20.00"), charge(kind="jurisdiction_charge", amount="3.30", label="local fee"))
        [result] = check_grand_total_math(invoice(other_charges=charges), TOL)
        assert result.status is CheckStatus.PASSED


class TestChargesAreNotTaxOrDeposit:
    def test_the_state_charge_is_not_added_to_tax_or_deposit_figures(self):
        inv = invoice(other_charges=(charge(),))
        assert (inv.tax_amount, inv.deposit_total, inv.fuel_surcharge) == (D("0.00"), None, None)
        assert inv.other_charges_total == D("23.30")

    def test_a_delivery_charge_is_not_counted_twice_when_the_invoice_already_has_a_fuel_figure(self):
        inv = invoice(subtotal=D("100.00"), grand_total=D("105.00"), fuel_surcharge=D("5.00"),
                      other_charges=(charge(kind="delivery", amount="5.00", label="delivery"),))
        assert typed_other_charges(inv) == D("0.00")
        assert check_grand_total_math(inv, TOL)[0].status is CheckStatus.PASSED

    def test_a_deposit_kind_is_counted_even_beside_a_fuel_figure(self):
        inv = invoice(subtotal=D("100.00"), grand_total=D("128.30"), fuel_surcharge=D("5.00"),
                      other_charges=(charge(),))
        assert typed_other_charges(inv) == D("23.30")
        assert check_grand_total_math(inv, TOL)[0].status is CheckStatus.PASSED


class TestNormalization:
    def extracted(self, charges):
        return ExtractedInvoice(vendor=ExtractedVendor(name="PEPSICO"), invoice_number="1", currency="USD",
                                subtotal=1821.18, grand_total=1844.48, line_items=[], other_charges=charges)

    def test_typed_charges_survive_normalization_as_exact_decimals(self):
        normalized, _ = normalize_invoice(self.extracted(
            [ExtractedCharge(kind="state_container_deposit", label=" NY St Deposit @ .05 ", amount=23.30)]))
        assert normalized.other_charges == (charge(),)

    def test_a_negative_or_zero_charge_is_normalized_to_a_positive_amount_or_dropped(self):
        normalized, _ = normalize_invoice(self.extracted([
            ExtractedCharge(kind="other", label="credit", amount=-4.5), ExtractedCharge(kind="other", amount=0)]))
        assert [c.amount for c in normalized.other_charges] == [D("4.50")]

    def test_no_charges_is_the_empty_tuple(self):
        normalized, _ = normalize_invoice(self.extracted([]))
        assert normalized.other_charges == ()

    @pytest.mark.parametrize("kind", ["state_container_deposit", "jurisdiction_charge", "delivery", "fuel", "service", "other"])
    def test_every_documented_kind_is_accepted(self, kind):
        assert ExtractedCharge(kind=kind, amount=1.0).kind == kind

    def test_sales_tax_is_not_a_charge_kind(self):
        with pytest.raises(ValueError):
            ExtractedCharge(kind="sales_tax", amount=1.0)
