"""
tests/test_structuring_service.py — Structuring orchestration, prompt
registry, and LLM factory tests. No network access.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.prompts.invoice_extraction import ACTIVE_VERSION, get_prompt
from app.schemas.extraction import ExtractedInvoice, ExtractedVendor
from app.services.llm.base import LLMCallMetadata, LLMProvider, LLMStructuredResponse
from app.services.llm.factory import get_llm_provider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.ocr.base import OCRResult
from app.services.structuring_service import StructuringService


class FakeLLMProvider(LLMProvider):
    """Records prompts and returns a canned structured response."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_structured(self, *, system_prompt, user_prompt, schema):
        self.calls.append({"system": system_prompt, "user": user_prompt, "schema": schema})
        return LLMStructuredResponse(
            parsed=ExtractedInvoice(
                vendor=ExtractedVendor(name="Acme Corp"),
                invoice_number="INV-42",
                line_items=[],
            ),
            raw_response={"id": "chatcmpl-fake"},
            metadata=LLMCallMetadata(provider="fake", model="fake-model", total_tokens=10),
        )


def ocr_result(text: str = "INVOICE INV-42 from Acme Corp") -> OCRResult:
    return OCRResult(full_text=text, source_type="digital_pdf", page_count=1)


class TestStructuringService:
    async def test_composes_result_with_prompt_version_and_metadata(self):
        fake = FakeLLMProvider()
        service = StructuringService(llm_provider=fake)

        result = await service.structure_invoice(ocr_result(), "inv.pdf")

        assert result.invoice.invoice_number == "INV-42"
        assert result.invoice.vendor.name == "Acme Corp"
        assert result.prompt_version == ACTIVE_VERSION
        assert result.metadata.model == "fake-model"
        assert result.raw_response == {"id": "chatcmpl-fake"}
        assert result.ocr_text_truncated is False

    async def test_sends_versioned_prompts_and_schema(self):
        fake = FakeLLMProvider()
        service = StructuringService(llm_provider=fake)

        await service.structure_invoice(ocr_result("Total due: 118.00"), "inv.pdf")

        [record] = fake.calls
        assert record["system"] == get_prompt().system_prompt
        assert "Total due: 118.00" in record["user"]
        assert "digital_pdf" in record["user"]  # source type is surfaced to the model
        assert record["schema"] is ExtractedInvoice

    async def test_truncates_text_over_token_budget(self):
        fake = FakeLLMProvider()
        service = StructuringService(llm_provider=fake)
        budget_chars = get_settings().openai_max_tokens_per_document * 4
        long_text = "x" * (budget_chars * 2)

        result = await service.structure_invoice(ocr_result(long_text), "big.pdf")

        assert result.ocr_text_truncated is True
        [record] = fake.calls
        assert "truncated" in record["user"]
        # The document portion respects the budget (prompt adds small framing)
        assert len(record["user"]) < budget_chars + 500

    async def test_short_text_is_never_truncated(self):
        fake = FakeLLMProvider()
        service = StructuringService(llm_provider=fake)

        result = await service.structure_invoice(ocr_result("short"), "s.pdf")

        assert result.ocr_text_truncated is False
        assert "truncated" not in fake.calls[0]["user"]


class TestPromptRegistry:
    def test_default_is_active_version(self):
        assert get_prompt().version == ACTIVE_VERSION

    def test_explicit_version_lookup(self):
        assert get_prompt("v1").version == "v1"

    def test_unknown_version_raises(self):
        with pytest.raises(KeyError, match="v999"):
            get_prompt("v999")

    def test_system_prompt_encodes_core_rules(self):
        system = get_prompt("v1").system_prompt
        for rule in ("null", "ISO-8601", "ISO 4217", "Never invent"):
            assert rule in system

    def test_user_prompt_wraps_document(self):
        rendered = get_prompt("v1").render_user_prompt("SOME TEXT", "ocr")
        assert "<document>\nSOME TEXT\n</document>" in rendered
        assert "ocr" in rendered


class TestPromptV4DepositExclusion:
    """
    v4 exists to fix the second observed production failure: on a real
    4-line invoice headed PRICE / DISC / DEP / NET / EXT, where NET is
    PRICE + DEP, the model read every unit_price from NET because v3's
    column table called NET the unit cost. PDI accepted the file with
    deposit-inclusive case costs. Rule D in reconciliation is the
    deterministic guard; these pin the instruction that removes the cause.
    """

    def test_net_is_taught_as_ambiguous_with_the_row_arithmetic(self):
        system = get_prompt("v4").system_prompt
        assert "NET                     -> AMBIGUOUS" in system
        assert "14.50 + 0.60 = 15.10" in system                  # the worked test on the row
        assert "unit_price` NEVER includes a container deposit" in system

    def test_the_schema_says_the_same_thing(self):
        from app.schemas.extraction import ColumnMapping, ExtractedLineItem

        assert "EXCLUDING any container deposit" in ExtractedLineItem.model_fields["unit_price"].description
        assert "if NET equals PRICE + DEP on the rows, choose PRICE" in ColumnMapping.model_fields["unit_cost_column"].description

    def test_v3_no_longer_says_net_is_the_cost_in_v4(self):
        assert "D.PRICE, NET, COST      -> NET unit cost" in get_prompt("v3").system_prompt
        assert "D.PRICE, NET, COST      -> NET unit cost" not in get_prompt("v4").system_prompt


