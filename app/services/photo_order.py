"""
Photo order evidence — app/services/photo_order.py

Several photographs of one long invoice arrive in whatever order they were selected. The extraction prompt tells
the model the photos run top to bottom, and the pipeline used to take that on trust: one real intake stored its
photos bottom-first and lost a third of its rows. Order is now judged from the document itself:

  * HEADER cues (invoice number block, customer block, the start of the item detail) belong to the top photo;
  * FOOTER cues (the final amount-due line, a closing charges block) belong to the bottom photo;
  * overlap: consecutive photos share printed product codes, so the middle of the chain follows its overlaps;
  * capture time in the file name (WhatsApp "at 21.18.39", camera "20261022_211839") breaks remaining ties and
    is reported, but is only a hint — a person may photograph bottom-first.

A reorder is applied only on STRONG evidence (a header photo and a different footer photo, the chain consistent
with them). Otherwise the stored order is kept and the weakness is reported, never guessed at. The photo's own
number stays on every section header, so a row's source_pages still names the stored photo it came from.

Pure functions: texts in, evidence out. Nothing here reads files or the database.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.completeness import PhotoText, ocr_anchors
from app.services.ocr.base import OCRResult
from app.services.photo_context import PhotoOCR, combine_photos
from app.services.validation.report import CheckResult, CheckStatus

_HEADER_CUES = [re.compile(p, re.IGNORECASE) for p in (
    r"\bCust(?:omer)?\s*Rep\b", r"\bRoute\s*#", r"\bLoad\s*No\b", r"\bNRID\b", r"\bSALES SUMMARY\b",
    r"\bITEM DETAIL\b", r"\bBILL\s*TO\b", r"\bSHIP\s*TO\b", r"\bSOLD\s*TO\b", r"\bInvoice\s*(?:Date|No\.?|Number)\b",
    r"\bCustomer\s*#", r"\bVendor\s*#", r"\bTAX\s*ID\b", r"\bACCOM\s*CODE\b", r"\bP\.?O\.?\s*(?:#|No)",
)]
_FOOTER_CUES = [re.compile(p, re.IGNORECASE) for p in (
    r"\bAmount\s+Due\s*\n?\s*for\s+this\s+Invoice", r"\bSTATE/LOCAL\s+CHARGES\s*\n\s*Package", r"\bGRAND\s+TOTAL\b",
    r"\bBALANCE\s+DUE\b", r"\bTHANK\s+YOU\b", r"\bTOTAL\s+DUE\b", r"\bPAGE\s+\d+\s+OF\s+\d+\b",
)]
# WhatsApp "at 21.18.39", camera "20261022_211839", ISO-like "2026-10-22 21.18.39".
_TIME_PATTERNS = [
    re.compile(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})[ _T-]+(?:at\s+)?(\d{2})[.:]?(\d{2})[.:]?(\d{2})"),
    re.compile(r"\bat\s+(\d{2})[.:](\d{2})[.:](\d{2})\b"),
]

STRONG, WEAK, NONE = "strong", "weak", "none"


def capture_key(filename: str | None) -> tuple[int, ...] | None:
    """The capture time encoded in a file name as a sortable tuple, or None when there is none."""
    for pattern in _TIME_PATTERNS:
        m = pattern.search(filename or "")
        if m:
            return tuple(int(g) for g in m.groups())
    return None


def _score(text: str, cues: Sequence[re.Pattern]) -> int:
    return sum(1 for cue in cues if cue.search(text))


@dataclass
class PhotoOrderEvidence:
    stored: list[int]                       # photo numbers in stored (upload) order
    expected: list[int] | None              # top-to-bottom order the evidence supports; None when unknown
    strength: str = NONE
    reorder: bool = False                   # True only on STRONG evidence that the stored order is wrong
    reasons: list[str] = field(default_factory=list)
    capture_order: list[int] | None = None  # photo numbers by capture time when every name carries one

    def to_dict(self) -> dict[str, Any]:
        return {"stored_order": self.stored, "expected_order": self.expected, "strength": self.strength,
                "reordered": self.reorder, "reasons": self.reasons, "capture_order": self.capture_order}


def detect_photo_order(photos: Sequence[PhotoText]) -> PhotoOrderEvidence:
    """Judge the top-to-bottom order of the photos from their own text."""
    stored = [p.page_number for p in photos]
    ev = PhotoOrderEvidence(stored=stored, expected=None)
    if len(photos) < 2:
        return ev

    keys = {p.page_number: capture_key(p.filename) for p in photos}
    if all(keys.values()):
        ev.capture_order = [n for n, _ in sorted(keys.items(), key=lambda kv: kv[1])]

    top = {p.page_number: _score(p.text, _HEADER_CUES) for p in photos}
    bottom = {p.page_number: _score(p.text, _FOOTER_CUES) for p in photos}
    top_best, bottom_best = max(top.values()), max(bottom.values())
    top_photo = next((n for n, s in top.items() if s == top_best), None) if top_best >= 2 and list(top.values()).count(top_best) == 1 else None
    bottom_photo = next((n for n, s in bottom.items() if s == bottom_best), None) if bottom_best >= 1 and list(bottom.values()).count(bottom_best) == 1 else None
    if top_photo is not None and top_photo == bottom_photo:
        bottom_photo = None

    if top_photo is None and bottom_photo is None:
        ev.reasons.append("No header or footer evidence to judge the photo order from.")
        return ev

    # Chain the rest by overlap, starting at the header photo (or ending at the footer photo).
    anchors = ocr_anchors(photos)
    shared = lambda a, b: len(set(anchors.per_photo[a]) & set(anchors.per_photo[b]))  # noqa: E731
    remaining = [n for n in stored if n not in (top_photo, bottom_photo)]
    order: list[int] = [top_photo] if top_photo is not None else []
    while remaining:
        last = order[-1] if order else (min(remaining, key=lambda n: stored.index(n)))
        if not order:
            order.append(last)
            remaining.remove(last)
            continue
        nxt = max(remaining, key=lambda n: (shared(last, n), -stored.index(n)))
        order.append(nxt)
        remaining.remove(nxt)
    if bottom_photo is not None:
        order.append(bottom_photo)
    ev.expected = order

    strong = top_photo is not None and bottom_photo is not None
    ev.strength = STRONG if strong else WEAK
    if top_photo is not None:
        ev.reasons.append(f"Photo {top_photo} carries the invoice header.")
    if bottom_photo is not None:
        ev.reasons.append(f"Photo {bottom_photo} carries the closing totals.")
    if ev.capture_order and ev.capture_order != order:
        ev.reasons.append(f"Capture times run {ev.capture_order}, which differs from the document evidence "
                          f"{order}; the document evidence is used.")
    if order != stored:
        ev.reorder = strong
        ev.reasons.append(f"Stored order {stored} is not top-to-bottom; expected {order}.")
    return ev


def check_photo_order(photos: Sequence[PhotoText]) -> list[CheckResult]:
    """
    The order of the photos AS PRESENTED (the section order in the text), judged from the text. WARNING when it
    still looks wrong or cannot be judged on a multi-photo intake; PASSED when evidence supports it.
    """
    if len(photos) < 2:
        return []
    ev = detect_photo_order(photos)
    if ev.expected is None:
        return [CheckResult("PHOTO_ORDER", CheckStatus.WARNING, field="photos",
                            message="The photo order could not be checked: " + " ".join(ev.reasons))]
    if ev.expected != ev.stored:
        return [CheckResult("PHOTO_ORDER", CheckStatus.WARNING, field="photos", message=" ".join(ev.reasons),
                            expected=str(ev.expected), actual=str(ev.stored))]
    return [CheckResult("PHOTO_ORDER", CheckStatus.PASSED, field="photos",
                        message=" ".join(ev.reasons) or "The photos run top to bottom.")]


def combine_in_detected_order(photos: Sequence[PhotoOCR]) -> tuple[OCRResult, PhotoOrderEvidence]:
    """
    The one place photos are combined for extraction: the order is judged from the document and applied on strong
    evidence only, so the first run and a reprocess read the same text. Returns the combined OCR result and the
    evidence (which the caller records).
    """
    evidence = detect_photo_order([PhotoText(p.page_number, p.filename, p.result.full_text) for p in photos])
    return combine_photos(list(photos), evidence.expected if evidence.reorder else None), evidence
