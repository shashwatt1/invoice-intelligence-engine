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

    def test_the_active_prompt_keeps_every_earlier_instruction(self):
        assert ACTIVE_VERSION == "v11"
        v3, v4, v5, v6 = (get_prompt(v).system_prompt for v in ("v3", "v4", "v5", "v6"))
        active = get_prompt().system_prompt
        for kept in ("QUANTITY IS NOT PACK SIZE", "WHOLESALE COST IS NOT RETAIL PRICE",
                     "quantity x unit_price ~= line_total", "VENDOR vs CUSTOMER", "Prefer null",
                     "D.PRICE", "U.PRICE", "Step 1b"):
            assert kept in v3 and kept in v4 and kept in v5 and kept in v6, kept
            assert kept in active, kept
        assert "NET                     -> AMBIGUOUS" in v6      # v4's correction survives
        assert "## Step 1c" in v6                             # v5's row anchoring survives
        assert get_prompt("v3").system_prompt == v3          # earlier versions are untouched
        assert get_prompt("v4").system_prompt == v4
        assert get_prompt("v5").system_prompt == v5

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


class TestPromptV6MultiPhoto:
    """
    v6 exists because one invoice may arrive as several overlapping
    photographs. The model gets each photo's OCR text under its own
    header and must return each physical row once — never merging when
    it cannot tell an overlap from a legitimately repeated row.
    """

    def test_overlap_is_reconciled_by_context_not_by_upc_alone(self):
        system = get_prompt("v6").system_prompt
        assert "## Step 1d" in system
        assert "--- PHOTO k of N ---" in system
        assert "emit them once, with source_pages listing both photos" in system
        assert "The same UPC on two rows is NOT by itself an overlap" in system

    def test_uncertainty_is_flagged_never_merged(self):
        from app.schemas.extraction import ExtractedLineItem

        system = get_prompt("v6").system_prompt
        assert "DO NOT merge them" in system
        assert "possible_duplicate_of" in system
        fields = ExtractedLineItem.model_fields
        assert fields["source_pages"].default_factory is not None
        assert fields["possible_duplicate_of"].default is None
        assert "Never merge when unsure" in fields["possible_duplicate_of"].description

    def test_single_file_text_is_unchanged_from_v5(self):
        from app.prompts.invoice_extraction import _STEP_1D

        v5 = get_prompt("v5").system_prompt
        v6 = get_prompt("v6").system_prompt
        assert v6.replace(_STEP_1D, "## Step 2 — Extract each line\n") == v5


class TestPromptV7TransactionTotalIsNotBalanceDue:
    """
    Red Bull 2035546957 (RCM, 17 Sep 2026): the totals block printed
    "INVOICE $329.53" and, after a payment received on account, "TOTAL DUE:
    $0.00". v6 only said "grand total must be the printed value" and the
    schema called grand_total "final amount payable", so the model reported
    0.00 — and even flagged the conflict in `concerns` while doing it. The
    EDI header would have carried $0.00 for $329.53 of goods.

    v7 names the distinction: grand_total is the invoice TRANSACTION total;
    a due/balance figure is amount_due. No vendor or layout is named.
    """

    def test_v7_states_the_distinction_and_where_each_figure_goes(self):
        system = get_prompt("v7").system_prompt
        assert "INVOICE TRANSACTION TOTAL" in system
        assert "NOT the balance still owed" in system
        assert "`amount_due`" in system
        assert "never the due figure" in system
        # A document whose only total is a due figure still yields a grand total.
        assert "Only when the sole printed total is a due/balance figure" in system
        for vendor_specific in ("Red Bull", "RCM", "Texaco"):
            assert vendor_specific not in system

    def test_v7_reads_exactly_as_v6_apart_from_rule_nine(self):
        from app.prompts.invoice_extraction import _RULE_9_V6, _RULE_9_V7

        v6 = get_prompt("v6").system_prompt
        v7 = get_prompt("v7").system_prompt
        assert v7.replace(_RULE_9_V7, _RULE_9_V6) == v6

    def test_schema_gives_the_due_figure_its_own_field(self):
        from app.schemas.extraction import ExtractedInvoice

        fields = ExtractedInvoice.model_fields
        assert fields["amount_due"].default is None
        assert "Never copy this into grand_total" in fields["amount_due"].description
        assert "NOT the balance still owed" in fields["grand_total"].description
        # Stored v6 extractions (no amount_due key) still parse.
        parsed = ExtractedInvoice.model_validate(
            {"vendor": {"name": "V"}, "line_items": [], "grand_total": 329.53}
        )
        assert parsed.amount_due is None and parsed.grand_total == 329.53


