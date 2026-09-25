"""
tests/test_units_per_case_conflicts.py — LOCAL OFFLINE TESTS for the
cross-store Units/Case investigation (scripts/investigate_units_per_case_conflicts.py).

The investigation decides between four outcomes for a product whose stores
disagree about units-per-case: one product with a legitimate store
difference, two identities confused for one, a mapping that is simply
wrong, or not enough evidence to say. These tests pin how each verdict is
reached, and that "not enough evidence" is a real answer rather than a
default to the loudest number.

The substantive fact being defended: a case whose unit cost equals its
case price contains one sellable unit, so `C-18` in a description
describes eighteen cans in the pack, not eighteen sellable units.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.investigate_units_per_case_conflicts import (  # noqa: E402
    assess,
    reconstructed_upc,
    scientific_form_of,
)

MICHELOB = "01820096721"
MICHELOB_UPC = "018200967214"
TWISTED = "08769200057"
TWISTED_UPC = "087692000570"


def database(*, mappings, invoice_lines=(), identifiers=(), proposals=()) -> dict:
    return {
        "available": True,
        "case_mappings": list(mappings),
        "invoice_lines": list(invoice_lines),
        "identifiers": list(identifiers),
        "identities": [],
        "proposals": list(proposals),
    }


def mapping(code, store, units, *, status="confirmed", source="APPROVED") -> dict:
    return {"item_code": code, "store_id": "abc", "store": store,
            "store_identity_status": status, "units_per_case": units,
            "source": source, "description": "C-18 12OZ"}


def invoice_line(sku, price, number="450033") -> dict:
    return {"product_sku": sku, "description": "MICHELOB ULTRA C-18 12OZ", "quantity": 3.0,
            "unit_price": price, "pack_size": "C-18 12OZ", "invoice_number": number,
            "store": "Apple Foods II", "store_identity_status": "confirmed",
            "document": "IMG_6567.jpg"}


def workbook_with(code, *entries) -> dict:
    return {code: [{"sheet": sheet, "row": row, "cells": cells,
                    "pack_divisor_formulas": divisors}
                   for sheet, row, cells, divisors in entries]}


class TestReconstruction:
    """The two 11-digit populations converge on one UPC for these products."""

    def test_the_scan_code_reconstructs_to_the_invoice_sku(self):
        assert reconstructed_upc(MICHELOB) == MICHELOB_UPC
        assert reconstructed_upc(TWISTED) == TWISTED_UPC

    def test_the_distributor_float_form_prepends_back_to_the_same_upc(self):
        # Monarch stores this UPC as 8.769200057E10 -> 87692000570.
        assert scientific_form_of(TWISTED_UPC) == "87692000570"
        assert "0" + scientific_form_of(TWISTED_UPC) == TWISTED_UPC

    def test_both_populations_converge_on_one_identity(self):
        # Scan code loses the check digit; the float form loses the leading
        # zero. Repaired, they are the same product.
        from_scan_code = reconstructed_upc(TWISTED)
        from_float = "0" + scientific_form_of(TWISTED_UPC)
        assert from_scan_code == from_float == TWISTED_UPC


class TestVerdicts:
    def test_a_value_contradicted_by_the_distributor_is_an_incorrect_mapping(self):
        finding = assess(
            MICHELOB,
            database(
                mappings=[mapping(MICHELOB, "Apple Foods II", 18),
                          mapping(MICHELOB, "unresolved:a07b83b2", 1, status="unresolved")],
                invoice_lines=[invoice_line(MICHELOB_UPC, 17.7)],
            ),
            # Sheet1's items/case column sits at index 7.
            workbook_with(MICHELOB, ("Sheet1", 60,
                                     ["MICHELOB ULTRA", "18/12 CAN", "241.0", MICHELOB_UPC,
                                      "16.3", "16.7", "0.4", "1.0", "16.7"], {})),
        )
        assert finding["verdict"] == "INCORRECT_EXISTING_MAPPING"
        assert finding["units_per_case_supported_by_reference"] == ["1"]
        assert finding["units_per_case_contradicted_by_reference"] == ["18"]

    def test_the_unit_cost_divisor_counts_as_distributor_pack_evidence(self):
        finding = assess(
            TWISTED,
            database(
                mappings=[mapping(TWISTED, "Apple Foods II", 18),
                          mapping(TWISTED, "unresolved:a07b83b2", 1, status="unresolved")],
                invoice_lines=[invoice_line(TWISTED_UPC, 23.5)],
            ),
            workbook_with(TWISTED, ("Monarch Package", 190, ["..."], {"L190": "1"})),
        )
        assert finding["verdict"] == "INCORRECT_EXISTING_MAPPING"
        assert any("divides by 1" in e["evidence"] for e in finding["distributor_pack_evidence"])

    def test_agreement_across_stores_is_not_reported_as_a_conflict(self):
        finding = assess(
            MICHELOB,
            database(mappings=[mapping(MICHELOB, "Apple Foods II", 1),
                               mapping(MICHELOB, "RCM", 1)]),
            workbook_with(MICHELOB, ("Monarch Package", 190, ["..."], {"L190": "1"})),
        )
        assert finding["verdict"] == "NO_CONFLICT"

    def test_a_conflict_with_no_reference_evidence_is_not_decided(self):
        finding = assess(
            MICHELOB,
            database(mappings=[mapping(MICHELOB, "Apple Foods II", 18),
                               mapping(MICHELOB, "RCM", 1)]),
            {},
        )
        assert finding["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert finding["units_per_case_supported_by_reference"] == []
        # Neither number may be presented as the answer.
        assert finding["units_per_case_contradicted_by_reference"] == []

    def test_every_existing_value_being_supported_is_a_legitimate_store_difference(self):
        finding = assess(
            MICHELOB,
            database(mappings=[mapping(MICHELOB, "Apple Foods II", 18),
                               mapping(MICHELOB, "RCM", 1)]),
            workbook_with(
                MICHELOB,
                ("Monarch Package", 190, ["..."], {"L190": "1"}),
                ("Monarch Package", 191, ["..."], {"L191": "18"}),
            ),
        )
        assert finding["verdict"] == "LEGITIMATE_STORE_SPECIFIC"
        assert finding["units_per_case_contradicted_by_reference"] == []


class TestIdentityLevel:
    def test_a_product_present_on_invoices_is_store_level_not_identity_level(self):
        finding = assess(
            MICHELOB,
            database(mappings=[mapping(MICHELOB, "Apple Foods II", 18)],
                     invoice_lines=[invoice_line(MICHELOB_UPC, 17.7)]),
            {},
        )
        assert finding["identity"]["same_canonical_product"] is True
        assert finding["identity"]["confirmed_by_invoice_sku"] is True
        assert finding["difference_level"].startswith("store/commercial-data-level")

    def test_the_app_identifier_record_alone_can_confirm_one_identity(self):
        finding = assess(
            MICHELOB,
            database(mappings=[mapping(MICHELOB, "Apple Foods II", 18)],
                     identifiers=[{"item_code": MICHELOB, "kind": "retail_upc_raw",
                                   "value": MICHELOB_UPC, "distributor": None}]),
            {},
        )
        assert finding["identity"]["confirmed_by_app_identifier_record"] is True

    def test_a_spaced_identifier_form_still_confirms_the_same_identity(self):
        finding = assess(
            MICHELOB,
            database(mappings=[mapping(MICHELOB, "Apple Foods II", 18)],
                     identifiers=[{"item_code": MICHELOB, "kind": "retail_upc_raw",
                                   "value": "01820096721 4", "distributor": None}]),
            {},
        )
        assert finding["identity"]["confirmed_by_app_identifier_record"] is True

    def test_without_any_corroboration_the_difference_is_flagged_identity_level(self):
        finding = assess(
            MICHELOB, database(mappings=[mapping(MICHELOB, "Apple Foods II", 18)]), {},
        )
        assert finding["identity"]["same_canonical_product"] is False
        assert finding["difference_level"].startswith("identity-level")


class TestCostIdentityEvidence:
    def test_a_reference_cost_equal_to_the_case_price_is_recorded_as_evidence(self):
        finding = assess(
            MICHELOB,
            database(
                mappings=[mapping(MICHELOB, "Apple Foods II", 18)],
                invoice_lines=[invoice_line(MICHELOB_UPC, 17.7)],
                proposals=[{"item_code": MICHELOB, "store_id": "x", "proposed_value": "1",
                            "source": "operator_entered", "status": "APPROVED", "reason": "",
                            "evidence": {"reference_avg_cost": 17.7}}],
            ),
            {},
        )
        assert len(finding["cost_identity_evidence"]) == 1
        assert "one case = one selling unit" in finding["cost_identity_evidence"][0]["implication"]

    def test_a_reference_cost_that_differs_is_not_claimed_as_evidence(self):
        finding = assess(
            MICHELOB,
            database(
                mappings=[mapping(MICHELOB, "Apple Foods II", 18)],
                invoice_lines=[invoice_line(MICHELOB_UPC, 17.7)],
                proposals=[{"item_code": MICHELOB, "store_id": "x", "proposed_value": "18",
                            "source": "document_derived", "status": "APPROVED", "reason": "",
                            "evidence": {"reference_avg_cost": 0.98}}],
            ),
            {},
        )
        assert finding["cost_identity_evidence"] == []


class TestNothingIsChanged:
    def test_assess_does_not_mutate_the_evidence_it_is_given(self):
        payload = database(
            mappings=[mapping(MICHELOB, "Apple Foods II", 18)],
            invoice_lines=[invoice_line(MICHELOB_UPC, 17.7)],
        )
        before = repr(payload)
        assess(MICHELOB, payload, workbook_with(MICHELOB, ("Sheet1", 60, ["x"], {"L60": "1"})))
        assert repr(payload) == before

    @pytest.mark.parametrize("code", [MICHELOB, TWISTED])
    def test_the_existing_units_values_are_reported_verbatim(self, code):
        finding = assess(
            code,
            database(mappings=[mapping(code, "Apple Foods II", 18),
                               mapping(code, "RCM", 1)]),
            {},
        )
        # Both survive; the investigation never drops the value it doubts.
        assert finding["existing_units_per_case_values"] == [1, 18]
