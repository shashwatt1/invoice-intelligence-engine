"""
Deterministic completeness gate — app/services/completeness.py

Per-row arithmetic can pass on an invoice that lost a third of its rows: every extracted row balances with
itself. Completeness is a different question, answered here WITHOUT the model, from the OCR text the model was
given, which is independent evidence of what the document printed:

  * ANCHORS   every printed product code (a hyphenated UPC "0-12000-02721-5", or a plain 11-14 digit code)
              should be a line item. A code the OCR saw and no line carries is a missing (or merged) row;
  * DUPLICATES a code extracted on more lines than any single photo printed it;
  * SUMMARY   the printed Sales Summary figures ("Cases 68", "Total Units 958") against what the lines add up to;
  * SECTIONS  each printed section subtotal (a pack-size heading, then "SUBTOTAL <cases> <units>") against the
              cases the rows under that heading add up to — a missing, merged or misplaced row shows up as a
              section that no longer adds up.

Any unexplained difference is INCOMPLETE and routes the invoice to review; an invoice whose rows individually
pass arithmetic is NOT complete because of that. With no OCR text (or nothing to compare) the answer is
UNVERIFIED — never COMPLETE. The functions are pure: text and lines in, findings out.

What it is not: it does not repair anything, never adds a line and never reads the model's confidence.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.services.export_service import normalize_item_code
from app.services.validation.report import CheckResult, CheckStatus, ConfidenceBreakdown

STATUS_COMPLETE = "COMPLETE"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_UNVERIFIED = "UNVERIFIED"

_PHOTO_HEADER = re.compile(r"^--- PHOTO (\d+) of \d+ \((.*)\) ---\s*$", re.MULTILINE)

# "0-12000-02721-5", "8-50645-00838-7", and the OCR variants "()-48500-20603-4" (a leading 0 read as "()") and
# a hyphenated code whose check digit is missing or wrapped to the next line ("0-12000-12635").
_LEAD = r"(?:\d|\(\)|[OQ])"
_HYPHEN_FULL = re.compile(rf"(?<![\w-])({_LEAD})-(\d{{5}})-(\d{{5}})(?:-(\d))?(?![\d-])")
# A code cut short by the printer ("0-12000-1803"): proves a row exists, cannot name it.
_HYPHEN_PARTIAL = re.compile(rf"(?<![\w-])({_LEAD})-(\d{{5}})-(\d{{1,4}})(?![\d-])")
_PLAIN = re.compile(r"(?<![\d.$,+-])(\d{11,14})(?![\d.-])")
# A long digit run on a line that is plainly not a product row: a phone or fax number, a bank or tax identifier,
# a web address. Such a number is never a product code, and counting it would send a complete invoice to review.
_NOT_A_CODE_LINE = re.compile(
    r"(?:\b(?:phone|tel|telephone|fax|cell|mobile|office|account|acct|routing|iban|ein|tax\s*id|www)\b|@|https?:)",
    re.IGNORECASE)

_SUMMARY_CASES = re.compile(r"Sales\s+Cases\s+(\d{1,5})\b", re.IGNORECASE)
_SUMMARY_UNITS = re.compile(r"Total\s+Units\s+(\d{1,6})\b", re.IGNORECASE)
# "SUBTOTAL 5 120" or "SUBTOTAL 1" / "12" on the next line. The units figure must END its line: an item row printed
# right after a SUBTOTAL ("1 8 22.17": cases, units, net) is not a subtotal and must not be read as one.
_SUBTOTAL = re.compile(r"SUBTOTAL[ \t]*\n?[ \t]*([0-9A-Z]{1,4})[ \t]*\n?[ \t]*(\d{1,5})[ \t]*(?:\r?\n|$)")
_HEADING = re.compile(
    r"^\s*(\d+(?:\.\d+)?\s*(?:OZ|0Z|ML|LTR|L(?![A-Z]))[A-Z0-9 .]*?\d\s*/\s*(\d{1,3})[A-Z0-9 ]*)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class PhotoText:
    page_number: int
    filename: str
    text: str


def split_photos(ocr_text: str | None) -> list[PhotoText]:
    """The per-photo sections of a combined OCR text; a single-file text is one photo (number 1)."""
    text = ocr_text or ""
    matches = list(_PHOTO_HEADER.finditer(text))
    if not matches:
        return [PhotoText(1, "", text)] if text.strip() else []
    photos = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        photos.append(PhotoText(int(m.group(1)), m.group(2), text[m.end():end].strip("\n")))
    return photos


def _digits(raw: str) -> str:
    return re.sub(r"[^0-9]", "", raw.replace("()", "0").replace("O", "0").replace("Q", "0"))


def code_key(code: str | None) -> str | None:
    """
    A code's comparison key: its normalized item code, exactly (the 11 digits a UPC-A carries without its check
    digit). Exact on purpose: two sibling products that differ only in their last digit are two rows, and a
    prefix match would let one stand in for the other. A code whose check digit was lost still normalizes to the
    same 11 digits; one with a digit misread simply does not match, which sends the invoice to review.
    """
    if not code:
        return None
    normalized = normalize_item_code(code)
    if not normalized or not normalized.strip("0"):
        return None
    return normalized


@dataclass(frozen=True)
class Anchors:
    keys: tuple[str, ...]              # one entry per distinct full code, in first-seen order
    per_photo: dict[int, Counter]      # photo number -> Counter of keys printed in that photo
    partial: tuple[str, ...]           # distinct truncated codes: rows that exist but cannot be named


def ocr_anchors(photos: Sequence[PhotoText]) -> Anchors:
    keys: dict[str, None] = {}
    per_photo: dict[int, Counter] = {}
    partial: dict[str, None] = {}
    for photo in photos:
        seen = Counter()
        taken: list[tuple[int, int]] = []
        for m in _HYPHEN_FULL.finditer(photo.text):
            key = code_key(_digits("".join(g or "" for g in m.groups())))
            taken.append(m.span())
            if key:
                seen[key] += 1
                keys.setdefault(key)
        for m in _PLAIN.finditer(photo.text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            line_start = photo.text.rfind("\n", 0, m.start()) + 1
            line_end = photo.text.find("\n", m.end())
            if _NOT_A_CODE_LINE.search(photo.text[line_start: line_end if line_end >= 0 else len(photo.text)]):
                continue
            key = code_key(m.group(1))
            if key:
                seen[key] += 1
                keys.setdefault(key)
        for m in _HYPHEN_PARTIAL.finditer(photo.text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            partial.setdefault(re.sub(r"\s", "", m.group(0)))
        per_photo[photo.page_number] = seen
    return Anchors(tuple(keys), per_photo, tuple(partial))


@dataclass(frozen=True)
class PrintedSection:
    heading: str
    cases: int | None
    units: int | None
    per_case: int | None               # the N of "1/N" in the heading


def _heading_key(heading: str | None) -> str:
    return re.sub(r"[^A-Z0-9/]", "", (heading or "").upper().replace("0Z", "OZ"))


def printed_sections(photos: Sequence[PhotoText]) -> list[PrintedSection]:
    """
    Each (heading, SUBTOTAL cases units) pair printed on the document, de-duplicated across overlapping photos.
    A SUBTOTAL is paired with the nearest heading above it in the SAME photo; one with no heading in its photo
    is ignored (nothing to compare it with). OCR may read a digit as a letter ("SUBTOTAL B 96"): the unreadable
    figure becomes None and cases are recovered from units ÷ N when that divides evenly.
    """
    found: dict[str, PrintedSection] = {}
    for photo in photos:
        headings = [(m.start(), m.group(1).strip(), int(m.group(2))) for m in _HEADING.finditer(photo.text)]
        for sub in _SUBTOTAL.finditer(photo.text):
            above = [h for h in headings if h[0] < sub.start()]
            if not above:
                continue
            _, heading, per_case = above[-1]
            units = int(sub.group(2))
            cases = int(sub.group(1)) if sub.group(1).isdigit() else None
            if cases is None and per_case and units % per_case == 0:
                cases = units // per_case
            if cases is not None and per_case and cases * per_case != units:
                continue                                  # OCR noise: the subtotal does not agree with itself
            found.setdefault(f"{_heading_key(heading)}|{cases}|{units}", PrintedSection(heading, cases, units, per_case))
    return list(found.values())


def printed_summary(ocr_text: str | None) -> tuple[int | None, int | None]:
    """The Sales Summary's printed (cases, units), when the document has one."""
    text = ocr_text or ""
    cases, units = _SUMMARY_CASES.search(text), _SUMMARY_UNITS.search(text)
    return (int(cases.group(1)) if cases else None, int(units.group(1)) if units else None)


