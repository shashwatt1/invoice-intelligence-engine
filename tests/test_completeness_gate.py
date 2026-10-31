"""
The deterministic completeness gate — tests/test_completeness_gate.py

Every extracted row of an invoice can balance on its own while a third of the document is missing. These tests
pin that the gate answers from the OCR text, not from the model or from per-row arithmetic: missing rows, a
duplicated row, a merged pair, a misplaced row, misaligned quantities and wrong section or summary totals are all
INCOMPLETE; nothing to compare with is UNVERIFIED; only a document whose printed evidence is fully accounted for is
COMPLETE. Fixture: tests/synthetic_invoice.py (61 rows, 15 sections, 68 cases, 958 units, 1,821.18).
"""

from __future__ import annotations

from copy import copy

import pytest

from app.services.completeness import (
    INCOMPLETE_CONFIDENCE_CAP,
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_UNVERIFIED,
    assess_completeness,
    code_key,
    ocr_anchors,
    printed_sections,
    printed_summary,
    split_photos,
)
from app.services.validation.report import CheckStatus
from tests import synthetic_invoice as syn


def names(report, status):
    return {c.name for c in report.checks if c.status is status}


@pytest.fixture
def lines():
    return syn.benchmark_lines()


class TestTheBenchmarkShape:
    def test_the_fixture_reproduces_the_benchmark_arithmetic(self, lines):
        assert len(lines) == syn.LINES_TOTAL == 61
        assert sum(int(i.quantity) for i in lines) == syn.CASES_TOTAL == 68
        assert sum(i.printed_units for i in lines) == syn.UNITS_TOTAL == 958
        assert sum(i.line_total for i in lines) == syn.SUBTOTAL
        assert syn.SUBTOTAL + 0 + syn.CHARGES == syn.AMOUNT_DUE

    def test_the_ocr_exposes_every_code_and_the_printed_totals(self):
        photos = split_photos(syn.ocr_text())
        assert [p.page_number for p in photos] == [1, 2, 3, 4]
        assert len(ocr_anchors(photos).keys) == 61
        assert printed_summary(syn.ocr_text()) == (68, 958)
        assert len(printed_sections(photos)) == 15


class TestACompleteInvoice:
    def test_all_61_rows_with_the_right_totals_is_complete(self, lines):
        report = assess_completeness(syn.ocr_text(), lines)
        assert report.status == STATUS_COMPLETE
        assert (report.anchors, report.lines, report.missing_codes, report.duplicate_codes) == (61, 61, [], [])
        assert not names(report, CheckStatus.FAILED)
        assert {"COMPLETENESS_MISSING_LINES", "COMPLETENESS_SUMMARY_CASES", "COMPLETENESS_SUMMARY_UNITS",
                "COMPLETENESS_SECTION_TOTALS", "COMPLETENESS_DUPLICATE_LINES"} <= names(report, CheckStatus.PASSED)

    @pytest.mark.parametrize("upload_order", [[1, 2, 3, 4], [4, 3, 2, 1], [2, 1, 4, 3]])
    def test_the_verdict_does_not_depend_on_the_upload_order(self, lines, upload_order):
        assert assess_completeness(syn.ocr_text(upload_order), lines).status == STATUS_COMPLETE

    def test_rows_seen_in_two_overlapping_photos_are_one_row(self, lines):
        photos = split_photos(syn.ocr_text())
        anchors = ocr_anchors(photos)
        overlapped = [k for k in anchors.keys if sum(1 for c in anchors.per_photo.values() if k in c) == 2]
        assert overlapped, "the fixture must overlap its photos"
        assert assess_completeness(syn.ocr_text(), lines).status == STATUS_COMPLETE

    def test_a_row_printed_twice_in_one_photo_may_be_extracted_twice(self, lines):
        text = syn.ocr_text()
        row = lines[0]
        repeat = f"\n{row.description} {syn._hyphen(row.product_code)}\n40.00\n1 12 26.86 26.86\n"
        text = text.replace("SUBTOTAL 1 12\n", repeat + "SUBTOTAL 1 12\n", 1)
        twice = [*lines, copy(row)]
        report = assess_completeness(text, twice)
        assert report.duplicate_codes == []