class TestPromptV8ExtendedAmountIsNotTheTaxInclusiveTotal:
    """
    UniFirst 2310090549 (RCM, 18 Sep 2026) prints a service-invoice table
    headed QTY | ITEM | DESCRIPTION | RATE | AMOUNT | TAX | TOTAL, where
    AMOUNT is the pre-tax extended amount and TOTAL is AMOUNT + TAX:

        12  2PLY MINI TWIN TT  7.1600   85.92   6.09   92.01

    v7's column table listed "EXT, AMOUNT, TOTAL -> extended line total"
    as synonyms, so on a layout that prints BOTH the model chose TOTAL —
    the tax-inclusive one. 12 x 7.16 = 85.92 then contradicts a line_total
    of 92.01 on every taxed row, and the goods never sum to the printed
    subtotal.

    The canonical model is quantity x unit_price = line_total with tax
    carried at the header, so line_total is the PRE-TAX extended amount.
    No vendor or layout is named in the rule.
    """

    def test_v8_separates_the_pre_tax_amount_from_a_tax_inclusive_total(self):
        system = get_prompt("v8").system_prompt
        assert "EXT, AMOUNT, NET AMOUNT  -> extended line total, BEFORE tax" in system
        assert "TOTAL, LINE TOTAL       -> extended total; when the row ALSO prints" in system
        assert "line_total is the PRE-TAX extended amount" in system
        for vendor_specific in ("UniFirst", "UNIFIRST", "RCM", "DEFE"):
            assert vendor_specific not in system

    def test_v8_keeps_the_arithmetic_identity_that_proves_the_choice(self):
        system = get_prompt("v8").system_prompt
        assert "quantity x unit_price ~= line_total" in system
        # and says which column to switch to when the identity fails by the tax
        assert "differs from quantity x unit_price by the row's TAX" in system

    def test_v8_reads_as_v7_apart_from_the_amount_rule(self):
        from app.prompts.invoice_extraction import _V8_EDITS

        v7, v8 = get_prompt("v7").system_prompt, get_prompt("v8").system_prompt
        restored = v8
        for old, new in _V8_EDITS:
            restored = restored.replace(new, old, 1)
        assert restored == v7

    def test_the_schema_gives_the_row_tax_its_own_field(self):
        from app.schemas.extraction import ExtractedLineItem

        fields = ExtractedLineItem.model_fields
        assert fields["line_tax"].default is None
        assert "NOT part of line_total" in fields["line_tax"].description
        assert "BEFORE tax" in fields["line_total"].description
        # A stored v7 row (no line_tax key) still parses.
        row = ExtractedLineItem.model_validate(
            {"description": "X", "quantity": 12, "unit_price": 7.16, "line_total": 85.92}
        )
        assert row.line_tax is None and row.line_total == 85.92


class TestPromptV9QuantityAttribution:
    """
    UniFirst 2310090549 again, on the QTY column.

    Vision reads the table as a columnar stream in which the quantity
    precedes the ITEM code, not the description. Two artifacts follow:
    five adjacent single-digit quantities merge into one token, and the
    preceding row's printed TOTAL of 2.02 arrives as "202" — sitting
    exactly where this layout prints a quantity, so it was taken as the
    next row's count.

    v9 states the attribution rule structurally: a quantity belongs to the
    row whose column position it occupies, a trailing money value of one
    row is never the next row's quantity, and an unattributable quantity
    on a CHARGE row is null rather than a borrowed number. It does not
    tell the model to split merged tokens arithmetically — that is the
    deterministic layer's job (Rule E), on proof.
    """

    def test_v9_states_the_row_attribution_rule_without_naming_a_vendor(self):
        system = get_prompt("v9").system_prompt
        assert "the quantity column may print its value BEFORE the item code" in system
        assert "never the trailing money value of the row above it" in system
        assert "may lose its decimal point" in system
        # The rule teaches the phenomenon; it never carries this invoice's figures.
        for vendor_specific in ("UniFirst", "UNIFIRST", "RCM", "DEFE", "24132", "2.02", "0.7106"):
            assert vendor_specific not in system

    def test_v9_gives_an_unquantified_charge_row_a_null_quantity(self):
        system = get_prompt("v9").system_prompt
        assert "A charge row that prints no quantity has quantity null" in system

    def test_v9_does_not_ask_the_model_to_split_merged_tokens_by_arithmetic(self):
        system = get_prompt("v9").system_prompt
        for forbidden in ("split the token", "divide the token", "split it into"):
            assert forbidden not in system

    def test_v9_keeps_genuine_zero_quantities_and_the_prefer_null_principle(self):
        system = get_prompt("v9").system_prompt
        assert "A row printed with 0 is quantity 0: keep it" in system
        assert "Prefer null plus a `concerns` entry over a confident guess" in system

    def test_v9_reads_as_v8_apart_from_the_attribution_rule(self):
        from app.prompts.invoice_extraction import _V9_EDITS

        v8, v9 = get_prompt("v8").system_prompt, get_prompt("v9").system_prompt
        # v8 still carries its own rule, untouched by the v9 derivation.
        assert "EXT, AMOUNT, NET AMOUNT  -> extended line total, BEFORE tax" in v8
        assert "line_total is the PRE-TAX extended amount" in v8
        restored = v9
        for old, new in _V9_EDITS:
            restored = restored.replace(new, old, 1)
        assert restored == v8

    def test_the_quantity_field_description_carries_the_attribution_rule(self):
        from app.schemas.extraction import ExtractedLineItem

        description = ExtractedLineItem.model_fields["quantity"].description
        assert "column position" in description
        assert "never a value that belongs to the row above" in description