@dataclass
class CompletenessReport:
    status: str
    checks: list[CheckResult] = field(default_factory=list)
    anchors: int = 0
    lines: int = 0
    missing_codes: list[str] = field(default_factory=list)
    duplicate_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "anchors": self.anchors, "lines": self.lines,
            "missing_codes": self.missing_codes, "duplicate_codes": self.duplicate_codes,
            "findings": [c.to_dict() for c in self.checks if c.status in (CheckStatus.FAILED, CheckStatus.WARNING)],
        }


def _product_lines(lines: Sequence[Any]) -> list[Any]:
    return [i for i in lines if (getattr(i, "line_type", "product") or "product") == "product"]


def _qty(item: Any) -> float:
    q = getattr(item, "quantity", None)
    return float(q) if q is not None else 0.0


def assess_completeness(ocr_text: str | None, lines: Sequence[Any]) -> CompletenessReport:
    """
    Compare the OCR's printed evidence with the extracted lines. `lines` are objects with product_code,
    pack_size, section_label, quantity, printed_units and line_type (the normalized or persisted rows).
    """
    photos = split_photos(ocr_text)
    products = _product_lines(lines)
    if not photos:
        return CompletenessReport(STATUS_UNVERIFIED, [CheckResult(
            "COMPLETENESS", CheckStatus.SKIPPED, message="No OCR text to verify the extraction against.")],
            lines=len(products))

    anchors = ocr_anchors(photos)
    checks: list[CheckResult] = []
    extracted_keys = Counter(k for k in (code_key(getattr(i, "product_code", None)) for i in products) if k)

    # ---- every printed code is a line ----
    missing = [k for k in anchors.keys if k not in extracted_keys]
    if missing:
        checks.append(CheckResult(
            "COMPLETENESS_MISSING_LINES", CheckStatus.FAILED, field="line_items",
            message=(f"{len(missing)} product code(s) printed on the document have no extracted line: "
                     + ", ".join(missing[:12]) + (" …" if len(missing) > 12 else "")
                     + ". A row was missed or merged into another."),
            expected=str(len(anchors.keys) + len(anchors.partial)), actual=str(len(products))))
    elif anchors.keys:
        checks.append(CheckResult("COMPLETENESS_MISSING_LINES", CheckStatus.PASSED, field="line_items",
                                  message=f"All {len(anchors.keys)} printed product codes are extracted lines."))
    else:
        checks.append(CheckResult("COMPLETENESS_MISSING_LINES", CheckStatus.SKIPPED, field="line_items",
                                  message="No printed product codes were recognised in the OCR text."))
    needed_rows = len(anchors.keys) + len(anchors.partial)
    if anchors.partial and not missing and len(products) < needed_rows:
        checks.append(CheckResult(
            "COMPLETENESS_PARTIAL_CODES", CheckStatus.FAILED, field="line_items",
            message=(f"{len(anchors.partial)} printed code(s) are cut off ({', '.join(anchors.partial[:5])}) and the "
                     f"document shows {needed_rows} rows but {len(products)} were extracted."),
            expected=str(needed_rows), actual=str(len(products))))

    # ---- no row on more lines than a photo printed it ----
    # An overlap that was not merged is the SAME row twice: same code and identical figures, printed once per photo.
    # A code on two lines with different figures is a legitimately repeated row (a case, then a shorted-and-
    # delivered row), and a pair a reviewer decided is two real rows is explained. Neither is a duplicate.
    same_row = Counter()
    for i in products:
        key = code_key(getattr(i, "product_code", None))
        if key and not getattr(i, "duplicate_resolved", False):
            same_row[(key, getattr(i, "quantity", None), getattr(i, "unit_price", None),
                      getattr(i, "line_total", None))] += 1
    duplicates = []
    for (key, *_), n in same_row.items():
        printed = max((c.get(key, 0) for c in anchors.per_photo.values()), default=0)
        if printed and n > printed and key not in duplicates:
            duplicates.append(key)
    if duplicates:
        checks.append(CheckResult(
            "COMPLETENESS_DUPLICATE_LINES", CheckStatus.FAILED, field="line_items",
            message=("Code(s) extracted on more lines than any photo printed them: " + ", ".join(duplicates[:12])
                     + ". A row was probably counted twice (an overlap not merged).")))
    elif anchors.keys:
        checks.append(CheckResult("COMPLETENESS_DUPLICATE_LINES", CheckStatus.PASSED, field="line_items",
                                  message="No product was extracted more often than it was printed."))

    # ---- printed Sales Summary ----
    printed_cases, printed_units = printed_summary(ocr_text)
    cases_sum = sum(_qty(i) for i in products)
    if printed_cases is not None:
        if abs(cases_sum - printed_cases) > 1e-9:
            checks.append(CheckResult(
                "COMPLETENESS_SUMMARY_CASES", CheckStatus.FAILED, field="line_items",
                message=f"The printed summary says {printed_cases} cases; the extracted lines add up to {cases_sum:g}.",
                expected=str(printed_cases), actual=f"{cases_sum:g}"))
        else:
            checks.append(CheckResult("COMPLETENESS_SUMMARY_CASES", CheckStatus.PASSED, field="line_items",
                                      message=f"Extracted cases match the printed {printed_cases}."))
    if printed_units is not None and products and all(getattr(i, "printed_units", None) is not None for i in products):
        units_sum = sum(int(i.printed_units) for i in products)
        status = CheckStatus.PASSED if units_sum == printed_units else CheckStatus.FAILED
        checks.append(CheckResult(
            "COMPLETENESS_SUMMARY_UNITS", status, field="line_items",
            message=(f"Extracted units match the printed {printed_units}." if status is CheckStatus.PASSED
                     else f"The printed summary says {printed_units} units; the extracted lines add up to {units_sum}."),
            expected=str(printed_units), actual=str(units_sum)))

    # ---- printed section subtotals ----
    sections = printed_sections(photos)
    if sections:
        by_section: dict[str, float] = {}
        for item in products:
            label = getattr(item, "section_label", None) or getattr(item, "pack_size", None)
            by_section[_heading_key(label)] = by_section.get(_heading_key(label), 0.0) + _qty(item)
        printed_by: dict[str, tuple[PrintedSection, int]] = {}
        for sec in sections:
            if sec.cases is None:
                continue
            key = _heading_key(sec.heading)
            prev = printed_by.get(key)
            printed_by[key] = (sec, (prev[1] if prev else 0) + sec.cases)
        bad = []
        for key, (sec, cases) in printed_by.items():
            got = by_section.get(key, 0.0)
            if abs(got - cases) > 1e-9:
                bad.append(f"{sec.heading}: printed {cases} case(s), extracted {got:g}")
        if bad:
            checks.append(CheckResult(
                "COMPLETENESS_SECTION_TOTALS", CheckStatus.FAILED, field="line_items",
                message=(f"{len(bad)} of {len(printed_by)} section subtotal(s) do not add up — a row is missing, "
                         "merged or filed under the wrong heading: " + "; ".join(bad[:6]) + (" …" if len(bad) > 6 else ""))))
        elif printed_by:
            checks.append(CheckResult("COMPLETENESS_SECTION_TOTALS", CheckStatus.PASSED, field="line_items",
                                      message=f"All {len(printed_by)} printed section subtotals add up."))

    failed = any(c.status is CheckStatus.FAILED for c in checks)
    compared = any(c.status in (CheckStatus.PASSED, CheckStatus.FAILED) for c in checks)
    status = STATUS_INCOMPLETE if failed else STATUS_COMPLETE if compared else STATUS_UNVERIFIED
    if status == STATUS_UNVERIFIED and not checks:
        checks.append(CheckResult("COMPLETENESS", CheckStatus.SKIPPED, message="Nothing printed could be compared."))
    return CompletenessReport(status, checks, anchors=len(anchors.keys), lines=len(products),
                              missing_codes=missing, duplicate_codes=duplicates)


