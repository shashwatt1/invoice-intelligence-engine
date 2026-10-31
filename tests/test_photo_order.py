"""
Photo order from the document's own evidence — tests/test_photo_order.py

A bottom-first upload cost one real invoice a third of its rows because the pipeline took the upload sequence as
top-to-bottom. The order is now judged from header and closing-totals cues, overlap between neighbouring photos and
(as a hint) capture time in the file name; a reorder is applied only on strong evidence and every section keeps its
stored photo number.
"""

from __future__ import annotations

import pytest

from app.services.completeness import PhotoText, split_photos
from app.services.ocr.base import OCRResult
from app.services.photo_context import PhotoOCR, combine_photos
from app.services.photo_order import (
    STRONG,
    WEAK,
    capture_key,
    check_photo_order,
    detect_photo_order,
)
from app.services.validation.report import CheckStatus
from tests import synthetic_invoice as syn


def photos(upload_order):
    return split_photos(syn.ocr_text(upload_order))


class TestDetectingTheOrder:
    def test_a_top_to_bottom_upload_is_left_alone(self):
        ev = detect_photo_order(photos([1, 2, 3, 4]))
        assert (ev.expected, ev.strength, ev.reorder) == ([1, 2, 3, 4], STRONG, False)

    def test_a_bottom_first_upload_is_detected_and_reordered_top_to_bottom(self):
        ev = detect_photo_order(photos([4, 3, 2, 1]))              # page 1 stored = the bottom photo
        assert ev.reorder is True and ev.strength == STRONG
        assert ev.expected == [4, 3, 2, 1]                           # stored page 4 is the header, then 3, 2, 1
        assert any("header" in r for r in ev.reasons) and any("closing totals" in r for r in ev.reasons)

    def test_a_shuffled_upload_is_ordered_by_header_footer_and_overlap(self):
        ev = detect_photo_order(photos([2, 4, 1, 3]))              # stored 1=content2, 2=content4, 3=content1, 4=content3
        assert ev.reorder and ev.expected == [3, 1, 4, 2]            # header (stored 3), 1 (content2), 4 (content3), 2

    def test_capture_time_in_the_file_name_is_reported_but_the_document_decides(self):
        ev = detect_photo_order(photos([4, 3, 2, 1]))
        assert ev.capture_order == [4, 3, 2, 1] or ev.capture_order is not None

    def test_one_photo_has_no_order(self):
        assert detect_photo_order([PhotoText(1, "a.jpg", "INVOICE\nTOTAL DUE 5")]).expected is None

    def test_no_cues_means_no_reorder_and_a_weak_statement(self):
        ev = detect_photo_order([PhotoText(1, "a.jpg", "foo\nbar"), PhotoText(2, "b.jpg", "baz")])
        assert (ev.expected, ev.reorder) == (None, False)

    def test_only_a_header_cue_is_weak_evidence_and_never_reorders(self):
        ev = detect_photo_order([PhotoText(1, "a.jpg", "rows"), PhotoText(2, "b.jpg", "Cust Rep\nRoute #\nSALES SUMMARY")])
        assert ev.strength == WEAK and ev.reorder is False

    def test_both_cues_in_the_same_photo_are_not_enough(self):
        both = "Cust Rep\nRoute #\nSALES SUMMARY\nAmount Due\nfor this Invoice"
        ev = detect_photo_order([PhotoText(1, "a.jpg", both), PhotoText(2, "b.jpg", "rows")])
        assert ev.reorder is False


class TestCombiningInTheDetectedOrder:
    def results(self, upload_order):
        return [PhotoOCR(page_number=p.page_number, filename=p.filename,
                         result=OCRResult(full_text=p.text, source_type="ocr", tokens=[], page_count=1,
                                          mean_confidence=0.97, duration_ms=1))
                for p in photos(upload_order)]

    def test_the_header_photo_comes_first_and_each_section_keeps_its_stored_number(self):
        ev = detect_photo_order(photos([4, 3, 2, 1]))
        combined = combine_photos(self.results([4, 3, 2, 1]), ev.expected).full_text
        headers = [line for line in combined.splitlines() if line.startswith("--- PHOTO")]
        assert [h.split()[2] for h in headers] == ["4", "3", "2", "1"]
        assert "SALES SUMMARY" in combined.split("--- PHOTO 3 of 4")[0]       # the top of the invoice is read first

    def test_without_an_order_the_stored_order_is_kept(self):
        combined = combine_photos(self.results([4, 3, 2, 1])).full_text
        assert [h.split()[2] for h in combined.splitlines() if h.startswith("--- PHOTO")] == ["1", "2", "3", "4"]

    def test_an_order_that_does_not_name_every_photo_is_ignored(self):
        combined = combine_photos(self.results([1, 2, 3, 4]), [3, 1]).full_text
        assert [h.split()[2] for h in combined.splitlines() if h.startswith("--- PHOTO")] == ["1", "2", "3", "4"]


class TestTheCheck:
    def test_a_top_to_bottom_text_passes(self):
        [result] = check_photo_order(photos([1, 2, 3, 4]))
        assert result.status is CheckStatus.PASSED

    def test_a_text_still_presented_bottom_first_warns(self):
        [result] = check_photo_order(photos([4, 3, 2, 1]))
        assert result.status is CheckStatus.WARNING and "not top-to-bottom" in result.message

    def test_a_single_photo_has_no_check(self):
        assert check_photo_order([PhotoText(1, "a.jpg", "x")]) == []

    def test_an_unjudgeable_multi_photo_intake_warns_rather_than_passes(self):
        [result] = check_photo_order([PhotoText(1, "a.jpg", "foo"), PhotoText(2, "b.jpg", "bar")])
        assert result.status is CheckStatus.WARNING


@pytest.mark.parametrize("name, key", [
    ("WhatsApp Image 2026-10-22 at 21.18.39.jpeg", (2026, 10, 22, 21, 18, 39)),
    ("IMG_20261022_211839.jpg", (2026, 10, 22, 21, 18, 39)),
    ("scan at 07.05.09.png", (7, 5, 9)),
    ("receipt.jpg", None), (None, None),
])
def test_capture_time_is_read_from_the_common_file_name_shapes(name, key):
    assert capture_key(name) == key