class TestMissingMergedAndMisplacedRows:
    def test_the_benchmarks_forty_of_sixty_one_is_incomplete_and_names_every_gap(self, lines):
        kept = [i for n, i in enumerate(lines) if n % 3 != 2][:40]
        report = assess_completeness(syn.ocr_text(), kept)
        assert report.status == STATUS_INCOMPLETE
        assert len(report.missing_codes) == 61 - len(kept)
        assert {"COMPLETENESS_MISSING_LINES", "COMPLETENESS_SUMMARY_CASES", "COMPLETENESS_SECTION_TOTALS"} \
            <= names(report, CheckStatus.FAILED)

    def test_one_missing_row_is_enough(self, lines):
        gone = lines.pop(30)
        report = assess_completeness(syn.ocr_text(), lines)
        assert report.status == STATUS_INCOMPLETE and report.missing_codes == [code_key(gone.product_code)]

    def test_a_merged_pair_is_a_missing_row(self, lines):
        lines[10].quantity = lines[10].quantity + lines[11].quantity          # row 11's cases folded into row 10
        lines[10].line_total = lines[10].line_total + lines[11].line_total
        del lines[11]
        report = assess_completeness(syn.ocr_text(), lines)
        assert report.status == STATUS_INCOMPLETE and len(report.missing_codes) == 1

    def test_a_duplicated_row_is_incomplete(self, lines):
        lines.append(copy(lines[5]))
        report = assess_completeness(syn.ocr_text(), lines)
        assert report.status == STATUS_INCOMPLETE and report.duplicate_codes == [code_key(lines[5].product_code)]
        assert "COMPLETENESS_DUPLICATE_LINES" in names(report, CheckStatus.FAILED)

    def test_a_row_filed_under_the_wrong_heading_breaks_two_section_totals(self, lines):
        lines[3].section_label = lines[40].section_label
        report = assess_completeness(syn.ocr_text(), lines)
        assert report.status == STATUS_INCOMPLETE
        assert "COMPLETENESS_SECTION_TOTALS" in names(report, CheckStatus.FAILED)
        assert not report.missing_codes                                         # every row is there, one is misfiled

    def test_misaligned_quantities_fail_even_though_each_row_balances_with_itself(self, lines):
        lines[7].quantity += 1
        lines[7].line_total = lines[7].unit_price * lines[7].quantity           # the row's own arithmetic still holds
        assert lines[7].unit_price * lines[7].quantity == lines[7].line_total
        report = assess_completeness(syn.ocr_text(), lines)
        assert report.status == STATUS_INCOMPLETE
        assert {"COMPLETENESS_SUMMARY_CASES", "COMPLETENESS_SECTION_TOTALS"} <= names(report, CheckStatus.FAILED)

    def test_the_figures_of_the_next_row_on_the_wrong_row_fail_the_section(self, lines):
        # Row k reads row k+1's cases (the misalignment that left one SCP row's figures on its neighbour).
        i = next(n for n, row in enumerate(lines) if row.quantity == 2)
        lines[i - 1].quantity = lines[i].quantity
        assert assess_completeness(syn.ocr_text(), lines).status == STATUS_INCOMPLETE

    def test_printed_units_that_do_not_add_up_fail_the_units_summary(self, lines):
        lines[0].printed_units = 13
        report = assess_completeness(syn.ocr_text(), lines)
        assert "COMPLETENESS_SUMMARY_UNITS" in names(report, CheckStatus.FAILED)


class TestNothingToCompare:
    def test_no_ocr_text_is_unverified_never_complete(self, lines):
        for text in (None, "", "   \n"):
            assert assess_completeness(text, lines).status == STATUS_UNVERIFIED

    def test_text_without_any_recognisable_codes_or_totals_is_unverified(self, lines):
        assert assess_completeness("Thank you for your business.\nTotal 12.00", lines).status == STATUS_UNVERIFIED

    def test_a_single_file_text_with_no_photo_headers_is_judged_as_one_photo(self, lines):
        text = "\n".join("\n".join(b) for b in syn.PHOTO_BLOCKS)               # no '--- PHOTO k ---' headers
        report = assess_completeness(text, lines)
        assert report.status == STATUS_INCOMPLETE or report.status == STATUS_COMPLETE
        assert report.anchors == 61


