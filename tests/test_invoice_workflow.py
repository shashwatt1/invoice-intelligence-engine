"""
tests/test_invoice_workflow.py — invoice reference link and Retailed status (pure parts).

The database end (permissions, the ledger, staleness after changes, locking,
no inventory or mapping side effects) is tests/integration/test_invoice_workflow_db.py.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import ValidationError
from app.services.invoice_workflow_service import (
    MAX_REFERENCE_URL,
    STATUS_NOT_RETAILED,
    STATUS_RETAILED,
    STATUS_RETAILED_STALE,
    attestation_fingerprint,
    pdi_sha256,
    status_for,
    validate_reference_url,
)


class TestReferenceUrl:
    @pytest.mark.parametrize("url", [
        "https://chat.whatsapp.com/abc123",
        "https://drive.google.com/file/d/xyz/view?usp=sharing",
        "https://teams.microsoft.com/l/message/19:abc@thread.v2/1700000000000",
        "  https://example.com/invoice/50185  ",
    ])
    def test_any_https_host_is_accepted(self, url):
        assert validate_reference_url(url) == url.strip()

    @pytest.mark.parametrize("given, stored", [
        ("HTTPS://Example.com/Path?Q=1", "https://Example.com/Path?Q=1"),
        ("Https://example.com/a", "https://example.com/a"),
        ("hTtPs://example.com", "https://example.com"),
    ])
    def test_the_scheme_is_stored_in_lower_case_matching_the_database_check(self, given, stored):
        assert validate_reference_url(given) == stored
        assert stored.startswith("https://")             # what ck_invoices_reference_url_https requires

    @pytest.mark.parametrize("url, message", [
        ("http://example.com/x", "Only https:// links"),
        ("javascript:alert(1)", "Only https:// links"),
        ("data:text/html,<script>alert(1)</script>", "Only https:// links"),
        ("ftp://example.com/x", "Only https:// links"),
        ("//example.com/x", "Only https:// links"),
        ("example.com/x", "Only https:// links"),
        ("https:///path-only", "has no host"),
        ("https://user:secret@example.com/x", "username or password"),
        ("https://example.com/a b", "spaces or control characters"),
        ("https://example.com/\x00", "spaces or control characters"),
        ("https://example.com/" + "a" * MAX_REFERENCE_URL, "longer than"),
    ])
    def test_unsafe_or_malformed_links_are_refused(self, url, message):
        with pytest.raises(ValidationError, match=message):
            validate_reference_url(url)

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_clears_the_link(self, value):
        assert validate_reference_url(value) is None


def _invoice(status="VALIDATED", attested=None, **header):
    base = {"invoice_number": "50185", "invoice_date": date(2026, 9, 1), "currency": "USD",
            "store_id": uuid.UUID(int=7), "subtotal": Decimal("60.00"), "tax_amount": Decimal("0"),
            "discount_amount": Decimal("0"), "deposit_total": None, "fuel_surcharge": None,
            "grand_total": Decimal("60.00")}
    base.update(header)
    items = [SimpleNamespace(sort_order=0, line_type="product", product_sku="077777333333", description="BUD LIGHT",
                             pack_size="24/12OZ", quantity=Decimal("2"), unit_price=Decimal("30.0000"),
                             line_total=Decimal("60.00"), discount=None, deposit=None, tax_rate=None)]
    return SimpleNamespace(status=status, retailed_at=datetime.now(UTC) if attested else None,
                           retailed_attestation_sha256=attested, reference_url=None, items=items, **base)


UNITS = {"07777733333": 24}


class TestRetailedStatus:
    def test_not_marked(self):
        assert status_for(_invoice(), "abc") == STATUS_NOT_RETAILED

    def test_current_only_while_validated_and_unchanged(self):
        assert status_for(_invoice(attested="abc"), "abc") == STATUS_RETAILED
        assert status_for(_invoice(attested="abc"), "def") == STATUS_RETAILED_STALE
        assert status_for(_invoice(attested="abc"), None) == STATUS_RETAILED_STALE
        assert status_for(_invoice(status="REVIEW_REQUIRED", attested="abc"), "abc") == STATUS_RETAILED_STALE

    def test_the_pdi_hash_is_the_ledgers(self):
        content = "H00000001...\r\nB0123...\r\n"
        assert pdi_sha256(content) == hashlib.sha256(content.encode("utf-8")).hexdigest()


class TestAttestationFingerprint:
    BASE = None

    def fp(self, invoice=None, units=None, pdi="pdi-1"):
        return attestation_fingerprint(invoice or _invoice(), UNITS if units is None else units, pdi)

    def test_deterministic_and_insensitive_to_decimal_scale_and_line_order(self):
        a, b = _invoice(), _invoice(subtotal=Decimal("60"), grand_total=Decimal("60.0000"))
        b.items[0].unit_price, b.items[0].quantity = Decimal("30"), Decimal("2.0000")
        assert self.fp(a) == self.fp(b) == self.fp(_invoice())
        two = _invoice()
        extra = SimpleNamespace(**{**vars(two.items[0]), "sort_order": 1, "product_sku": "07777744444"})
        two.items = [extra, two.items[0]]
        swapped = _invoice()
        swapped.items = [swapped.items[0], SimpleNamespace(**vars(extra))]
        assert self.fp(two) == self.fp(swapped)

    @pytest.mark.parametrize("field, value", [
        ("product_sku", "077777999999"), ("description", "BUD LIGHT CAN"), ("pack_size", "12/12OZ"),
        ("quantity", Decimal("3")), ("unit_price", Decimal("29.99")), ("line_total", Decimal("59.98")),
        ("discount", Decimal("0.50")), ("deposit", Decimal("0.10")), ("tax_rate", Decimal("0.07")),
        ("line_type", "voided"),
    ])
    def test_any_attested_line_value_changes_it(self, field, value):
        changed = _invoice()
        setattr(changed.items[0], field, value)
        assert self.fp(changed) != self.fp()

    @pytest.mark.parametrize("field, value", [
        ("subtotal", Decimal("61.00")), ("tax_amount", Decimal("1.00")), ("discount_amount", Decimal("2.00")),
        ("deposit_total", Decimal("1.20")), ("fuel_surcharge", Decimal("3.00")), ("grand_total", Decimal("62.00")),
        ("invoice_number", "50186"), ("invoice_date", date(2026, 9, 2)), ("store_id", uuid.UUID(int=8)),
    ])
    def test_any_attested_header_value_changes_it(self, field, value):
        assert self.fp(_invoice(**{field: value})) != self.fp()

    def test_an_added_line_a_mapping_change_and_the_pdi_file_change_it(self):
        added = _invoice()
        added.items.append(SimpleNamespace(**{**vars(added.items[0]), "sort_order": 1}))
        assert self.fp(added) != self.fp()
        assert self.fp(units={"07777733333": 12}) != self.fp()
        assert self.fp(pdi="pdi-2") != self.fp() and self.fp(pdi=None) != self.fp()

    def test_values_outside_the_attestation_do_not_change_it(self):
        other = _invoice(status="REVIEW_REQUIRED", attested="x")
        other.reference_url = "https://chat.example.com/m/1"
        assert self.fp(other) == self.fp()          # status is checked separately by status_for