class TestPromptV10ChargeRowsDoNotBorrowNumbers:
    """
    v9 stated the attribution rule but a fresh run still gave a fixed
    charge row the number sitting immediately before its item code — the
    previous row's printed total, whose decimal point OCR had dropped, so
    it no longer looked like money at all.

    v9 left that to prose ("the number of trailing figures each row
    carries tells you which it is"). v10 makes it a procedure the model
    can execute: count the price columns in the header, and before
    treating a token as a quantity check whether the row above already
    has that many figures. If it does, the token completes that row. And
    a charge row's quantity is null unless the QTY column prints one on
    that row — a fixed charge counts nothing.
    """

    def test_v10_makes_the_trailing_figure_check_a_procedure(self):
        system = get_prompt("v10").system_prompt
        assert "count how many figures each complete row carries" in system
        assert "already has its full set of figures" in system
        assert "completes the row above" in system

    def test_v10_makes_a_charge_rows_quantity_null_by_default(self):
        system = get_prompt("v10").system_prompt
        assert "a charge row's quantity is null unless the QTY column prints one on that row" in system
        assert "never a token taken from a neighbouring row" in system

    def test_v10_still_keeps_genuine_zero_quantities_and_invents_nothing(self):
        system = get_prompt("v10").system_prompt
        assert "A row printed with 0 is quantity 0: keep it" in system
        assert "Prefer null plus a `concerns` entry over a confident guess" in system
        for forbidden in ("split the token", "divide the token", "split it into"):
            assert forbidden not in system

    def test_v10_charge_rules_remain_intact_registered_unchanged(self):
        from app.prompts.invoice_extraction import _REGISTRY, _V10_EDITS

        assert {"v8", "v9", "v10"} <= set(_REGISTRY)
        v8, v9, v10 = (get_prompt(v).system_prompt for v in ("v8", "v9", "v10"))
        # v8 keeps its own rule and neither of the later ones.
        assert "EXT, AMOUNT, NET AMOUNT  -> extended line total, BEFORE tax" in v8
        assert "3a. ATTRIBUTE EACH QUANTITY TO ITS OWN ROW" not in v8
        # v9 keeps its rule and not v10's.
        assert "3a. ATTRIBUTE EACH QUANTITY TO ITS OWN ROW" in v9
        assert "count how many figures each complete row carries" not in v9
        restored = v10
        for old, new in _V10_EDITS:
            restored = restored.replace(new, old, 1)
        assert restored == v9

    def test_v11_is_active_and_v9_and_v10_remain_registered_and_unchanged(self):
        from app.prompts.invoice_extraction import _REGISTRY, _V11_EDITS

        assert ACTIVE_VERSION == "v11"
        assert {"v9", "v10", "v11"} <= set(_REGISTRY)
        v9, v10, v11 = (get_prompt(v).system_prompt for v in ("v9", "v10", "v11"))
        # v10 keeps its own rule and not v11's.
        assert "count how many figures each complete row carries" in v10
        assert "product_code is the retail barcode/UPC ONLY" not in v10
        # v9's charge-quantity rule is untouched all the way through v11.
        assert "3a. ATTRIBUTE EACH QUANTITY TO ITS OWN ROW" in v9
        assert "3a. ATTRIBUTE EACH QUANTITY TO ITS OWN ROW" in v11
        restored = v11
        for old, new in _V11_EDITS:
            restored = restored.replace(new, old, 1)
        assert restored == v10

    def test_v10_names_no_vendor_and_carries_no_invoice_figures(self):
        system = get_prompt("v10").system_prompt
        for specific in ("UniFirst", "UNIFIRST", "RCM", "DEFE", "Energy Surcharge",
                         "24132", "2.02", "0.7106", "2310090549"):
            assert specific not in system


