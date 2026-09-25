"""
Shadow EDI comparison — app/services/product_master/shadow_edi.py

Answers one question, read-only: *what would this invoice's EDI look like
if the Product Master were authoritative?*

It does not replace anything. `build_pdi_export` is reused unchanged, and
the only thing that differs between the two runs is where the master-data
inputs come from:

    CURRENT_LEGACY_EDI    units-per-case from product_case_mappings
    PRODUCT_MASTER_SHADOW units-accounted-for from APPROVED master_commercial_mappings

Financial values are never touched. Quantity, AMOUNT and the invoice
totals come from the invoice's own persisted data in both runs, so a
difference in those is by construction impossible — and the comparison
asserts it rather than assuming it.

Only APPROVED commercial mappings feed the shadow. A candidate still
awaiting review is not master data yet, and a line whose item code has no
approved mapping makes the whole invoice SHADOW_BLOCKED rather than
silently falling back to the legacy value, which would make the comparison
meaningless.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.services.export_service import build_pdi_export, normalize_item_code

# What a difference means. Never "better" or "worse" — the shadow is not a
# proposed improvement, it is a statement of what would change.
SHADOW_MATCH = "SHADOW_MATCH"
SHADOW_DIFFERENCE_EXPECTED = "SHADOW_DIFFERENCE_EXPECTED"
SHADOW_BLOCKED = "SHADOW_BLOCKED"
SHADOW_UNSAFE_DIFFERENCE = "SHADOW_UNSAFE_DIFFERENCE"

# A description difference is expected where the master holds sanctioned
# canonical wording; PDI product-matches on the UPC, so the description is
# displayed, not identifying.
EXPECTED_DIFFERENCE_KINDS = frozenset({"description"})
# These change what PDI computes or which product it touches.
UNSAFE_DIFFERENCE_KINDS = frozenset({
    "item_code", "units_accounted_for", "case_cost", "quantity", "amount", "record_count",
})

# The named difference vocabulary. One code per field so a result names the
# specific risk rather than a generic "unsafe".
DIFF_NONE = "NONE"
DIFF_EXPECTED_DESCRIPTION = "EXPECTED_DESCRIPTION_DIFFERENCE"
DIFF_EXPECTED_MASTER_DATA = "EXPECTED_MASTER_DATA_DIFFERENCE"
DIFF_UNSAFE_IDENTITY = "UNSAFE_IDENTITY_DIFFERENCE"
DIFF_UNSAFE_MULTIPLIER = "UNSAFE_MULTIPLIER_DIFFERENCE"
DIFF_UNSAFE_COST = "UNSAFE_COST_DIFFERENCE"
DIFF_UNSAFE_QUANTITY = "UNSAFE_QUANTITY_DIFFERENCE"
DIFF_UNSAFE_AMOUNT = "UNSAFE_AMOUNT_DIFFERENCE"
DIFF_RECORD_COUNT = "RECORD_COUNT_DIFFERENCE"
DIFF_BLOCKED = "BLOCKED_MISSING_MASTER_MAPPING"

_FIELD_TO_DIFFERENCE = {
    "description": DIFF_EXPECTED_DESCRIPTION,
    "item_code": DIFF_UNSAFE_IDENTITY,
    "units_accounted_for": DIFF_UNSAFE_MULTIPLIER,
    "case_cost": DIFF_UNSAFE_COST,
    "quantity": DIFF_UNSAFE_QUANTITY,
    "amount": DIFF_UNSAFE_AMOUNT,
    "record_count": DIFF_RECORD_COUNT,
}


def difference_codes(differences: list[dict], blockers: list[str]) -> list[str]:
    """
    The named codes for one comparison, deduplicated and ordered.

    A description difference is never reported as unsafe merely for
    differing; item code, multiplier, cost, quantity, amount and record
    count always are.
    """
    if blockers:
        return [DIFF_BLOCKED]
    if not differences:
        return [DIFF_NONE]
    codes: list[str] = []
    for difference in differences:
        code = _FIELD_TO_DIFFERENCE.get(difference["field"], DIFF_EXPECTED_MASTER_DATA)
        if code not in codes:
            codes.append(code)
    return codes


@dataclass
class ShadowComparison:
    invoice_number: str | None
    classification: str
    legacy_status: str
    shadow_status: str
    blockers: list[str] = field(default_factory=list)
    legacy_line_count: int = 0
    shadow_line_count: int = 0
    legacy_b_record_count: int = 0
    shadow_b_record_count: int = 0
    legacy_sha256: str | None = None
    shadow_sha256: str | None = None
    legacy_bytes: int = 0
    shadow_bytes: int = 0
    byte_equal: bool = False
    crlf_preserved: bool = True
    differences: list[dict] = field(default_factory=list)
    difference_codes: list[str] = field(default_factory=list)
    financial_invariant: bool = True


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _b_records(content: str) -> list[str]:
    return [line for line in content.split("\r\n") if line.startswith("B")]


def approved_units_for_invoice(
    invoice, approved_by_code: dict[str, int],
) -> tuple[dict[str, int], list[str]]:
    """
    The master-sourced mapping for one invoice, and what it could not cover.

    A missing approved mapping is reported, never substituted.
    """
    units: dict[str, int] = {}
    missing: list[str] = []
    for item in invoice.items:
        code = normalize_item_code(item.product_sku)
        if code is None:
            continue
        if code in approved_by_code:
            units[code] = approved_by_code[code]
        elif code not in missing:
            missing.append(code)
    return units, missing


def compare_line_fields(legacy_line: str, shadow_line: str) -> list[dict]:
    """
    Field-level differences between two B records, by the confirmed layout.

    Positions come from the B-record map in export_service; this reads
    them, it does not redefine them.
    """
    slices = {
        "item_code": (1, 12),
        "description": (12, 37),
        "case_cost": (43, 49),
        "units_accounted_for": (53, 57),
        "quantity": (58, 62),
    }
    differences: list[dict] = []
    for name, (start, end) in slices.items():
        legacy_value = legacy_line[start:end]
        shadow_value = shadow_line[start:end]
        if legacy_value != shadow_value:
            differences.append({
                "field": name,
                "legacy": legacy_value.strip(),
                "shadow": shadow_value.strip(),
                "kind": "expected" if name in EXPECTED_DIFFERENCE_KINDS else "unsafe",
            })
    return differences


def classify(differences: list[dict], blockers: list[str], byte_equal: bool) -> str:
    if blockers:
        return SHADOW_BLOCKED
    if byte_equal and not differences:
        return SHADOW_MATCH
    if any(d["kind"] == "unsafe" for d in differences):
        return SHADOW_UNSAFE_DIFFERENCE
    return SHADOW_DIFFERENCE_EXPECTED


def compare_invoice(
    invoice, legacy_units: dict[str, int], approved_by_code: dict[str, int],
) -> ShadowComparison:
    """
    Build both exports for one invoice and describe every difference.

    Neither export is persisted and the invoice is not modified.
    """
    number = invoice.invoice_number
    comparison = ShadowComparison(
        invoice_number=number, classification=SHADOW_BLOCKED,
        legacy_status="not built", shadow_status="not built",
    )

    try:
        legacy_content = build_pdi_export(invoice, legacy_units)
        comparison.legacy_status = "built"
    except Exception as exc:  # noqa: BLE001 — a legacy failure is itself the finding
        comparison.legacy_status = f"failed: {type(exc).__name__}"
        comparison.blockers.append(f"legacy export failed: {exc}")
        return comparison

    shadow_units, missing = approved_units_for_invoice(invoice, approved_by_code)
    if missing:
        comparison.shadow_status = "blocked"
        comparison.blockers.append(
            f"{len(missing)} item code(s) have no APPROVED Product Master commercial "
            f"mapping: {', '.join(missing[:5])}"
        )

    comparison.legacy_sha256 = _sha(legacy_content)
    comparison.legacy_bytes = len(legacy_content.encode("utf-8"))
    comparison.legacy_line_count = len(legacy_content.split("\r\n"))
    comparison.legacy_b_record_count = len(_b_records(legacy_content))

    if comparison.blockers:
        comparison.difference_codes = difference_codes([], comparison.blockers)
        return comparison

    try:
        shadow_content = build_pdi_export(invoice, shadow_units)
        comparison.shadow_status = "built"
    except Exception as exc:  # noqa: BLE001
        comparison.shadow_status = f"failed: {type(exc).__name__}"
        comparison.blockers.append(f"shadow export failed: {exc}")
        return comparison

    comparison.shadow_sha256 = _sha(shadow_content)
    comparison.shadow_bytes = len(shadow_content.encode("utf-8"))
    comparison.shadow_line_count = len(shadow_content.split("\r\n"))
    comparison.shadow_b_record_count = len(_b_records(shadow_content))
    comparison.byte_equal = legacy_content == shadow_content
    comparison.crlf_preserved = "\r\n" in shadow_content or comparison.shadow_line_count <= 1

    if comparison.legacy_line_count != comparison.shadow_line_count:
        comparison.differences.append({
            "field": "record_count", "legacy": comparison.legacy_line_count,
            "shadow": comparison.shadow_line_count, "kind": "unsafe",
        })

    legacy_lines, shadow_lines = _b_records(legacy_content), _b_records(shadow_content)
    for index, (legacy_line, shadow_line) in enumerate(zip(legacy_lines, shadow_lines, strict=False)):
        for difference in compare_line_fields(legacy_line, shadow_line):
            comparison.differences.append({**difference, "b_record": index})

    # The header carries AMOUNT. It is built from invoice data in both runs,
    # so a difference would mean the shadow path reached somewhere it must not.
    legacy_header = legacy_content.split("\r\n")[0]
    shadow_header = shadow_content.split("\r\n")[0]
    comparison.financial_invariant = legacy_header == shadow_header
    if not comparison.financial_invariant:
        comparison.differences.append({
            "field": "amount", "legacy": legacy_header.strip(),
            "shadow": shadow_header.strip(), "kind": "unsafe",
        })

    comparison.classification = classify(
        comparison.differences, comparison.blockers, comparison.byte_equal,
    )
    comparison.difference_codes = difference_codes(
        comparison.differences, comparison.blockers)
    return comparison
