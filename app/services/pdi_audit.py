"""
PDI Export Audit — app/services/pdi_audit.py

Decodes a generated PDI file back into named byte fields and checks it
against the confirmed data contract.

Deliberately written as a DECODER, not as a second copy of the encoder:
it reads the finished bytes and reconstructs what each slice means, then
compares that to the source invoice. A bug that made the encoder shift a
field would be reproduced by a re-implementation of the encoder, but is
caught here, because the check runs from the opposite direction.

Two classes of check, kept apart on purpose:

  STRUCTURAL — record lengths, line endings, digit-only fields, the
  "0100" marker, exactly one AMOUNT record. These follow from the byte
  layout confirmed against real accepted PDI files, so they are
  enforced: build_pdi_export() refuses to return a file that fails one.

  CONTENT — item code, description, case cost, units per case, quantity,
  batch, date, amount. These compare the file to the invoice it came
  from. Reported, and available to the caller, but a mismatch here means
  "look at this", not "the layout is broken".

One figure is reported WITHOUT a verdict: the header AMOUNT against the
sum of the detail lines. Whether PDI requires those to balance is
genuinely unresolved (docs/PDI_OPEN_QUESTIONS.md Q7) and no accepted
file in our possession settles it, so this module measures the gap and
refuses to call it pass or fail.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from app.models.invoice import Invoice
from app.services.export_service import (
    PDI_AMOUNT_WIDTH,
    PDI_BATCH_WIDTH,
    PDI_DATE_FORMAT,
    _sorted_items,
    normalize_item_code,
)

# Absolute byte offsets of the 70-char B record. CONFIRMED — see the byte
# map in export_service.py and docs/PDI_CASE_COST_INVESTIGATION.md.
B_RECORD_WIDTH = 70
B_SLICES: dict[str, tuple[int, int]] = {
    "record_type": (0, 1),
    "item_code": (1, 12),
    "description": (12, 37),
    "product_ref": (37, 43),
    "case_cost": (43, 49),
    "marker": (49, 53),
    "units_per_case": (53, 57),
    "sign": (57, 58),
    "quantity": (58, 62),
    "edi_srp": (62, 67),
    "tail": (67, 70),
}
HEADER_SLICES: dict[str, tuple[int, int]] = {
    "tag": (0, 6),
    "batch": (7, 14),
    "date": (17, 23),
    "sign": (23, 24),
    "amount": (24, 24 + PDI_AMOUNT_WIDTH),
}
MARKER = "0100"
# A line with no product code is emitted as "00000" + 6 spaces — a
# CONFIRMED convention, so the item-code field is the one B-record field
# that is legitimately not all digits.
BLANK_ITEM_CODE = "00000" + " " * 6


@dataclass(frozen=True)
class PdiField:
    """One decoded byte range."""

    name: str
    start: int
    end: int
    raw: str
    value: str | None = None  # human-readable interpretation, when one exists


@dataclass(frozen=True)
class PdiRecord:
    index: int
    record_type: str
    length: int
    raw: str
    fields: list[PdiField]


@dataclass(frozen=True)
class PdiCheck:
    name: str
    passed: bool
    kind: str  # "structural" | "content"
    expected: str
    actual: str
    detail: str | None = None


@dataclass(frozen=True)
class PdiAudit:
    """Machine-readable audit of one generated PDI file."""

    byte_count: int
    record_count: int
    detail_count: int
    records: list[PdiRecord] = field(default_factory=list)
    checks: list[PdiCheck] = field(default_factory=list)
    # Header/detail balance — measured, deliberately not judged. See Q7.
    header_amount_cents: int = 0
    detail_total_cents: int = 0

    @property
    def structural_failures(self) -> list[PdiCheck]:
        return [c for c in self.checks if not c.passed and c.kind == "structural"]

    @property
    def content_failures(self) -> list[PdiCheck]:
        return [c for c in self.checks if not c.passed and c.kind == "content"]

    @property
    def ok(self) -> bool:
        """True when nothing failed, structural or content."""
        return not any(not c.passed for c in self.checks)

    @property
    def balance_difference_cents(self) -> int:
        return self.header_amount_cents - self.detail_total_cents

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "byte_count": self.byte_count,
            "record_count": self.record_count,
            "detail_count": self.detail_count,
            "header_amount_cents": self.header_amount_cents,
            "detail_total_cents": self.detail_total_cents,
            "balance_difference_cents": self.balance_difference_cents,
            "records": [asdict(r) for r in self.records],
            "checks": [asdict(c) for c in self.checks],
        }


def _decode_header(line: str) -> PdiRecord:
    fields = [
        PdiField("tag", *HEADER_SLICES["tag"], line[slice(*HEADER_SLICES["tag"])]),
        PdiField("batch", *HEADER_SLICES["batch"], line[slice(*HEADER_SLICES["batch"])]),
        PdiField("date", *HEADER_SLICES["date"], line[slice(*HEADER_SLICES["date"])]),
        PdiField("sign", *HEADER_SLICES["sign"], line[slice(*HEADER_SLICES["sign"])]),
    ]
    amount = line[slice(*HEADER_SLICES["amount"])]
    fields.append(
        PdiField(
            "amount",
            *HEADER_SLICES["amount"],
            amount,
            f"${int(amount) / 100:.2f}" if amount.isdigit() else None,
        )
    )
    return PdiRecord(0, "AMOUNT", len(line), line, fields)


def _decode_detail(index: int, line: str) -> PdiRecord:
    fields: list[PdiField] = []
    for name, (start, end) in B_SLICES.items():
        raw = line[start:end]
        value: str | None = None
        if name == "case_cost" and raw.isdigit():
            value = f"${int(raw) / 100:.2f}"
        elif name in ("units_per_case", "quantity") and raw.isdigit():
            value = str(int(raw))
        elif name == "edi_srp" and raw.isdigit():
            value = f"${int(raw) / 100:.2f}"
        elif name == "description":
            value = raw.rstrip()
        elif name == "item_code":
            value = None if raw == BLANK_ITEM_CODE else raw
        fields.append(PdiField(name, start, end, raw, value))
    return PdiRecord(index, "B", len(line), line, fields)


def _field(record: PdiRecord, name: str) -> str:
    return next(f.raw for f in record.fields if f.name == name)


def _check(
    checks: list[PdiCheck],
    name: str,
    passed: bool,
    kind: str,
    expected: Any,
    actual: Any,
    detail: str | None = None,
) -> None:
    checks.append(PdiCheck(name, passed, kind, str(expected), str(actual), detail))


def audit_pdi_export(text: str, invoice: Invoice | None = None) -> PdiAudit:
    """
    Decode and verify a generated PDI file.

    Pass `invoice` to additionally cross-check every emitted value
    against the data it was built from; without it only the structural
    contract is checked (useful for auditing a file whose source invoice
    is not to hand).
    """
    checks: list[PdiCheck] = []
    raw = text.encode()

    # --- line endings: CRLF everywhere, nothing bare ---
    crlf = raw.count(b"\r\n")
    bare_lf = raw.count(b"\n") - crlf
    bare_cr = raw.count(b"\r") - crlf
    _check(checks, "line_endings_crlf_only", bare_lf == 0 and bare_cr == 0,
           "structural", "0 bare LF, 0 bare CR", f"{bare_lf} bare LF, {bare_cr} bare CR",
           "A plain-LF file was rejected by a real PDI import.")
    _check(checks, "file_ends_with_crlf", raw.endswith(b"\r\n"),
           "structural", "trailing CRLF", "trailing CRLF" if raw.endswith(b"\r\n") else "missing")

    lines = [line for line in text.split("\r\n") if line]

    # --- exactly one AMOUNT record, and it comes first ---
    headers = [i for i, line in enumerate(lines) if line.startswith("AMOUNT")]
    _check(checks, "exactly_one_amount_record", len(headers) == 1,
           "structural", 1, len(headers))
    _check(checks, "amount_record_is_first", headers == [0] if headers else False,
           "structural", "index 0", headers)

    records: list[PdiRecord] = []
    if headers == [0]:
        records.append(_decode_header(lines[0]))

    detail_lines = [line for line in lines if line.startswith("B")]
    for i, line in enumerate(detail_lines, start=1):
        records.append(_decode_detail(i, line))

    # --- every B record exactly 70 chars ---
    bad_len = [(r.index, r.length) for r in records if r.record_type == "B"
               and r.length != B_RECORD_WIDTH]
    _check(checks, "b_records_are_70_chars", not bad_len,
           "structural", f"all == {B_RECORD_WIDTH}", bad_len or "all 70")

    # --- digit-only fields: the guard against silent field shifting ---
    digit_fields = ("product_ref", "case_cost", "marker", "units_per_case",
                    "quantity", "edi_srp", "tail")
    shifted: list[str] = []
    for record in records:
        if record.record_type != "B":
            continue
        for name in digit_fields:
            value = _field(record, name)
            if not value.isdigit():
                shifted.append(f"record {record.index} {name}={value!r}")
        code = _field(record, "item_code")
        if not code.isdigit() and code != BLANK_ITEM_CODE:
            shifted.append(f"record {record.index} item_code={code!r}")
    _check(checks, "no_field_shifting", not shifted,
           "structural", "every fixed field parses in place", shifted or "clean",
           "A non-digit in a numeric field means an upstream field changed width.")

    # --- confirmed constant marker ---
    bad_marker = [r.index for r in records
                  if r.record_type == "B" and _field(r, "marker") != MARKER]
    _check(checks, "marker_constant", not bad_marker,
           "structural", MARKER, bad_marker or MARKER)

    # --- sign is uniform across the whole file ---
    signs = {_field(r, "sign") for r in records}
    _check(checks, "sign_uniform", len(signs) <= 1 and signs <= {"+", "-"},
           "structural", "one of + / - throughout", sorted(signs))

    # --- units per case is never zero or out of range ---
    bad_units = [(r.index, _field(r, "units_per_case")) for r in records
                 if r.record_type == "B" and _field(r, "units_per_case").isdigit()
                 and not 1 <= int(_field(r, "units_per_case")) <= 9999]
    _check(checks, "units_per_case_in_range", not bad_units,
           "structural", "1..9999", bad_units or "all in range")

    # --- nothing fabricated in the fields we deliberately leave empty ---
    fabricated = [r.index for r in records if r.record_type == "B"
                  and (_field(r, "product_ref") != "000000"
                       or _field(r, "edi_srp") != "00000")]
    _check(checks, "no_fabricated_values", not fabricated,
           "structural", "product_ref and edi_srp left zero", fabricated or "clean",
           "Neither value exists on a wholesale invoice; zero keeps PDI's own data.")

    header = records[0] if records and records[0].record_type == "AMOUNT" else None
    header_cents = (
        int(_field(header, "amount")) if header and _field(header, "amount").isdigit() else 0
    )
    detail_cents = sum(
        int(_field(r, "case_cost")) * int(_field(r, "quantity"))
        for r in records
        if r.record_type == "B"
        and _field(r, "case_cost").isdigit()
        and _field(r, "quantity").isdigit()
    )

    if invoice is not None:
        _audit_against_invoice(checks, records, header, invoice)

    return PdiAudit(
        byte_count=len(raw),
        record_count=len(lines),
        detail_count=len(detail_lines),
        records=records,
        checks=checks,
        header_amount_cents=header_cents,
        detail_total_cents=detail_cents,
    )


def _cents(value: Decimal | None) -> int:
    return round(float(abs(value)) * 100) if value is not None else 0


def _audit_against_invoice(
    checks: list[PdiCheck],
    records: list[PdiRecord],
    header: PdiRecord | None,
    invoice: Invoice,
) -> None:
    """Cross-check the decoded file against the invoice it was built from."""
    items = _sorted_items(invoice)
    details = [r for r in records if r.record_type == "B"]

    _check(checks, "b_record_count_matches_line_items", len(details) == len(items),
           "content", len(items), len(details))

    if header is not None:
        expected_batch = re.sub(r"\D", "", invoice.invoice_number or "")
        expected_batch = (expected_batch[-PDI_BATCH_WIDTH:] if expected_batch else "").rjust(
            PDI_BATCH_WIDTH, "0"
        )
        _check(checks, "batch_is_invoice_number",
               _field(header, "batch") == expected_batch,
               "content", expected_batch, _field(header, "batch"))

        expected_date = (
            invoice.invoice_date.strftime(PDI_DATE_FORMAT) if invoice.invoice_date else "0" * 6
        )
        _check(checks, "date_matches_invoice", _field(header, "date") == expected_date,
               "content", expected_date, _field(header, "date"))

        expected_amount = str(_cents(invoice.grand_total)).rjust(PDI_AMOUNT_WIDTH, "0")
        _check(checks, "header_amount_is_grand_total",
               _field(header, "amount") == expected_amount,
               "content", expected_amount, _field(header, "amount"),
               "Currently the invoice grand total. See Q7 before trusting this.")

        expected_sign = "-" if (invoice.grand_total or 0) < 0 else "+"
        _check(checks, "sign_matches_invoice_direction",
               _field(header, "sign") == expected_sign,
               "content", expected_sign, _field(header, "sign"))

    for record, item in zip(details, items, strict=False):
        label = f"line {item.sort_order + 1}"

        code = normalize_item_code(item.product_sku)
        expected_code = code.rjust(11, "0") if code else BLANK_ITEM_CODE
        _check(checks, f"item_code[{label}]", _field(record, "item_code") == expected_code,
               "content", expected_code, _field(record, "item_code"))

        expected_desc = (item.description or "")[:25].ljust(25)
        _check(checks, f"description[{label}]", _field(record, "description") == expected_desc,
               "content", repr(expected_desc), repr(_field(record, "description")))

        expected_cost = str(_cents(item.unit_price)).rjust(6, "0")[-6:]
        _check(checks, f"case_cost[{label}]", _field(record, "case_cost") == expected_cost,
               "content", expected_cost, _field(record, "case_cost"),
               "Case cost is the NET unit cost from the invoice.")

        expected_qty = str(max(int(item.quantity) if item.quantity is not None else 0, 0)).rjust(
            4, "0"
        )
        _check(checks, f"quantity[{label}]", _field(record, "quantity") == expected_qty,
               "content", expected_qty, _field(record, "quantity"))
