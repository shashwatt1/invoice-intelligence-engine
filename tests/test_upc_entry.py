"""
tests/test_upc_entry.py — entering a missing UPC (no DB).

A Frito-Lay row prints a short UPC fragment and the supplier's own 8-digit
ITEM number, so extraction leaves its UPC empty and the export gate blocks it.
A manager now enters the UPC through the existing audited line correction.
These tests pin the pieces that make that safe:

* the supplier ITEM number is recovered only as display context, and only
  when the row matches the extraction with certainty;
* a typed UPC must be digits (spaces and dashes allowed), never letters;
* "missing" is exactly the export gate's own test, so entering a value does
  not by itself bypass any other gate.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.v1 import invoices as invoices_module
from app.api.v1.invoices import _UPC_CHARACTERS, correct_line_item
from app.core.exceptions import RecordNotFoundError, ValidationError
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.schemas.processing import LineItemCorrection, LineItemData
from app.services.export_service import (
    items_with_unresolved_identity,
    normalize_item_code,
    pdi_export_eligibility,
)
from app.services.supplier_item_context import digits, supplier_item_ids


def _completion(line_items: list[dict]) -> dict:
    """The stored provider response: the extraction is JSON text inside the first choice."""
    return {"id": "chatcmpl-x", "choices": [{"message": {"content": json.dumps({"line_items": line_items})}}]}


def _row(sort_order, description, *, sku=None, entry_source="extracted", history=None):
    return SimpleNamespace(sort_order=sort_order, description=description, product_sku=sku,
                           entry_source=entry_source, correction_history=history or [])


FRITO = [
    {"description": "2.09 GM VAN MINI CRE", "product_code": None, "supplier_item_id": "00025328"},
    {"description": "2.09 GM VANILLA CREM", "product_code": None, "supplier_item_id": "00025294"},
    {"description": "2.09 GM PNB CREMES", "product_code": None, "supplier_item_id": None},
]


class TestSupplierItemContext:
    def test_items_are_matched_by_position_and_description(self):
        invoice = SimpleNamespace(raw_extraction_json=_completion(FRITO), items=[
            _row(0, "2.09 GM VAN MINI CRE"), _row(1, "2.09 GM VANILLA CREM"), _row(2, "2.09 GM PNB CREMES")])
        assert supplier_item_ids(invoice) == {0: "00025328", 1: "00025294"}

    def test_a_description_mismatch_shows_nothing_rather_than_a_wrong_number(self):
        invoice = SimpleNamespace(raw_extraction_json=_completion(FRITO), items=[
            _row(0, "SOMETHING ELSE"), _row(1, "2.09 GM VANILLA CREM")])
        assert supplier_item_ids(invoice) == {1: "00025294"}

    def test_a_corrected_description_is_matched_on_its_original_wording(self):
        history = [{"field": "description", "old": "2.09 GM VAN MINI CRE", "new": "GM VANILLA MINI CREMES"}]
        invoice = SimpleNamespace(raw_extraction_json=_completion(FRITO), items=[
            _row(0, "GM VANILLA MINI CREMES", history=history)])
        assert supplier_item_ids(invoice) == {0: "00025328"}

    def test_manual_rows_and_rows_past_the_extraction_get_nothing(self):
        invoice = SimpleNamespace(raw_extraction_json=_completion(FRITO), items=[
            _row(0, "2.09 GM VAN MINI CRE", entry_source="manual"), _row(7, "2.09 GM VAN MINI CRE")])
        assert supplier_item_ids(invoice) == {}

    @pytest.mark.parametrize("raw", [
        None, {}, {"id": "chatcmpl-fake", "model": "gpt-4o-mini"},       # the test fake's shape
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": json.dumps({"no_items": True})}}]},
        {"choices": []}, "a string",
    ])
    def test_anything_unreadable_fails_closed(self, raw):
        invoice = SimpleNamespace(raw_extraction_json=raw, items=[_row(0, "2.09 GM VAN MINI CRE")])
        assert supplier_item_ids(invoice) == {}

    def test_a_top_level_line_items_shape_is_also_read(self):
        invoice = SimpleNamespace(raw_extraction_json={"line_items": FRITO}, items=[_row(1, "2.09 GM VANILLA CREM")])
        assert supplier_item_ids(invoice) == {1: "00025294"}

    def test_digits_compares_only_digits(self):
        assert digits("0002-5328") == "00025328"
        assert digits(None) == ""

    @pytest.mark.parametrize("value", ["\uff10\uff10\uff10\uff12\uff15\uff13\uff12\uff18",
                                       "\u0660\u0660\u0660\u0662\u0665\u0663\u0662\u0668",
                                       "\u06f0\u06f0\u06f0\u06f2", "\u00b2\u00b3", "\U0001d7ce\U0001d7cf"])
    def test_digits_keeps_ascii_digits_only(self, value):
        # Fullwidth, Arabic-Indic, extended Arabic-Indic, superscript and mathematical digits are not 0-9.
        assert digits(value) == ""
        assert digits("0002-5328" + value) == "00025328"


class TestTypedUpcCharacters:
    @pytest.mark.parametrize("value", ["012000126300", "0-12000-12630-0", "0 12000 12630 0", "7", "81003051002"])
    def test_digits_with_optional_spaces_or_dashes_are_accepted(self, value):
        assert _UPC_CHARACTERS.fullmatch(value)

    @pytest.mark.parametrize("value", ["RB248904", "ABC", "0-12000-1263-", "-012000", "12.5", "0120 00x", ""])
    def test_letters_and_dangling_separators_are_refused(self, value):
        assert not _UPC_CHARACTERS.fullmatch(value)

    @pytest.mark.parametrize("value", [
        "\uff10\uff11\uff12\uff10\uff10\uff10\uff11\uff12\uff16\uff13\uff10\uff10",   # fullwidth 012000126300
        "\u0660\u0661\u0662\u0660\u0660\u0660\u0661\u0662\u0666\u0663\u0660\u0660",   # Arabic-Indic
        "\u06f0\u06f1\u06f2\u06f0\u06f0\u06f0",                                             # extended Arabic-Indic
        "0-12000-\uff11\uff12\uff16\uff13\uff10-0",                                         # ASCII mixed with fullwidth
        "\U0001d7ce\U0001d7cf\U0001d7d0",                                                      # mathematical digits
    ])
    def test_unicode_digits_are_refused(self, value):
        assert not _UPC_CHARACTERS.fullmatch(value)


class _NoInvoiceRepository:
    """Stands in for InvoiceRepository: reaching it means the input checks passed."""

    def __init__(self, _db):
        pass

    async def get(self, _invoice_id):
        return None


MANAGER = SimpleNamespace(username="testmanager", role="MANAGER")


def _record_lock(monkeypatch) -> list:
    """Stub the posted-receipt lock (it needs a session) and record each call."""
    calls: list = []

    async def lock(db, invoice_id, action):
        calls.append(action)

    monkeypatch.setattr(invoices_module, "ensure_invoice_changeable", lock)
    return calls


class TestTheCorrectionEndpointRefusesNonAsciiDigits:
    """The real PATCH handler: its UPC checks run before any database access."""

    @pytest.mark.parametrize("code", [
        "\uff10\uff11\uff12\uff10\uff10\uff10\uff11\uff12\uff16\uff13\uff10\uff10",
        "\u0660\u0661\u0662\u0660\u0660\u0660\u0661\u0662\u0666\u0663\u0660\u0660",
        "0-12000-\uff11\uff12\uff16\uff13\uff10-0",
    ])
    async def test_fullwidth_and_arabic_indic_upcs_are_refused_with_a_422(self, code, monkeypatch):
        monkeypatch.setattr(invoices_module, "InvoiceRepository", _NoInvoiceRepository)
        locks = _record_lock(monkeypatch)
        with pytest.raises(ValidationError) as refused:
            await correct_line_item(uuid.uuid4(), 0, LineItemCorrection(product_code=code), db=None, user=MANAGER)
        assert "digits only" in refused.value.message
        assert refused.value.detail["field"] == "product_code"
        assert locks == []                    # refused on input, before the receipt lock is taken

    @pytest.mark.parametrize("code", ["012000126300", "0-12000-12630-0", "0 12000 12630 0"])
    async def test_ascii_upcs_pass_the_checks_as_before(self, code, monkeypatch):
        monkeypatch.setattr(invoices_module, "InvoiceRepository", _NoInvoiceRepository)
        locks = _record_lock(monkeypatch)
        # Past the UPC checks the handler takes the receipt lock, then looks the invoice up;
        # the stand-in has none.
        with pytest.raises(RecordNotFoundError):
            await correct_line_item(uuid.uuid4(), 0, LineItemCorrection(product_code=code), db=None, user=MANAGER)
        assert locks == ["correct_line_item"]

    async def test_clearing_the_code_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(invoices_module, "InvoiceRepository", _NoInvoiceRepository)
        locks = _record_lock(monkeypatch)
        with pytest.raises(RecordNotFoundError):
            await correct_line_item(uuid.uuid4(), 0, LineItemCorrection(product_code=""), db=None, user=MANAGER)
        assert locks == ["correct_line_item"]


def _invoice(skus: list[str | None]) -> Invoice:
    inv = Invoice(id=uuid.uuid4(), document_id=uuid.uuid4(), invoice_number="FL-1", currency="USD",
                  subtotal=Decimal("10.00") * len(skus), grand_total=Decimal("10.00") * len(skus), status="VALIDATED")
    inv.items = [InvoiceItem(invoice_id=inv.id, description=f"ITEM {n}", product_sku=s, quantity=Decimal("1"),
                             unit_price=Decimal("10.00"), line_total=Decimal("10.00"), line_type="product", sort_order=n)
                 for n, s in enumerate(skus)]
    return inv


class TestMissingMeansTheExportGatesOwnTest:
    def test_missing_rows_block_export_and_a_typed_upc_clears_only_that_gate(self):
        inv = _invoice([None, "012000001130"])
        assert items_with_unresolved_identity(inv) == ["ITEM 0"]
        assert "no resolved UPC" in pdi_export_eligibility(inv, {"01200000113": 1}).blocked_reason

        inv.items[0].product_sku = "0-28400-26092-1"
        # Identity is resolved now; the mapping gate still applies to the new code.
        eligibility = pdi_export_eligibility(inv, {"01200000113": 1})
        assert eligibility.allowed is False
        assert "units-per-case" in eligibility.blocked_reason or "mapping" in eligibility.blocked_reason

    def test_a_supplier_item_number_typed_as_a_upc_would_resolve_so_the_api_refuses_it(self):
        # Why the endpoint compares with the ITEM: on its own it normalises to a plausible code.
        assert normalize_item_code("00025328") == "00025328"

    def test_the_new_fields_default_to_no_claim(self):
        data = LineItemData(description="X", quantity=1.0)
        assert data.product_code_missing is False
        assert data.supplier_item_id is None