class TestPromptV11SupplierItemIdVsUpc:
    """
    A real photographed invoice printed both a vendor item code in the ID
    column (e.g. "RB248904") and, separately, the actual retail barcode
    on its own line below the description (e.g. "611269002461"). v10's
    rule 10 only said "prefer the UPC when both are shown" without saying
    how to recognize which token IS the UPC, so the model kept the
    ID-column token — the only one it reliably associated with the row —
    and the generated EDI carried a zero-padded vendor code that cannot
    match an existing product downstream.

    v11 names two things a naive reading misses: (1) an ID-column vendor
    code and a barcode are different identifiers, never one standing in
    for the other; (2) a barcode printed on its own line below the
    description can appear, in raw OCR text, closer to the FOLLOWING
    row's leading numbers than to its own row — so pairing must go by
    document order, not text proximity. No vendor or invoice is named.
    """

    def test_v11_states_the_identifier_distinction(self):
        system = get_prompt("v11").system_prompt
        assert "product_code is the retail barcode/UPC ONLY" in system
        assert "is NEVER product_code" in system
        assert "supplier_item_id instead" in system

    def test_v11_states_the_document_order_pairing_rule(self):
        system = get_prompt("v11").system_prompt
        assert "closer to the FOLLOWING row's leading numbers" in system
        assert "the k-th barcode-shaped token belongs to the k-th product row" in system
        assert "never by which row's text block it happens to fall nearest to" in system

    def test_v11_never_tells_the_model_to_substitute_the_vendor_code(self):
        system = get_prompt("v11").system_prompt
        assert "never substitute the vendor item number" in system

    def test_v11_keeps_earlier_instructions_and_invents_nothing(self):
        system = get_prompt("v11").system_prompt
        assert "Prefer null plus a `concerns` entry over a confident guess" in system
        assert "A row printed with 0 is quantity 0: keep it" in system
        for forbidden in ("split the token", "divide the token", "split it into"):
            assert forbidden not in system

    def test_v11_names_no_vendor_and_carries_no_invoice_figures(self):
        system = get_prompt("v11").system_prompt
        for specific in ("Red Bull", "RED BULL", "RB248904", "RB2860", "RB221027",
                         "611269002461", "611269109009", "2035546957", "Red Cliff Texaco"):
            assert specific not in system

    def test_v11_is_active_and_v9_and_v10_remain_registered_and_unchanged(self):
        from app.prompts.invoice_extraction import _REGISTRY, _V11_EDITS

        assert ACTIVE_VERSION == "v11"
        assert {"v9", "v10", "v11"} <= set(_REGISTRY)
        v9, v10, v11 = (get_prompt(v).system_prompt for v in ("v9", "v10", "v11"))
        # v10 keeps its own rule and not v11's.
        assert "count how many figures each complete row carries" in v10
        assert "product_code is the retail barcode/UPC ONLY" not in v10
        # v9's charge-quantity rule is untouched all the way through v11.
        assert "3a. ATTRIBUTE EACH QUANTITY TO ITS OWN ROW" in v9
        assert "3a. ATTRIBUTE EACH QUANTITY TO ITS OWN ROW" in v11
        restored = v11
        for old, new in _V11_EDITS:
            restored = restored.replace(new, old, 1)
        assert restored == v10


class TestExtractedLineItemSeparatesSupplierIdFromUpc:
    """
    Schema-level pin: the two identifier roles are distinct FIELDS, not
    one field carrying two meanings. Field descriptions are what the
    model actually sees (sent as JSON schema) — the system prompt is
    reinforcement, not the primary channel.
    """

    def test_both_fields_exist_and_are_independently_settable(self):
        from app.schemas.extraction import ExtractedLineItem

        item = ExtractedLineItem(
            description="SF ICED 8.40Z", product_code="611269002461",
            supplier_item_id="RB248904", quantity=1, unit_price=37.99, line_total=37.99,
        )
        assert item.product_code == "611269002461"
        assert item.supplier_item_id == "RB248904"

    def test_product_code_description_forbids_vendor_codes(self):
        from app.schemas.extraction import ExtractedLineItem

        description = ExtractedLineItem.model_fields["product_code"].description
        assert "NEVER a vendor's own internal item/SKU/ID-column code" in description
        assert "never substitute the vendor item number" in description

    def test_supplier_item_id_description_names_the_distinction(self):
        from app.schemas.extraction import ExtractedLineItem

        description = ExtractedLineItem.model_fields["supplier_item_id"].description
        assert "distinct from product_code" in description

    def test_both_are_optional_and_default_to_null(self):
        from app.schemas.extraction import ExtractedLineItem

        item = ExtractedLineItem(description="X")
        assert item.product_code is None and item.supplier_item_id is None
