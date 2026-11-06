"""
Vendor name matching, the delivery-date rule, the vendor-agrees check and the Retailed declaration gate.

Pure logic, no database: the stored behaviour is covered in tests/integration/test_vendor_delivery_db.py.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.core.exceptions import ValidationError
from app.models.invoice import SOURCE_EXTRACTED, SOURCE_OPERATOR, SOURCE_REQUESTED
from app.services.invoice_workflow_service import attestation_fingerprint, missing_declarations
from app.services.upload_choices import parse_delivery_date
from app.services.validation.checks import CheckStatus, check_vendor_matches_choice
from app.services.vendor_matching import (
    ExpectedVendor,
    names_match,
    normalize_vendor_name,
    tax_ids_match,
    vendor_agrees,
)


class TestVendorNames:
    @pytest.mark.parametrize("a,b", [
        ("T.J. Sheehan", "TJ SHEEHAN"),
        ("T J Sheehan Distributing Inc.", "T.J. Sheehan"),
        ("PEPSICO", "PepsiCo Pepsi Beverages Company"),
        ("Acme Foods LLC", "ACME"),
        ("Smith & Sons", "Smith and Sons Inc"),
    ])
    def test_the_same_vendor_under_another_spelling_matches(self, a, b):
        assert names_match(a, b)
        assert names_match(b, a)

    @pytest.mark.parametrize("a,b", [
        ("Acme Foods", "Zenith Foods"),
        ("PepsiCo", "Coca-Cola Bottling"),
        ("", "Acme"),
        (None, None),
    ])
    def test_a_different_vendor_does_not_match(self, a, b):
        assert not names_match(a, b)

    def test_a_name_made_only_of_generic_words_still_compares_with_itself(self):
        assert names_match("The Company Inc", "The Company Inc")

    def test_initials_alone_never_make_a_subset_match(self):
        assert not names_match("AB", "AB Wholesale Traders")

    def test_the_normalised_form_is_order_and_punctuation_free(self):
        assert normalize_vendor_name("Sheehan, T.J.") == normalize_vendor_name("T J SHEEHAN")

    def test_tax_ids_ignore_punctuation_and_empties_never_match(self):
        assert tax_ids_match("12-3456789", "123456789")
        assert not tax_ids_match("", "")
        assert not tax_ids_match(None, "1")


class TestVendorAgrees:
    def test_nothing_printed_means_nothing_to_compare(self):
        assert vendor_agrees(["Acme"], None) is None
        assert vendor_agrees(["Acme"], "  ", extracted_tax_id=" ") is None

    def test_a_shared_tax_id_agrees_whatever_the_spelling(self):
        assert vendor_agrees(["Acme"], "Totally Other Name", selected_tax_id="11-222", extracted_tax_id="11222") is True

    def test_either_known_name_of_the_chosen_vendor_may_match(self):
        assert vendor_agrees(["Registered Legal Name Ltd", "Acme"], "ACME STORE DELIVERY") is True

    def test_a_different_printed_vendor_disagrees(self):
        assert vendor_agrees(["Acme"], "Zenith") is False


class TestVendorChoiceCheck:
    expected = ExpectedVendor(names=("Acme",), tax_id=None)

    def test_no_choice_adds_no_check(self):
        assert check_vendor_matches_choice("Anything", None, None) == []

    def test_agreement_passes(self):
        (result,) = check_vendor_matches_choice("ACME Foods", None, self.expected)
        assert result.status is CheckStatus.PASSED

    def test_a_blank_vendor_is_skipped_not_failed(self):
        (result,) = check_vendor_matches_choice(None, None, self.expected)
        assert result.status is CheckStatus.SKIPPED

    def test_a_mismatch_fails_and_names_both_vendors(self):
        (result,) = check_vendor_matches_choice("Zenith", None, self.expected)
        assert result.status is CheckStatus.FAILED
        assert "Zenith" in result.message and "Acme" in result.message

    def test_a_mismatch_is_not_a_critical_check(self):
        from app.services.validation.report import CRITICAL_CHECKS
        assert CRITICAL_CHECKS, "the critical set must be found, or this test proves nothing"
        assert "VENDOR_MATCHES_CHOICE" not in CRITICAL_CHECKS, "a vendor mismatch is a review reason, never a pause"

    def test_expected_vendor_of_a_vendor_row(self):
        vendor = SimpleNamespace(display_name="Acme Distributing", name="ACME DIST", tax_id="9")
        assert ExpectedVendor.of(vendor) == ExpectedVendor(names=("Acme Distributing", "ACME DIST"), tax_id="9")
        assert ExpectedVendor.of(None) is None


NOW = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)


class TestDeliveryDate:
    def test_none_and_blank_mean_not_given(self):
        assert parse_delivery_date(None, now=NOW) is None
        assert parse_delivery_date("  ", now=NOW) is None

    def test_a_plain_date_is_kept_exactly(self):
        assert parse_delivery_date("2026-10-22", now=NOW) == date(2026, 10, 22)

    def test_today_and_utc_tomorrow_are_accepted_so_a_us_evening_is_never_the_future(self):
        assert parse_delivery_date("2026-11-01", now=NOW) == date(2026, 11, 1)
        assert parse_delivery_date("2026-11-02", now=NOW) == date(2026, 11, 2)

    def test_two_days_ahead_is_refused(self):
        with pytest.raises(ValidationError) as raised:
            parse_delivery_date("2026-11-03", now=NOW)
        assert raised.value.detail["reason"] == "future_date"

    @pytest.mark.parametrize("bad", ["22/10/2026", "yesterday", "2026-13-01", "2026-02-30"])
    def test_a_malformed_date_is_refused(self, bad):
        with pytest.raises(ValidationError) as raised:
            parse_delivery_date(bad, now=NOW)
        assert raised.value.detail["reason"] == "invalid"

    def test_an_absurdly_old_date_is_refused(self):
        with pytest.raises(ValidationError) as raised:
            parse_delivery_date("1999-12-31", now=NOW)
        assert raised.value.detail["reason"] == "too_old"


def _invoice(**kw):
    base = {"delivery_date": date(2026, 10, 22), "vendor_source": SOURCE_OPERATOR}
    base.update(kw)
    return SimpleNamespace(**base)


class TestRetailedDeclarationGate:
    def test_a_declared_vendor_and_date_pass(self):
        assert missing_declarations(_invoice()) is None

    def test_a_missing_delivery_date_blocks(self):
        assert "delivery date" in missing_declarations(_invoice(delivery_date=None))

    def test_a_vendor_still_awaiting_approval_does_not_block(self):
        # The invoice is processed all the way; the administrator's approval only attaches the vendor afterwards.
        assert missing_declarations(_invoice(vendor_source=SOURCE_REQUESTED)) is None
        assert "delivery date" in missing_declarations(_invoice(vendor_source=SOURCE_REQUESTED, delivery_date=None))

    @pytest.mark.parametrize("source", [SOURCE_EXTRACTED, None])
    def test_a_vendor_nobody_chose_blocks(self, source):
        assert "vendor list" in missing_declarations(_invoice(vendor_source=source))

    def test_both_missing_are_both_named(self):
        message = missing_declarations(_invoice(delivery_date=None, vendor_source=None))
        assert "delivery date" in message and "vendor list" in message

    def test_an_invoice_that_predates_the_columns_is_treated_as_undeclared(self):
        assert missing_declarations(SimpleNamespace()) is not None


class TestAttestationFingerprintStaysStableForOldInvoices:
    def test_the_delivery_date_joins_the_fingerprint_only_when_set(self):
        import inspect
        source = inspect.getsource(attestation_fingerprint)
        assert "delivery_date" in source and "is not None" in source