class TestOcrNoise:
    def test_a_zero_read_as_parentheses_still_yields_the_code(self):
        photos = split_photos("A PRODUCT ()-48500-20603-4\n40.00\n")
        assert ocr_anchors(photos).keys == (code_key("048500206034"),)

    def test_a_check_digit_wrapped_to_the_next_line_still_matches_its_row(self, lines):
        text = syn.ocr_text().replace(syn._hyphen(lines[0].product_code), syn._hyphen(lines[0].product_code)[:-2], 1)
        assert code_key(lines[0].product_code) in ocr_anchors(split_photos(text)).keys

    def test_an_unreadable_cases_digit_is_recovered_from_units(self):
        photos = split_photos("16OZ CN 1/12 SCP\nROW 0-12000-02721-5\nSUBTOTAL B 96\n")
        [section] = printed_sections(photos)
        assert (section.cases, section.units, section.per_case) == (8, 96, 12)

    def test_an_item_row_printed_after_a_subtotal_is_not_read_as_a_subtotal(self):
        photos = split_photos("2L PL 1/8S\nSUBTOTAL\n1 8 22.17 22.17\n")
        assert printed_sections(photos) == []

    def test_a_subtotal_that_contradicts_its_heading_is_ignored_as_noise(self):
        assert printed_sections(split_photos("2L PL 1/8S\nSUBTOTAL 3 20\n")) == []

    def test_a_cut_off_code_proves_a_row_exists_that_no_line_accounts_for(self, lines):
        text = syn.ocr_text() + "\n--- PHOTO 5 of 5 (x.jpeg) ---\nSYN CUT OFF 0-90000-1803\n"
        report = assess_completeness(text, lines)
        assert report.status == STATUS_INCOMPLETE and "COMPLETENESS_PARTIAL_CODES" in names(report, CheckStatus.FAILED)


def test_the_cap_for_an_incomplete_invoice_is_below_any_review_threshold_a_reviewer_would_trust():
    assert INCOMPLETE_CONFIDENCE_CAP <= 0.60


class TestNumbersThatAreNotProductCodes:
    @pytest.mark.parametrize("line", [
        "+18477158912", "Phone: 18477158912", "Tel 18477158912", "Fax 18477158912",
        "Account 61126911357", "office@example.com 18477158912",
    ])
    def test_a_phone_account_or_contact_number_is_not_an_anchor(self, line):
        assert ocr_anchors(split_photos(f"SYN SHOP LLC\n{line}\nInvoice#: 7\n")).keys == ()

    def test_a_plain_product_code_on_a_row_still_is(self):
        assert ocr_anchors(split_photos("RED BULL YELLOW 12OZ\n611269133448\n$45.38\n")).keys == (code_key("611269133448"),)

    def test_an_all_zero_placeholder_is_not_a_code(self):
        assert ocr_anchors(split_photos("MISCELLANEOUS DELIVERY CHARGE 000000000000 5.00\n")).keys == ()


class TestWhatCountsAsADuplicate:
    def twice(self, lines, **second):
        row = copy(lines[5])
        for key, value in second.items():
            setattr(row, key, value)
        return [*lines, row]

    def test_the_same_code_with_different_figures_is_a_legitimately_repeated_row(self, lines):
        extra = self.twice(lines, quantity=lines[5].quantity + 1, line_total=lines[5].line_total * 2)
        # printed once per photo in two photos (the row and its second appearance)
        assert assess_completeness(syn.ocr_text(), extra).duplicate_codes == []

    def test_identical_figures_on_two_lines_is_an_unmerged_overlap(self, lines):
        assert assess_completeness(syn.ocr_text(), self.twice(lines)).duplicate_codes == [code_key(lines[5].product_code)]

    def test_a_pair_a_reviewer_decided_is_two_real_rows_is_explained(self, lines):
        extra = self.twice(lines, duplicate_resolved=True)
        assert assess_completeness(syn.ocr_text(), extra).duplicate_codes == []