# An invoice that is missing rows must not read as a well-extracted one. Its composite confidence is capped here
# (the uncapped figure is kept beside it), so no screen can present a high score for an incomplete document.
INCOMPLETE_CONFIDENCE_CAP = 0.60


def apply_completeness_gate(
    checks: list[CheckResult], confidence: ConfidenceBreakdown, ocr_text: str | None, lines: Sequence[Any],
) -> tuple[list[CheckResult], ConfidenceBreakdown, CompletenessReport]:
    """
    Run the gate and fold it into a validation run: its checks are appended (a FAILED one routes the invoice to
    review through the ordinary decision rule), the confidence records the completeness status and, when
    INCOMPLETE, is capped. Both the first-time validation and the revalidation after a correction call this.
    """
    from app.services.photo_order import check_photo_order  # local: photo_order imports this module

    report = assess_completeness(ocr_text, lines)
    report.checks.extend(check_photo_order(split_photos(ocr_text)))
    capped = confidence.composite
    if report.status == STATUS_INCOMPLETE:
        capped = min(confidence.composite, INCOMPLETE_CONFIDENCE_CAP)
    confidence = replace(
        confidence, composite=capped, completeness=report.status,
        composite_uncapped=confidence.composite if capped != confidence.composite else None)
    return [*checks, *report.checks], confidence, report
