"""
tests/test_printed_code.py — visibly cut-off printed UPCs (no DB).

PepsiCo 2026-10-22 prints dashed UPCs, and three of them wrap onto a second
print line. The read keeps only the first part: "8-10030-51002-",
"0-12000-1263" and "0-12000-1803". `normalize_item_code` turns each into a
plausible item code, and "8-10030-51002-" even becomes exactly the code an
unverified completion would produce. These tests pin the response:

* a narrow detector that claims only formatting evidence and never judges
  plain digits by length;
* a validation failure that keeps the raw printed value and names the row
  structurally;
* an export block; and
* the existing audited line-item correction as the only way through.

Nothing here completes a code, and `normalize_item_code` is unchanged.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.schemas.extraction import ExtractedInvoice, ExtractedLineItem, ExtractedVendor
from app.schemas.processing import LineItemCorrection
from app.services.export_service import (
    items_with_incomplete_printed_code,
    items_with_unresolved_identity,
    normalize_item_code,
    pdi_export_eligibility,
)
from app.services.printed_code import (
    PARTIAL_UPC_LAYOUT,
    TRAILING_SEPARATOR,
    printed_code_incompleteness,
)
from app.services.revalidation_service import build_report
from app.services.validation.service import ValidationService

PEPSI_CUT_OFF = ["8-10030-51002-", "0-12000-1263", "0-12000-1803"]


class TestDetector:
    @pytest.mark.parametrize(("raw", "reason"), [
        ("8-10030-51002-", TRAILING_SEPARATOR),
        (" 8-10030-51002 - ", TRAILING_SEPARATOR),
        ("8-10030-51002–", TRAILING_SEPARATOR),
        ("12-345-", TRAILING_SEPARATOR),
        ("0-12000-1263", PARTIAL_UPC_LAYOUT),
        ("0-12000-1803", PARTIAL_UPC_LAYOUT),
        ("0-12000-1", PARTIAL_UPC_LAYOUT),
        ("0 - 12000 - 1263", PARTIAL_UPC_LAYOUT),
    ])
    def test_visibly_cut_off_codes_are_claimed(self, raw, reason):
        assert printed_code_incompleteness(raw) == reason

    @pytest.mark.parametrize("raw", [
        # Complete dashed UPC-A, with and without the printed check digit.
        "0-12000-00113-0", "8-10030-51850-1", "0-12000-12630", "0-12000-0011301",
        # Plain digits are never judged by length: full UPCs, short codes, PLUs, UPC-E.
        "012000126300", "81003051002", "0120001263", "1263", "4011", "01234565",
        # Other dashed shapes carry no layout evidence.
        "0-12000", "1-23", "049000-05010", "12-3456-7890", "2026-10-22",
        # Letters (supplier item numbers) and empties are out of scope.
        "RB248904", "ABC-12-", "PEP-0-12000-1263", "", "   ", "-", None,
    ])
    def test_everything_else_is_left_alone(self, raw):
        assert printed_code_incompleteness(raw) is None

    def test_the_shared_normalization_is_unchanged(self):
        # The detector exists precisely because these still normalise to plausible codes.
        assert normalize_item_code("8-10030-51002-") == "81003051002"
        assert normalize_item_code("0-12000-1263") == "0120001263"
        assert normalize_item_code("0-12000-00113-0") == "01200000113"


def _extracted(codes: list[str | None]) -> ExtractedInvoice:
    return ExtractedInvoice(
        vendor=ExtractedVendor(name="PepsiCo Beverages"), invoice_number="PEP-1", invoice_date="2026-10-22",
        subtotal=10.0 * len(codes), grand_total=10.0 * len(codes),
        line_items=[
            ExtractedLineItem(description=f"ITEM {n}", product_code=code, quantity=1, unit_price=10.0, line_total=10.0)
            for n, code in enumerate(codes)
        ],
    )


def _item(sort_order: int, sku: str | None, *, line_type: str = "product", qty: str = "1") -> InvoiceItem:
    return InvoiceItem(
        invoice_id=uuid.uuid4(), description=f"ITEM {sort_order}", product_sku=sku, quantity=Decimal(qty),
        unit_price=Decimal("10.00"), line_total=Decimal(qty) * Decimal("10.00"), line_type=line_type,
        sort_order=sort_order,
    )


def _invoice(items: list[InvoiceItem]) -> Invoice:
    total = sum((i.line_total for i in items if i.line_type == "product"), Decimal("0"))
    inv = Invoice(id=uuid.uuid4(), document_id=uuid.uuid4(), invoice_number="PEP-1", currency="USD",
                  vendor_name="PepsiCo Beverages", subtotal=total, grand_total=total, status="VALIDATED")
    inv.items = items
    return inv


def _units(inv: Invoice) -> dict[str, int]:
    return {c: 1 for c in (normalize_item_code(i.product_sku) for i in inv.items) if c}


class TestValidationCheck:
    def test_pipeline_validation_flags_each_cut_off_row_with_the_raw_value(self):
        result = ValidationService().validate_invoice(_extracted(["0-12000-00113-0", *PEPSI_CUT_OFF]))
        report = result.report
        flagged = [c for c in report.checks if c.name == "PRODUCT_CODE_COMPLETE"]
        assert [(c.status.value, c.field, c.actual) for c in flagged] == [
            ("FAILED", "line_items[1].product_code", "8-10030-51002-"),
            ("FAILED", "line_items[2].product_code", "0-12000-1263"),
            ("FAILED", "line_items[3].product_code", "0-12000-1803"),
        ]
        assert flagged[1].message == (
            'The printed UPC "0-12000-1263" stops partway through a dashed UPC (1-5-5-1) layout. '
            "Enter the full UPC from the invoice before export; it is never completed automatically."
        )
        assert all(not re.search(r"\b[Rr]ows? \d", c.message) for c in flagged)
        assert report.decision.value == "REVIEW_REQUIRED"
        # The raw printed value is preserved, not repaired, on the normalized invoice.
        assert [i.product_code for i in result.invoice.line_items][1:] == PEPSI_CUT_OFF

    def test_a_clean_invoice_gets_no_result_so_its_report_and_confidence_are_unchanged(self):
        report = ValidationService().validate_invoice(_extracted(["0-12000-00113-0", "012000126300", "1263"])).report
        assert not any(c.name == "PRODUCT_CODE_COMPLETE" for c in report.checks)

    def test_revalidation_flags_the_persisted_code_and_clears_once_corrected(self):
        inv = _invoice([_item(0, "012000001130"), _item(1, "0-12000-1263"), _item(2, None, line_type="charge")])
        report = build_report(inv, ocr_confidence=0.95, ai_confidence=0.95)
        flagged = [c for c in report.checks if c.name == "PRODUCT_CODE_COMPLETE"]
        assert [(c.field, c.actual) for c in flagged] == [("line_items[1].product_code", "0-12000-1263")]
        assert any(r.startswith('PRODUCT_CODE_COMPLETE: The printed UPC "0-12000-1263"') for r in report.review_reasons)

        inv.items[1].product_sku = "0-12000-12630-0"
        report = build_report(inv, ocr_confidence=0.95, ai_confidence=0.95)
        assert not any(c.name == "PRODUCT_CODE_COMPLETE" for c in report.checks)

    def test_charge_and_resolved_duplicate_rows_are_not_product_identity_problems(self):
        inv = _invoice([_item(0, "012000001130"), _item(1, "0-12000-1263", line_type="duplicate"),
                        _item(2, "8-10030-51002-", line_type="voided")])
        report = build_report(inv, ocr_confidence=0.95, ai_confidence=0.95)
        assert not any(c.name == "PRODUCT_CODE_COMPLETE" for c in report.checks)


class TestExportGate:
    def test_a_cut_off_code_blocks_export_and_names_the_row_without_completing_it(self):
        inv = _invoice([_item(0, "012000001130"), _item(1, "8-10030-51002-")])
        # The existing identity gate cannot see it: the fragment normalises.
        assert items_with_unresolved_identity(inv) == []
        assert items_with_incomplete_printed_code(inv) == ["ITEM 1"]
        eligibility = pdi_export_eligibility(inv, _units(inv))
        assert eligibility.allowed is False
        assert eligibility.blocked_reason == (
            "1 product has a printed UPC that is cut off (ITEM 1). Enter the full UPC from the "
            "invoice before exporting — a partial code is never completed automatically."
        )
        assert not re.search(r"\d{6,}", eligibility.blocked_reason)

    def test_the_block_counts_every_cut_off_row(self):
        inv = _invoice([_item(n, code) for n, code in enumerate(PEPSI_CUT_OFF)])
        eligibility = pdi_export_eligibility(inv, _units(inv))
        assert eligibility.allowed is False
        assert eligibility.blocked_reason.startswith("3 products have a printed UPC that is cut off (ITEM 0, ITEM 1, ITEM 2)")

    def test_rows_that_never_export_are_not_blocked(self):
        inv = _invoice([_item(0, "012000001130"), _item(1, "0-12000-1263", qty="0"),
                        _item(2, "8-10030-51002-", line_type="charge")])
        assert items_with_incomplete_printed_code(inv) == []
        assert pdi_export_eligibility(inv, _units(inv)).allowed is True

    def test_a_corrected_code_is_eligible_again(self):
        inv = _invoice([_item(0, "0-12000-1263")])
        assert pdi_export_eligibility(inv, _units(inv)).allowed is False
        inv.items[0].product_sku = "0-12000-12630-7"
        assert pdi_export_eligibility(inv, _units(inv)).allowed is True


class TestCorrectionPath:
    def test_the_existing_line_item_correction_carries_a_full_upc_to_the_code_column(self):
        # PATCH /invoices/{id}/items/{sort_order} with product_code is the audited path
        # (history entry with the old value, corrected_fields, revalidation, eligibility).
        correction = LineItemCorrection(product_code="  0-12000-12630-0  ")
        assert correction.updates() == {"product_code": "0-12000-12630-0"}
        assert LineItemCorrection.COLUMNS["product_code"] == "product_sku"