class TestPromptV5RowAnchoring:
    """
    v5 exists because a real photographed invoice (T.J. Sheehan 101497)
    came back from OCR with its quantity+name rows and its UPC/price rows
    in alternating runs. Prices were read perfectly; rows were dropped or
    paired with the wrong name and quantity, a delivery charge became a
    product, and the deposit total was taken from that charge. These pin
    the instructions that address each of those.
    """

    def test_line_items_are_anchored_on_the_upc_rows_and_counted(self):
        system = get_prompt("v5").system_prompt
        assert "## Step 1c" in system
        assert "EVERY row that carries an item code / UPC and prices is ONE line item" in system
        assert "Count these rows first; call it N" in system
        assert "pair the k-th name row with the k-th UPC row" in system
        assert "d) the number of line items equals the number of UPC/price rows" in system

    def test_shorted_rows_keep_quantity_zero(self):
        system = get_prompt("v5").system_prompt
        assert "A row printed with 0 is quantity 0: keep it" in system
        assert "SHORT ON TRUCK" in system

    def test_charges_are_not_products_and_not_deposits(self):
        from app.schemas.extraction import ExtractedInvoice, ExtractedLineItem

        system = get_prompt("v5").system_prompt
        assert "line_type 'charge' with product_code null" in system
        assert "A delivery, fuel or service charge is never a deposit" in system
        assert ExtractedLineItem.model_fields["line_type"].default == "product"
        assert "NEVER a delivery" in ExtractedInvoice.model_fields["deposit_total"].description

    def test_v5_is_v4_plus_exactly_the_three_passages(self):
        from app.prompts.invoice_extraction import _V5_EDITS

        v4, v5 = get_prompt("v4").system_prompt, get_prompt("v5").system_prompt
        rebuilt = v4
        for old, new in _V5_EDITS:
            assert old in rebuilt
            rebuilt = rebuilt.replace(old, new, 1)
        assert rebuilt == v5


class TestPromptV3ColumnDisambiguation:
    """
    v3 exists to fix one observed production failure: on a real 7-line
    invoice the model took unit_price from the gross pre-discount column
    on every row while taking line_total from the net column, overstating
    cost by exactly the invoice's printed total discount (9.3%).

    These tests pin the specific instructions that prevent it, so a future
    prompt edit can't quietly drop them.
    """

    def test_v5_is_active_and_keeps_every_earlier_instruction(self):
        assert ACTIVE_VERSION == "v5"
        v3, v4, v5 = (get_prompt(v).system_prompt for v in ("v3", "v4", "v5"))
        for kept in ("QUANTITY IS NOT PACK SIZE", "WHOLESALE COST IS NOT RETAIL PRICE",
                     "quantity x unit_price ~= line_total", "VENDOR vs CUSTOMER", "Prefer null",
                     "D.PRICE", "U.PRICE", "Step 1b"):
            assert kept in v3 and kept in v4 and kept in v5, kept
        assert "NET                     -> AMBIGUOUS" in v5      # v4's correction survives
        assert get_prompt("v3").system_prompt == v3          # earlier versions are untouched
        assert get_prompt("v4").system_prompt == v4

    def test_teaches_net_vs_gross_price_selection(self):
        system = get_prompt("v3").system_prompt
        assert "NET" in system and "gross" in system.lower()
        assert "D.PRICE" in system  # names the real column that was mis-read
        assert "U.PRICE" in system

    def test_separates_quantity_from_pack_size(self):
        system = get_prompt("v3").system_prompt
        assert "QUANTITY IS NOT PACK SIZE" in system

    def test_distinguishes_wholesale_cost_from_retail(self):
        system = get_prompt("v3").system_prompt
        assert "WHOLESALE COST IS NOT RETAIL PRICE" in system

    def test_requires_arithmetic_self_verification(self):
        system = get_prompt("v3").system_prompt
        assert "quantity x unit_price ~= line_total" in system

    def test_warns_against_vendor_customer_confusion(self):
        # Observed on a receipt-style invoice: the customer's address block
        # was extracted as the vendor.
        system = get_prompt("v3").system_prompt
        assert "VENDOR vs CUSTOMER" in system

    def test_prefers_null_over_guessing(self):
        system = get_prompt("v3").system_prompt
        assert "Prefer null" in system

    def test_vendor_profile_is_injected_when_supplied(self):
        rendered = get_prompt("v3").render_user_prompt(
            "SOME TEXT", "ocr", "Acme Co: net cost is the D.PRICE column."
        )
        assert "<vendor_profile>" in rendered
        assert "net cost is the D.PRICE column" in rendered
        assert "<document>\nSOME TEXT\n</document>" in rendered

    def test_vendor_profile_omitted_when_absent(self):
        rendered = get_prompt("v3").render_user_prompt("SOME TEXT", "ocr")
        assert "<vendor_profile>" not in rendered

    def test_older_versions_ignore_vendor_profile(self):
        # Shipped prompt versions must render identically forever, or
        # stored ProcessingLog entries stop being reproducible.
        for version in ("v1", "v2"):
            template = get_prompt(version)
            assert template.render_user_prompt("T", "ocr", "a profile") == (
                template.render_user_prompt("T", "ocr")
            )

    def test_shipped_versions_are_never_mutated(self):
        # v1/v2 are immutable production assets; v3 must be additive.
        assert "product_code" not in get_prompt("v1").system_prompt
        assert "product_code" in get_prompt("v2").system_prompt
        assert get_prompt("v1").system_prompt != get_prompt("v3").system_prompt


class TestLLMFactory:
    def test_returns_openai_provider_when_configured(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        get_settings.cache_clear()
        try:
            assert isinstance(get_llm_provider(), OpenAIProvider)
        finally:
            get_settings.cache_clear()

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm_provider("claude")
