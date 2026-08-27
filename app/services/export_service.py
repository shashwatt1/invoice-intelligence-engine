"""
Invoice Export Service — app/services/export_service.py

Builds ERP-consumable representations of a persisted invoice:

    JSON — the final validated invoice object (what validation wrote to
           the database), NOT the raw LLM response
    TXT  — human-readable summary
    CSV  — line items for accounting systems

Design decisions:
- Purely read-side and additive: everything here is a projection of the
  Invoice ORM aggregate loaded by InvoiceRepository.get_detail(). No
  pipeline or validation logic is duplicated — the persisted row IS the
  validated object.
- Money is emitted as JSON numbers (consistent with the existing detail
  API); the database remains the precision source of truth.
- schema_version lets ERP consumers integrate against a stable contract
  that can evolve additively.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.product_case_mapping import MAX_UNITS_PER_CASE, MIN_UNITS_PER_CASE

EXPORT_SCHEMA_VERSION = "1.0"

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _sorted_items(invoice: Invoice) -> list[InvoiceItem]:
    return sorted(invoice.items, key=lambda item: item.sort_order)


def export_basename(invoice: Invoice) -> str:
    """Safe filename stem: invoice number if usable, else the invoice id."""
    stem = invoice.invoice_number or str(invoice.id)[:8]
    cleaned = _FILENAME_SAFE.sub("-", stem).strip("-.")
    return f"invoice_{cleaned or str(invoice.id)[:8]}"


# ---------------------------------------------------------------------------
# JSON — the validated structured invoice
# ---------------------------------------------------------------------------


def build_export_payload(invoice: Invoice) -> dict[str, Any]:
    """The final validated invoice object, as persisted by the pipeline."""
    vendor = invoice.vendor
    document = invoice.document
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "invoice_id": str(invoice.id),
        "document_id": str(invoice.document_id),
        "invoice": {
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "currency": invoice.currency,
            "totals": {
                "subtotal": _num(invoice.subtotal),
                "tax": _num(invoice.tax_amount),
                "discount": _num(invoice.discount_amount),
                "grand_total": _num(invoice.grand_total),
            },
        },
        "vendor": {
            "name": vendor.name if vendor else invoice.vendor_name,
            "tax_id": vendor.tax_id if vendor else invoice.vendor_tax_id,
            "address": vendor.address if vendor else invoice.vendor_address,
            "phone": vendor.phone if vendor else None,
            "email": vendor.email if vendor else None,
        },
        "line_items": [
            {
                "position": index + 1,
                "description": item.description,
                "quantity": _num(item.quantity),
                "unit_price": _num(item.unit_price),
                "line_total": _num(item.line_total),
                "tax_rate": _num(item.tax_rate),
                "sku_upc": item.product_sku,
            }
            for index, item in enumerate(_sorted_items(invoice))
        ],
        "validation": {
            "status": invoice.status,
            "review_required": invoice.status == "REVIEW_REQUIRED",
            "composite_confidence": _num(invoice.composite_confidence),
        },
        "processing": {
            "filename": document.filename if document else None,
            "source_type": document.source_type if document else None,
            "extraction_model": invoice.extraction_model,
            "processed_at": invoice.created_at.isoformat() if invoice.created_at else None,
        },
    }


# ---------------------------------------------------------------------------
# TXT — human-readable summary
# ---------------------------------------------------------------------------

_RULE = "-" * 48


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def build_txt(invoice: Invoice) -> str:
    payload = build_export_payload(invoice)
    header = payload["invoice"]
    totals = header["totals"]
    vendor = payload["vendor"]
    validation = payload["validation"]

    lines: list[str] = [
        "Vendor",
        "------",
        _fmt(vendor["name"]),
        "",
        "Invoice Number:",
        _fmt(header["invoice_number"]),
        "",
        "Invoice Date:",
        _fmt(header["invoice_date"]),
        "",
        "Due Date:",
        _fmt(header["due_date"]),
        "",
        "Currency:",
        _fmt(header["currency"]),
        "",
        _RULE,
        "",
        "Items",
        "",
    ]

    for item in payload["line_items"]:
        lines += [
            f"{item['position']}.",
            "Description:",
            _fmt(item["description"]),
            "",
            "Quantity:",
            _fmt(item["quantity"]),
            "",
            "Unit Price:",
            _fmt(item["unit_price"]),
            "",
            "Line Total:",
            _fmt(item["line_total"]),
            "",
        ]

    lines += [
        _RULE,
        "",
        "Totals",
        "",
        "Subtotal:",
        _fmt(totals["subtotal"]),
        "",
        "Tax:",
        _fmt(totals["tax"]),
        "",
        "Discount:",
        _fmt(totals["discount"]),
        "",
        "Grand Total:",
        f"{_fmt(totals['grand_total'])} {header['currency'] or ''}".strip(),
        "",
        _RULE,
        "",
        "Validation Status:",
        _fmt(validation["status"]),
        "",
        "Confidence:",
        f"{validation['composite_confidence'] * 100:.1f}%"
        if validation["composite_confidence"] is not None
        else "—",
        "",
        "Review Required:",
        "Yes" if validation["review_required"] else "No",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV — line items for accounting systems
# ---------------------------------------------------------------------------

CSV_HEADERS = ["Description", "Quantity", "Unit Price", "Line Total", "Tax Rate (%)", "UPC"]


def build_items_csv(invoice: Invoice) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for item in _sorted_items(invoice):
        writer.writerow(
            [
                item.description,
                _num(item.quantity),
                _num(item.unit_price),
                _num(item.line_total),
                _num(item.tax_rate) if item.tax_rate is not None else "",
                item.product_sku or "",
            ]
        )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDI — fixed-width positional import format
#
# Reverse-engineered from real PDI import files. Field confidence is NOT
# uniform — see docs/PDI_OPEN_QUESTIONS.md.
#
# Every field is produced by its own small, named encoder below, grouped
# CONFIRMED (verified against real PDI samples) vs. PLACEHOLDER (business
# rule not yet known — see docs/PDI_OPEN_QUESTIONS.md). Confirming a rule
# means changing exactly one function; nothing else in this module, the
# API, or the frontend needs to change.
#
# Detail record layout (70 chars, 0-indexed slices):
#   [0]      "B"                     record type
#   [1:12]   item code               11 digits — CONFIRMED (_pdi_item_code)
#   [12:37]  description             25 chars  — CONFIRMED (_pdi_description)
#   [37:57]  cost block              20 digits — CONFIRMED structure
#                                                 (_pdi_cost_block), resolved by
#                                                 live PDI experiments — see
#                                                 docs/PDI_CASE_COST_INVESTIGATION.md:
#     [37:43]  unknown product ref     6 digits  — not available to us, left zero
#                                                  (PDI product-matches on the UPC)
#     [43:49]  CASE COST in cents      6 digits  — CONFIRMED
#     [49:53]  constant "0100"         4 digits  — literal in all 908 samples
#     [53:57]  units per case          4 digits  — CONFIRMED (_pdi_units_per_case),
#                                                  sourced ONLY from a
#                                                  human-confirmed mapping
#   [57]     sign                    1 char    — CONFIRMED (_pdi_sign)
#   [58:62]  quantity                4 digits  — CONFIRMED (_pdi_quantity)
#   [62:70]  cost block tail         8 digits  — CONFIRMED as EDI SRP (retail),
#                                                 deliberately left zero
#                                                 (_pdi_cost_tail): a wholesale
#                                                 invoice prints no retail price,
#                                                 and zero leaves PDI's own
#                                                 Product Master retail intact.
#
# Header record: "AMOUNT {batch}   {date}{sign}{amount}"
#   batch  — CONFIRMED (_pdi_batch_number: the invoice's own reference number)
#   date   — CONFIRMED format, semantics open (_pdi_date, see open questions)
#   amount — CONFIRMED (_pdi_amount_cents)
#
# Sign — CONFIRMED (_pdi_sign): "+" for a normal invoice, "-" for a return
# (grand_total negative). Applied uniformly to the header and every detail
# line, matching every real sample: a file is either entirely a delivery or
# entirely a return, never mixed.
#
# Trailer record layout (38 chars) — CONFIRMED against three real PDI
# ground-truth files, every one of which ended with exactly these two
# lines:
#   [0:4]   record code             "CFUE" or "CPPT"
#   [4:29]  label                   25 chars, space-padded ("FUEL SURCHARGE",
#                                     "PREPAID SALES TAX")
#   [29]    sign
#   [30:38] amount                  8 digits, cents, magnitude only
#
# Content is a separate question from layout: neither trailer type is
# currently emitted. CPPT was previously populated from invoice.tax_amount;
# cross-file analysis showed CPPT tracks cigarette-carton volume (near
# $12.50/carton in real files) — a specific excise calculation, not a copy
# of a generic tax total — so that mapping was reverted rather than continue
# emitting a plausible-but-wrong number. CFUE (fuel surcharge) is a flat
# per-delivery constant in every real sample ($12.45) — not derived from
# invoice content, so no formatter logic can produce it without a confirmed
# business constant. See docs/PDI_DATA_CONTRACT.md §2.2-2.3.
# ---------------------------------------------------------------------------

PDI_ITEM_CODE_WIDTH = 11
PDI_DESCRIPTION_WIDTH = 25
PDI_QUANTITY_WIDTH = 4
PDI_BLOCK_A_WIDTH = 20  # PLACEHOLDER — see docs/PDI_DATA_CONTRACT.md §2.1
PDI_BLOCK_B_TAIL_WIDTH = 8  # PLACEHOLDER — see docs/PDI_DATA_CONTRACT.md §2.1
PDI_BATCH_WIDTH = 7
PDI_DATE_FORMAT = "%m%d%y"
PDI_AMOUNT_WIDTH = 9
PDI_TRAILER_LABEL_WIDTH = 25
PDI_TRAILER_AMOUNT_WIDTH = 8

_NON_DIGITS = re.compile(r"\D")


# ---------------------------------------------------------------------------
# CONFIRMED field encoders — verified against real PDI samples
# ---------------------------------------------------------------------------


def normalize_item_code(product_code: str | None) -> str | None:
    """
    Reduce a printed product code to its canonical digits: non-digits
    stripped, a 12-digit UPC-A's trailing check digit dropped, truncated
    to the PDI item-code width. Returns None when there is no usable code.

    Public because it is also the key of the units-per-case mapping table
    (app/models/product_case_mapping.py). A mapping must be found again
    from any invoice carrying the same barcode regardless of how that
    vendor printed it ("0-48500-20603-4" vs "048500206034"), which only
    holds if the lookup key and the emitted EDI code come from the same
    function — so they do.
    """
    if not product_code:
        return None
    digits = _NON_DIGITS.sub("", product_code)
    if not digits:
        return None
    if len(digits) == 12:
        digits = digits[:-1]
    return digits[:PDI_ITEM_CODE_WIDTH]


def _pdi_item_code(product_code: str | None) -> str:
    """
    Reduce an extracted product_code to PDI's 11-digit item-code field.

    Rule: a 12-digit code is treated as UPC-A and reduced by dropping its
    trailing check digit (the standard "UPC without check digit" convention
    many POS/back-office systems use) — UNVERIFIED against a matched
    ground-truth PDI file, but a documented, deterministic, industry-standard
    transformation rather than a guess. Shorter codes (e.g. a vendor item
    number) are zero-padded on the left.

    No code on file → "00000" followed by 6 spaces. This exact convention
    (not all-spaces, not all-zeros) is CONFIRMED against a real blank-code
    row in a supplied PDI ground-truth file — see the PDI compatibility
    report, Track A case 3.
    """
    digits = normalize_item_code(product_code)
    if digits is None:
        return "00000" + " " * (PDI_ITEM_CODE_WIDTH - 5)
    return digits.rjust(PDI_ITEM_CODE_WIDTH, "0")


def _pdi_description(description: str) -> str:
    """CONFIRMED: 25 chars, left-justified, space-padded/truncated."""
    return description[:PDI_DESCRIPTION_WIDTH].ljust(PDI_DESCRIPTION_WIDTH)


def _pdi_quantity(quantity: Decimal | None) -> str:
    """
    CONFIRMED: 4-digit zero-padded quantity. A quantity of 10000+ (never
    seen in any real sample) would overflow the field rather than being
    silently truncated to a wrong-but-plausible-looking number.
    """
    value = int(quantity) if quantity is not None else 0
    return str(max(value, 0)).rjust(PDI_QUANTITY_WIDTH, "0")


def _pdi_is_return(invoice: Invoice) -> bool:
    """A return/credit invoice is one whose grand total was extracted as
    negative — no new data is required to detect this, extraction and
    validation already preserve the printed sign on grand_total."""
    return invoice.grand_total is not None and invoice.grand_total < 0


def _pdi_sign(invoice: Invoice) -> str:
    """
    CONFIRMED: "+" for a normal invoice, "-" for a return/credit invoice.
    Applied uniformly to the header and every detail line — every real
    sample file was either entirely a delivery or entirely a return, never
    mixed line-by-line, so the sign is computed once per invoice.
    """
    return "-" if _pdi_is_return(invoice) else "+"


def _pdi_date(invoice: Invoice) -> str:
    """CONFIRMED format (MMDDYY). Semantics (invoice date vs. some other
    date PDI expects) unconfirmed but low-risk — see open questions."""
    return invoice.invoice_date.strftime(PDI_DATE_FORMAT) if invoice.invoice_date else "0" * 6


def _pdi_amount_cents(invoice: Invoice) -> str:
    """
    CONFIRMED: integer cents, zero-padded, magnitude only — cross-checked
    against fee amounts in supplied PDI ground-truth files. The sign
    character (_pdi_sign) carries direction; this field is always a plain
    positive digit string, for a return invoice exactly as for a normal
    one — matching every real sample, where the amount digits never
    contained a minus sign, only the dedicated sign character did.
    """
    cents = round(float(abs(invoice.grand_total)) * 100) if invoice.grand_total is not None else 0
    return f"{cents:0{PDI_AMOUNT_WIDTH}d}"


# ---------------------------------------------------------------------------
# Cost block/tail — PLACEHOLDER, reverted from a disproven calculation
#
# A prior milestone implemented these as unit_cost and unit_cost x quantity
# (in cents), per a business-provided rule. Cross-file analysis of 18 real
# accepted PDI files (docs/PDI_DATA_CONTRACT.md §2.1) disproved this: across
# 908 detail lines and 59 items observed at multiple different delivered
# quantities, neither field ever scales with quantity — both are per-product
# constants that only change between pricing periods. Decoded structure
# points to product-master data (a secondary code, a price-period value, a
# case-pack size) that does not exist anywhere in our schema or on a
# supplier invoice — not a digit-layout question, a missing-data question.
# Reverted to an honest placeholder rather than continue emitting a
# confident-looking wrong number.
# ---------------------------------------------------------------------------


# "24/12OZ", "12/14", "2/12PK" — the packs-per-case/unit-size notation
# standard on beverage and grocery invoices. Requires the slash form so a
# bare size ("20OZ", "2L") can never be read as a case pack.
_PACK_IN_DESCRIPTION = re.compile(r"(?<!\d)(\d{1,4})\s*/\s*\d")


# Forms where the leading integer does NOT settle units-per-case, because
# the second element is itself a multi-unit pack rather than a container
# size: "4/6/16OZ" (four six-packs = 4 or 24?), "3/8/16", "8/6PK". The
# number is still offered, but marked so the operator knows it is a
# reading of the package, not a fact about how the store sells it.
_AMBIGUOUS_PACK = re.compile(
    r"(?<!\d)(\d{1,4})\s*/\s*(\d+)\s*(?:/\s*\d|P(?:K|ACK)\b)", re.IGNORECASE
)

SUGGESTION_FROM_PACK_SIZE = "pack_size"
SUGGESTION_FROM_DESCRIPTION = "description"
SUGGESTION_FROM_DESCRIPTION_AMBIGUOUS = "description_ambiguous"


def pack_candidates(description: str | None) -> list[int]:
    """
    The units-per-case readings a structurally ambiguous description
    could support, smallest first. Empty when the description is not
    ambiguous.

    "BUSCH 4/6/16OZ" is four six-packs. Units per case is 4 if the store
    sells the six-pack as one item, or 24 if it breaks singles — the
    invoice cannot say which. Offering both, and prefilling neither,
    turns a one-click mistake into a deliberate answer. That mistake is
    not hypothetical: an earlier build defaulted this field to 1 whenever
    the pack size was unreadable, and PDI duly showed "Units Per Case 1"
    for every product on a real invoice.
    """
    found = _AMBIGUOUS_PACK.search(description or "")
    if not found:
        return []
    outer, inner = int(found.group(1)), int(found.group(2))
    product = outer * inner
    candidates = [outer]
    if outer < product <= MAX_UNITS_PER_CASE:
        candidates.append(product)
    return candidates


def suggest_units_per_case(
    pack_size: str | None, description: str | None = None
) -> tuple[int | None, str | None]:
    """
    A units-per-case suggestion and the source it came from.

    The source matters to the operator: a value read off a dedicated pack
    column is stronger evidence than one scraped out of a description,
    and a description like "BUSCH 4/6/16OZ CAN" does not settle the
    question at all. Returning the provenance lets the review UI say
    where a number came from instead of presenting every suggestion with
    equal authority.

    NOTHING here is ever applied automatically, whatever the source.
    """
    match = re.match(r"\s*(\d+)", pack_size or "")
    if match:
        units = int(match.group(1))
        if MIN_UNITS_PER_CASE <= units <= MAX_UNITS_PER_CASE:
            return units, SUGGESTION_FROM_PACK_SIZE
        return None, None

    found = _PACK_IN_DESCRIPTION.search(description or "")
    if not found:
        return None, None
    units = int(found.group(1))
    if not 2 <= units <= MAX_UNITS_PER_CASE:
        return None, None
    source = (
        SUGGESTION_FROM_DESCRIPTION_AMBIGUOUS
        if _AMBIGUOUS_PACK.search(description or "")
        else SUGGESTION_FROM_DESCRIPTION
    )
    return units, source


def suggested_units_per_case(
    pack_size: str | None, description: str | None = None
) -> int | None:
    """
    Best guess at units-per-case, from the printed pack descriptor when
    the vendor prints one ("24/12OZ" -> 24, "12/14" -> 12), otherwise
    from the same notation embedded in the description.

    The description fallback exists because many suppliers have no pack
    column at all — on the Balkan receipt layout every one of the seven
    lines came back with pack_size null while the description read "RB
    COCONUT 24/12OZ", so the operator was asked for seven values with no
    help on screen. It is restricted to the explicit N/M form and to
    N >= 2: a lone "1" recovered from prose like "1/2 GALLON" would be
    indistinguishable from a genuine single-unit case, and quietly
    proposing 1 for an unknown product is the exact failure this whole
    mapping table exists to prevent.

    A SUGGESTION ONLY — offered to a human for confirmation, never used
    directly for EDI generation. Returns None when nothing usable is
    printed, so callers must handle "unknown" rather than receive a
    fabricated default.
    """
    return suggest_units_per_case(pack_size, description)[0]


def _pdi_units_per_case(item: InvoiceItem, units_by_item_code: Mapping[str, int]) -> str:
    """
    Units-per-case, 4 digits — cost block bytes [16:20] (absolute [53:57]).

    CONFIRMED against live PDI: whatever lands here is displayed verbatim
    as "Units Per Case", and PDI computes Case Retail = Item Retail x this
    value. A test upload that put 1896 here produced "Units Per Case
    1,896" and "Case Retail $5,100.24" (= $2.69 x 1896) exactly.

    Sourced exclusively from the confirmed mapping passed in by the
    caller. The LLM's pack_size is deliberately NOT consulted here: it is
    a suggestion for a human to confirm, and a wrong value silently
    corrupts Case Retail in PDI. build_pdi_export() refuses to run when a
    mapping is missing, so an unmapped item can never reach this function.
    """
    code = normalize_item_code(item.product_sku)
    if code is None:
        # No product code at all: nothing to key a mapping on, and the
        # line already exports with the blank item-code convention, so PDI
        # cannot product-match it either way. 1 keeps Case Retail equal to
        # Item Retail. unmapped_item_codes() skips these lines for the
        # same reason — the gate and this function must agree, or an
        # invoice becomes permanently un-exportable.
        return "0001"
    units = units_by_item_code.get(code)
    if units is None:
        # Unreachable via build_pdi_export(), which gates on the same
        # mapping. Fail loudly rather than emit a fabricated pack size.
        raise ValueError(
            f"No confirmed units-per-case mapping for item code {code!r} "
            f"({item.description!r}). Confirm the mapping before exporting."
        )
    return str(units).rjust(4, "0")


def _pdi_cost_block(item: InvoiceItem, units_by_item_code: Mapping[str, int]) -> str:
    """
    20-digit block. CONFIRMED structure (see docs/PDI_CASE_COST_INVESTIGATION.md):

        [0:6]   unknown 6-digit product reference — not available to us,
                left zero. PDI matches products on the UPC in [1:12], which
                is confirmed working, so this does not block anything.
        [6:12]  CASE COST in cents  <- the field PDI reads for Case Cost
        [12:16] constant "0100"     — literal in all 908 ground-truth records
        [16:20] units per case

    Case Cost placement was proved by elimination plus arithmetic: live
    PDI showed [16:20] driving Units Per Case and the cost tail driving
    EDI SRP, leaving [6:12]; decoding that field across real accepted
    vendor files gives cost/retail ratios with a median of 0.59, 27 of 28
    inside a normal retail margin band, none above 1.0, and every
    cigarette line at 0.93 — the razor-thin margin that category is known
    for. Random bytes do not produce that.

    A line whose cost was never extracted raises rather than encoding
    zero. Telling PDI the goods were free is worse than refusing to
    produce a file, and unmapped_item_codes()/items_missing_cost() gate
    on the same condition so this is unreachable in normal use.
    """
    if item.unit_price is None:
        raise ValueError(
            f"No unit cost was extracted for {item.description!r}. "
            "Correct the line before exporting; a missing cost must never "
            "be encoded as 000000."
        )
    cents = round(float(abs(item.unit_price)) * 100)
    return (
        "0" * 6
        + str(cents).rjust(6, "0")[-6:]
        + "0100"
        + _pdi_units_per_case(item, units_by_item_code)
    )


def _pdi_cost_tail(item: InvoiceItem) -> str:
    """
    8 digits: [0:5] EDI SRP (retail per unit, cents) + constant "001".

    CONFIRMED as a RETAIL field, not a cost field: an earlier upload put
    the invoice's gross unit prices here and PDI displayed them verbatim
    in its "EDI SRP" column ($19.41, $56.50, $36.50, $43.50).

    Left zero deliberately. A wholesale supplier invoice does not print a
    retail price, so we have nothing truthful to put here, and sending
    zero is safe: PDI keeps its own Product Master retail (it showed the
    correct Item Retail of $2.69/$3.49/etc. on the upload where this
    field was all zeros). Sending a wholesale cost here would overwrite a
    correct retail price with a wrong one.
    """
    return "0" * PDI_BLOCK_B_TAIL_WIDTH


def _pdi_batch_number(invoice: Invoice) -> str:
    """
    CONFIRMED: the invoice's own reference number (invoice_number), as
    extracted from the document. Non-digit characters are stripped and the
    result is fit to the fixed 7-digit field — trailing digits are kept
    (right-aligned) when longer, zero-padded on the left when shorter.
    """
    batch = _NON_DIGITS.sub("", invoice.invoice_number or "")
    return (batch[-PDI_BATCH_WIDTH:] if batch else "").rjust(PDI_BATCH_WIDTH, "0")


# ---------------------------------------------------------------------------
# Trailer records — layout CONFIRMED, content partially available
# ---------------------------------------------------------------------------


def _pdi_trailer_line(code: str, label: str, cents: int, *, invoice: Invoice) -> str:
    """
    One 38-char trailer record. CONFIRMED byte layout (see module note
    above): "C" + 3-char subtype code + 25-char label + sign + 8-digit
    cents. Sign follows the same invoice-level direction as the header and
    every detail line (_pdi_sign) — never observed to differ within a
    single real sample file.
    """
    return (
        f"C{code}"
        + label[:PDI_TRAILER_LABEL_WIDTH].ljust(PDI_TRAILER_LABEL_WIDTH)
        + _pdi_sign(invoice)
        + str(cents).rjust(PDI_TRAILER_AMOUNT_WIDTH, "0")
    )


def _pdi_trailer_lines(invoice: Invoice) -> list[str]:
    """
    PLACEHOLDER — CFUE (fuel surcharge) / CPPT (prepaid sales tax) trailer
    records. Byte layout is confirmed (_pdi_trailer_line); neither is
    currently emitted:

    CPPT was previously sourced from invoice.tax_amount. Cross-file
    analysis (docs/PDI_DATA_CONTRACT.md §2.2) shows real CPPT values track
    cigarette-carton volume (near $12.50/carton), not a generic tax total —
    we don't capture carton/tax-category classification, so this can't be
    computed correctly yet. Reverted rather than emit a plausible-but-wrong
    number.

    CFUE is a flat per-delivery constant in every real sample ($12.45) —
    implementing it needs only a confirmed business constant, not new data
    capture, but that confirmation hasn't happened.

    See docs/PDI_DATA_CONTRACT.md §2.2-2.3.
    """
    return []


# ---------------------------------------------------------------------------
# Record composition
# ---------------------------------------------------------------------------


def _pdi_detail_line(
    item: InvoiceItem, *, invoice: Invoice, units_by_item_code: Mapping[str, int]
) -> str:
    return (
        "B"
        + _pdi_item_code(item.product_sku)
        + _pdi_description(item.description)
        + _pdi_cost_block(item, units_by_item_code)
        + _pdi_sign(invoice)
        + _pdi_quantity(item.quantity)
        + _pdi_cost_tail(item)
    )


def _pdi_header_line(invoice: Invoice) -> str:
    return (
        f"AMOUNT {_pdi_batch_number(invoice)}   {_pdi_date(invoice)}"
        f"{_pdi_sign(invoice)}{_pdi_amount_cents(invoice)}"
    )


# ---------------------------------------------------------------------------
# PDI export eligibility — gating, not formatting
#
# Single source of truth for whether an invoice can be exported as PDI,
# shared by the export endpoint's gate (app/api/v1/exports.py) and the
# invoice-detail API (app/api/v1/invoices.py) so the frontend and backend
# can never drift on this rule — the frontend reads the computed result,
# it never re-implements the condition.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PdiExportEligibility:
    allowed: bool
    requires_confirmation: bool
    blocked_reason: str | None = None


def unmapped_item_codes(
    invoice: Invoice, units_by_item_code: Mapping[str, int]
) -> list[str]:
    """
    Normalized item codes on this invoice with no confirmed
    units-per-case mapping, in document order without duplicates.

    Empty list means the invoice is ready to export. Items with no usable
    product code at all are excluded: they already export with the blank
    item-code convention and there is nothing to key a mapping on.
    """
    missing: list[str] = []
    for item in _sorted_items(invoice):
        code = normalize_item_code(item.product_sku)
        if code and code not in units_by_item_code and code not in missing:
            missing.append(code)
    return missing


def items_missing_cost(invoice: Invoice) -> list[str]:
    """
    Descriptions of line items with no extracted unit cost, in document
    order.

    Empty means every line can be priced. A NULL unit_price means
    extraction could not read the figure (see migration 0005) — it is not
    a free item, and encoding it as a 000000 case cost would tell PDI
    exactly that. Blocking here is the counterpart to the units-per-case
    gate: both refuse to guess a number that silently corrupts PDI.
    """
    return [
        item.description or "(no description)"
        for item in _sorted_items(invoice)
        if item.unit_price is None
    ]


def pdi_export_eligibility(
    invoice: Invoice, units_by_item_code: Mapping[str, int] | None = None
) -> PdiExportEligibility:
    """
    VALIDATED and REVIEW_REQUIRED invoices are both eligible, as long as
    there's at least one extracted line item — a PDI file with only a
    header and no detail lines isn't a usable import. REVIEW_REQUIRED
    invoices are eligible but flagged for confirmation: the underlying
    data may contain extraction inaccuracies that haven't been reviewed.

    An invoice is additionally blocked while any line item lacks a
    confirmed units-per-case mapping, so the export cannot silently
    default an unknown product's pack size. `units_by_item_code` is
    optional only so callers that genuinely have no session (unit tests
    of the status rules) can skip that check.
    """
    if not invoice.items:
        return PdiExportEligibility(
            allowed=False,
            requires_confirmation=False,
            blocked_reason="This invoice has no extracted line items to export.",
        )

    unpriced = items_missing_cost(invoice)
    if unpriced:
        count = len(unpriced)
        return PdiExportEligibility(
            allowed=False,
            requires_confirmation=False,
            blocked_reason=(
                f"{count} line item{'s' if count != 1 else ''} "
                f"{'have' if count != 1 else 'has'} no extracted unit cost "
                f"({', '.join(unpriced[:3])}"
                f"{', …' if count > 3 else ''}). Correct the invoice before "
                "exporting — a missing cost cannot be sent as zero."
            ),
        )

    if units_by_item_code is not None:
        missing = unmapped_item_codes(invoice, units_by_item_code)
        if missing:
            count = len(missing)
            return PdiExportEligibility(
                allowed=False,
                requires_confirmation=False,
                blocked_reason=(
                    f"{count} product{'s' if count != 1 else ''} "
                    f"need{'' if count != 1 else 's'} a units-per-case mapping "
                    "before this invoice can be exported."
                ),
            )

    if invoice.status == "VALIDATED":
        return PdiExportEligibility(allowed=True, requires_confirmation=False)
    return PdiExportEligibility(allowed=True, requires_confirmation=True)


def build_pdi_export(invoice: Invoice, units_by_item_code: Mapping[str, int]) -> str:
    """
    Deterministic PDI-compatible fixed-width export.

    `units_by_item_code` maps normalized item code -> confirmed
    units-per-case and MUST cover every line item; see
    unmapped_item_codes(). Passing it in (rather than querying here) keeps
    this module pure and synchronous — the caller owns the database
    session. Raises ValueError on a missing mapping rather than guessing a
    pack size, because a wrong value silently corrupts Case Retail in PDI.

    Confirmed fields (verified against real PDI samples): record structure
    (header, detail, and trailer byte layout), item code, description,
    quantity, sign (return vs. normal invoice), batch number. Still-open
    fields (cost block/tail, CPPT/CFUE trailer content) are emitted as
    documented zero-value placeholders — evidence shows these encode
    product-master data (retail price, case pack, a cigarette excise rate)
    that does not exist on a supplier invoice, so nothing is fabricated to
    look more certain than it is. See docs/PDI_DATA_CONTRACT.md and
    docs/PDI_OPEN_QUESTIONS.md for the full breakdown before relying on
    this for a live import.

    Line endings are CRLF ("\\r\\n"), confirmed against every real ground
    truth file (both formats) — a plain "\\n" file was rejected by a real
    PDI import attempt with a generic "file format was not right one"
    error, consistent with a legacy fixed-width importer that expects
    DOS/Windows-style line endings.
    """
    lines = [_pdi_header_line(invoice)]
    lines.extend(
        _pdi_detail_line(item, invoice=invoice, units_by_item_code=units_by_item_code)
        for item in _sorted_items(invoice)
    )
    lines.extend(_pdi_trailer_lines(invoice))
    text = "\r\n".join(lines) + "\r\n"

    # Structural self-check before the file leaves this function. The
    # audit decodes the finished bytes rather than re-running the
    # encoders, so a field that changed width — the failure mode that
    # silently shifts every later field and is invisible in a diff —
    # cannot escape as a downloadable file. Only structural invariants
    # (lengths, CRLF, digit fields, the "0100" marker) are enforced here;
    # content comparisons are reported by the audit, not raised, and the
    # header/detail balance is deliberately not judged at all (Q7).
    from app.services.pdi_audit import audit_pdi_export  # local: avoids an import cycle

    failures = audit_pdi_export(text).structural_failures
    if failures:
        raise ValueError(
            "Generated PDI file violates the confirmed byte contract: "
            + "; ".join(f"{f.name} (expected {f.expected}, got {f.actual})" for f in failures)
        )
    return text
