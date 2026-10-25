"""
Pepsi identifier characterization — tests/test_pepsi_identifier_characterization.py

Standalone checks of ONE PepsiCo Beverages Company invoice (2026-10-22), transcribed from four
overlapping photos into tests/fixtures/supplier_characterization/pepsico_beverages_20261022.json
(deliberately outside tests/fixtures/identifier/, whose Phase 1 contents are frozen by a manifest hash).

These tests assert only what the document shows: its layout, its arithmetic and the printed
identifiers. They deliberately do NOT establish deposit eligibility of individual products, the
completions of the three truncated UPCs, whether a UPC is unit- or case-level, or the store's
identity. Nothing here imports the identifier resolver or its templates: no Pepsi template exists,
and this vendor is characterization-only. The transcription is not yet independently human-verified.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "supplier_characterization" / "pepsico_beverages_20261022.json"
DOC = json.loads(FIXTURE.read_text())
GROUPS = DOC["groups"]
LINES = [line for group in GROUPS for line in group["lines"]]
COMPLETE = re.compile(r"\d-\d{5}-\d{5}-\d")


def money(value: str) -> Decimal:
    return Decimal(value)


def upc_a_check_digit(first_eleven: str) -> str:
    """GTIN-12 check digit, computed here only (never by production code)."""
    total = sum(int(ch) * (3 if i % 2 == 0 else 1) for i, ch in enumerate(first_eleven))
    return str((10 - total % 10) % 10)


def pack_size(heading: str) -> int:
    return int(re.search(r"1/\s?(\d+)", heading).group(1))


# ---------------------------------------------------------------------------
# 1. Structure
# ---------------------------------------------------------------------------


class TestStructure:

    def test_one_invoice_of_fifteen_package_groups_and_sixty_one_lines(self):
        assert DOC["schema"] == "pepsi-characterization-v1"
        assert len(GROUPS) == 15
        assert [line["n"] for line in LINES] == list(range(1, 62))      # each line exactly once, in printed order

    def test_every_heading_carries_the_pack_size_it_records(self):
        for group in GROUPS:
            assert pack_size(group["heading"]) == group["pack_size_printed"], group["heading"]

    def test_the_transcription_is_not_yet_human_verified(self):
        assert DOC["transcription"]["human_verified"] is False
        assert DOC["transcription"]["verified_by"] is None


# ---------------------------------------------------------------------------
# 2. Arithmetic printed on the invoice
# ---------------------------------------------------------------------------


class TestArithmetic:

    def test_amount_is_cases_times_net_and_units_are_cases_times_the_pack_size(self):
        for group in GROUPS:
            for line in group["lines"]:
                assert money(line["amount"]) == line["cases"] * money(line["net"]), line["n"]
                assert line["units"] == line["cases"] * group["pack_size_printed"], line["n"]

    def test_every_group_subtotal_reconciles_with_its_lines(self):
        for group in GROUPS:
            sub = group["subtotal"]
            assert sub["cases"] == sum(line["cases"] for line in group["lines"]), group["heading"]
            assert sub["units"] == sum(line["units"] for line in group["lines"]), group["heading"]
            assert money(sub["amount"]) == sum(money(line["amount"]) for line in group["lines"]), group["heading"]

    def test_the_sales_summary_reconciles(self):
        totals = DOC["printed_totals"]
        assert totals["cases"] == sum(g["subtotal"]["cases"] for g in GROUPS) == 68
        assert totals["total_units"] == sum(g["subtotal"]["units"] for g in GROUPS) == 958
        assert money(totals["sales"]) == sum(money(g["subtotal"]["amount"]) for g in GROUPS) == Decimal("1821.18")

    def test_state_local_charges_and_amount_due_reconcile(self):
        charges = DOC["state_local_charges"]
        assert sum(money(c["amount"]) for c in charges["by_group"]) == money(charges["subtotal"]) == Decimal("23.30")
        assert money(DOC["printed_totals"]["state_local_charges"]) == money(charges["subtotal"])
        assert money(DOC["printed_totals"]["sales"]) + money(charges["subtotal"]) == money(DOC["printed_totals"]["amount_due"])


# ---------------------------------------------------------------------------
# 3. Deposits: printed per package group; product eligibility is NOT asserted
# ---------------------------------------------------------------------------


class TestDepositsAsPrinted:

    def test_charges_are_printed_per_package_group_for_existing_groups_only(self):
        headings = {g["heading"] for g in GROUPS}
        charged = [c["heading"] for c in DOC["state_local_charges"]["by_group"]]
        assert len(charged) == len(set(charged)) == 8
        assert set(charged) <= headings

    def test_group_charges_compared_with_group_units_is_recorded_not_interpreted(self):
        """Arithmetic only: which items carry a deposit is not printed, so it is not asserted."""
        rate = money(DOC["state_local_charges"]["rate"])
        units = {g["heading"]: g["subtotal"]["units"] for g in GROUPS}
        equal = {c["heading"] for c in DOC["state_local_charges"]["by_group"] if money(c["amount"]) == rate * units[c["heading"]]}
        differs = {c["heading"] for c in DOC["state_local_charges"]["by_group"]} - equal
        assert len(equal) == 7
        assert differs == {"1L PL 1/15S"}
        assert "not established" in DOC["state_local_charges"]["interpretation_note"]


# ---------------------------------------------------------------------------
# 4. Printed identifiers
# ---------------------------------------------------------------------------


class TestPrintedUPCs:

    def test_complete_upcs_use_the_printed_1_5_5_1_format_and_pass_the_check_digit(self):
        complete = [line for line in LINES if line["upc_status"] == "COMPLETE"]
        assert len(complete) == 58
        for line in complete:
            assert COMPLETE.fullmatch(line["upc_raw"]), line["n"]
            digits = line["upc_raw"].replace("-", "")
            assert upc_a_check_digit(digits[:11]) == digits[11], line["n"]   # valid is not proof of identity

    def test_truncated_upcs_keep_their_printed_text_and_have_no_complete_value(self):
        truncated = {line["n"]: line for line in LINES if line["upc_status"] != "COMPLETE"}
        assert set(truncated) == {5, 21, 23}
        assert {n: (line["upc_raw"], line["left_margin_text"]) for n, line in truncated.items()} == {
            5: ("8-10030-51002-", "4"), 21: ("0-12000-1263", "5-2"), 23: ("0-12000-1803", "2-")}
        for line in truncated.values():
            assert line["upc_status"] == "INCOMPLETE_TRUNCATED"
            assert not COMPLETE.fullmatch(line["upc_raw"])
            assert len(line["upc_raw"].replace("-", "")) < 12

    def test_only_truncated_rows_record_left_margin_text(self):
        assert {line["n"] for line in LINES if line["left_margin_text"]} == {5, 21, 23}

    def test_proposed_completions_stay_unverified_and_are_never_line_values(self):
        hypotheses = DOC["unverified_hypotheses"]
        assert {h["line"] for h in hypotheses} == {5, 21, 23}
        raw_values = {line["upc_raw"].replace("-", "") for line in LINES}
        for h in hypotheses:
            assert h["status"] == "UNVERIFIED"
            assert h["candidate"] not in raw_values
            assert all("not independent proof" in c for c in h["corroboration"])

    def test_no_separate_supplier_item_number_is_recorded(self):
        assert DOC["columns_as_printed"] == ["Description UPC", "Whlsl", "Cases", "Units", "Net", "Amount"]
        for line in LINES:
            assert not {"item", "supplier_item_id", "product_code"} & set(line)


# ---------------------------------------------------------------------------
# 5. Evidence: photos, overlaps and print defects
# ---------------------------------------------------------------------------


class TestPhotoEvidence:

    def test_four_photo_hashes_in_top_to_bottom_order(self):
        photos = DOC["source_photos"]
        assert [p["file"] for p in sorted(photos, key=lambda p: p["order"])] == ["34.jpg", "33.jpg", "32.jpg", "31.jpg"]
        assert all(re.fullmatch(r"[0-9a-f]{64}", p["sha256"]) for p in photos)

    def test_overlapping_photos_cover_every_line_and_lines_name_only_covering_photos(self):
        ranges = {p["file"].removesuffix(".jpg"): range(p["lines"][0], p["lines"][1] + 1) for p in DOC["source_photos"]}
        covered = set().union(*ranges.values())
        assert covered == set(range(1, 62))
        for line in LINES:
            assert line["photos"], line["n"]
            assert all(line["n"] in ranges[p] for p in line["photos"]), line["n"]

    def test_print_defects_are_recorded_not_corrected(self):
        flagged = {line["n"] for line in LINES if line["glyph_flags"]}
        assert {5, 21, 23} <= flagged
        twenty_eight = next(g for g in GROUPS if g["heading"] == "28OZ PL 1/15")
        assert any("partly printed" in flag for flag in twenty_eight["subtotal"]["glyph_flags"])


# ---------------------------------------------------------------------------
# 6. Privacy and isolation
# ---------------------------------------------------------------------------

FORBIDDEN_KEYS = {"customer_number", "customer", "tax_id", "cust_rep", "rep", "route", "load_no", "nrid", "cid",
                  "address", "street", "signature", "received_by", "phone", "contact"}
LONG_NUMBER_ALLOWED_KEYS = {"upc_raw", "sha256", "candidate"}


def _walk(node, key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, k)
            yield ("key", k, None)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)
    else:
        yield ("value", key, node)


class TestPrivacyAndIsolation:

    def test_no_customer_or_route_details_are_recorded(self):
        keys = {k for kind, k, _ in _walk(DOC) if kind == "key"}
        assert not keys & FORBIDDEN_KEYS
        assert DOC["invoice_number"] is None
        assert DOC["receiving_store"]["status"] == "UNRESOLVED"

    def test_no_long_numbers_outside_identifier_and_hash_fields(self):
        for kind, key, value in _walk(DOC):
            if kind == "value" and isinstance(value, str) and key not in LONG_NUMBER_ALLOWED_KEYS:
                assert not re.search(r"\d{7,}", value), key

    def test_no_pepsi_identifier_template_exists(self):
        template_dir = ROOT / "app" / "services" / "product_master" / "identifier_template_data"
        if template_dir.is_dir():
            assert not [p.name for p in template_dir.glob("*.json") if "pepsi" in p.read_text().lower()]

    def test_the_eight_gold_fixtures_are_untouched_by_this_characterization(self):
        gold = ROOT / "tests" / "fixtures" / "identifier" / "invoices"
        if gold.is_dir():
            assert len(list(gold.glob("*.json"))) == 8
            assert not (gold / FIXTURE.name).exists()
