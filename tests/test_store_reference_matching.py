"""
tests/test_store_reference_matching.py — exact UPC matching and the
reference-derived units-per-case suggestion.

Two rules are load-bearing here and both are pinned:

  1. Matching is EXACT UPC only. Description similarity is never used to
     identify a product: in this store's own export 666 descriptions map
     to more than one scan code, and across 35 UPC-verified pairs the
     median token overlap between invoice and reference wording was 0.25.

  2. A reference-derived value is a SUGGESTION. It never becomes a
     confirmed mapping on its own, and a confirmed mapping always wins.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.services.case_mapping_service import (
    SUGGESTION_FROM_DATABASE,
    build_case_mapping_status,
)
from app.services.store_reference_service import (
    EVIDENCE_RATIO,
    MAX_PACK_RELATIVE_ERROR,
    ReferenceMatch,
    UnitsEvidence,
    derive_units_per_case,
)

BUSCH = "01820000063"
BEATBOX = "85005919539"


def make_invoice(*items: InvoiceItem) -> Invoice:
    invoice = Invoice(
        id=uuid.uuid4(), document_id=uuid.uuid4(), invoice_number="228245",
        currency="USD", grand_total=Decimal("2058.02"), status="REVIEW_REQUIRED",
    )
    invoice.items = list(items)
    return invoice


def make_item(sku, description, unit_price="18.75", pack_size=None, sort_order=0):
    return InvoiceItem(
        description=description, product_sku=sku, pack_size=pack_size,
        quantity=Decimal("1"),
        unit_price=None if unit_price is None else Decimal(unit_price),
        line_total=Decimal("0"), sort_order=sort_order,
    )


def match(code, avg_cost, invoice_cost, description="ref name"):
    candidate, ratio = derive_units_per_case(
        None if invoice_cost is None else Decimal(invoice_cost),
        None if avg_cost is None else Decimal(avg_cost),
    )
    evidence = (
        UnitsEvidence(candidate, EVIDENCE_RATIO, "Item_Sales_Summary", None, None,
                      {"avg_cost": avg_cost, "ratio": ratio})
        if candidate is not None else None
    )
    return ReferenceMatch(
        item_code=code, reference_description=description,
        avg_cost=None if avg_cost is None else Decimal(avg_cost),
        avg_price=None, units_per_case_candidate=candidate, candidate_ratio=ratio,
        best_evidence=evidence, all_evidence=(evidence,) if evidence else (),
    )


class TestDerivingUnitsPerCase:
    def test_real_products_resolve_exactly(self):
        # Each pair is an invoice net case cost and the store's own
        # per-unit cost for the same UPC.
        for invoice_cost, avg_cost, expected in [
            ("18.75", "4.6875", 4),      # BUSCH 4/6/16OZ — an ambiguous pack
            ("26.45", "6.6125", 4),      # BUD LT 4/6/16OZ — ambiguous
            ("25.15", "8.3833", 3),      # BUD LT 3/8 — ambiguous
            ("34.50", "2.8750", 12),     # BEATBOX — PDI received 1 for this
            ("15.75", "15.7500", 1),     # BUD 18 PACK — an 18-pack IS one unit
            ("16.70", "16.7000", 1),     # ULTRA 18 PACK
            ("20.30", "1.6917", 12),     # LAB 12/24
            ("18.65", "1.2433", 15),     # NAT DADDY 15/25OZ
        ]:
            candidate, _ = derive_units_per_case(Decimal(invoice_cost), Decimal(avg_cost))
            assert candidate == expected, (invoice_cost, avg_cost)

    def test_a_drifted_average_still_resolves(self):
        # avg_cost is a period-weighted average, so it moves when a
        # product's cost changed mid-period. Snapping to a plausible case
        # pack recovers the right answer anyway.
        assert derive_units_per_case(Decimal("50.20"), Decimal("2.0291"))[0] == 24
        assert derive_units_per_case(Decimal("33.11"), Decimal("2.9258"))[0] == 12

    def test_no_candidate_without_a_cost_on_both_sides(self):
        assert derive_units_per_case(Decimal("18.75"), None) == (None, None)
        assert derive_units_per_case(None, Decimal("4.6875")) == (None, None)

    def test_a_zero_cost_never_produces_a_candidate(self):
        # Guards against a division by zero and against treating
        # "unknown" as free.
        assert derive_units_per_case(Decimal("18.75"), Decimal("0")) == (None, None)
        assert derive_units_per_case(Decimal("0"), Decimal("4.6875")) == (None, None)

    def test_a_ratio_between_plausible_packs_stays_silent(self):
        # 40 is not a case pack anyone ships, and the nearest one (36) is
        # 11% away — too far to present as evidence. Silence is correct;
        # the operator then answers from the product itself.
        assert derive_units_per_case(Decimal("100.00"), Decimal("2.50"))[0] is None
        assert derive_units_per_case(Decimal("1000.00"), Decimal("7.00"))[0] is None

    def test_the_tolerance_is_the_documented_one(self):
        # A ratio just inside the band resolves; just outside stays silent.
        inside = 12 * (1 + MAX_PACK_RELATIVE_ERROR * 0.8)
        outside = 12 * (1 + MAX_PACK_RELATIVE_ERROR * 1.5)
        assert derive_units_per_case(Decimal(str(inside)), Decimal("1"))[0] == 12
        assert derive_units_per_case(Decimal(str(outside)), Decimal("1"))[0] != 12


class TestSuggestionPrecedence:
    def test_a_reference_candidate_is_offered_as_a_suggestion(self):
        invoice = make_invoice(make_item(BUSCH, "BUSCH 4/6/160Z CAN", "18.75"))
        [row] = build_case_mapping_status(
            invoice, {}, {BUSCH: match(BUSCH, "4.6875", "18.75", "Busch 6pack cans")}
        )
        assert row.suggested_units_per_case == 4
        assert row.suggestion_source == "reference_ratio"
        assert row.reference_description == "Busch 6pack cans"
        assert row.reference_avg_cost == 4.6875

    def test_a_reference_suggestion_is_never_a_confirmed_mapping(self):
        invoice = make_invoice(make_item(BUSCH, "BUSCH 4/6/160Z CAN", "18.75"))
        [row] = build_case_mapping_status(
            invoice, {}, {BUSCH: match(BUSCH, "4.6875", "18.75")}
        )
        assert row.mapped is False          # still blocks the export
        assert row.units_per_case is None   # nothing applied

    def test_a_confirmed_mapping_outranks_the_reference(self):
        invoice = make_invoice(make_item(BUSCH, "BUSCH 4/6/160Z CAN", "18.75"))
        [row] = build_case_mapping_status(
            invoice, {BUSCH: 24}, {BUSCH: match(BUSCH, "4.6875", "18.75")}
        )
        assert row.units_per_case == 24
        assert row.suggestion_source == SUGGESTION_FROM_DATABASE

    def test_the_reference_outranks_an_ambiguous_description(self):
        # "4/6/16OZ" alone could be 4 or 24; the store's cost settles it.
        invoice = make_invoice(make_item(BUSCH, "BUSCH 4/6/160Z CAN", "18.75"))
        [without] = build_case_mapping_status(invoice, {})
        assert without.suggestion_source == "description_ambiguous"
        assert without.suggestion_candidates == [4, 24]

        [with_ref] = build_case_mapping_status(
            invoice, {}, {BUSCH: match(BUSCH, "4.6875", "18.75")}
        )
        assert with_ref.suggestion_source == "reference_ratio"
        assert with_ref.suggestion_candidates == []   # the ambiguity is resolved

    def test_a_matched_product_with_no_cost_falls_back_to_the_document(self):
        invoice = make_invoice(make_item(BEATBOX, "RB COCONUT 24/12OZ", "50.20"))
        [row] = build_case_mapping_status(
            invoice, {}, {BEATBOX: match(BEATBOX, None, "50.20", "ref name")}
        )
        assert row.suggestion_source == "description"
        assert row.suggested_units_per_case == 24
        assert row.reference_description == "ref name"   # identity still shown

    def test_an_unmatched_product_behaves_exactly_as_before(self):
        invoice = make_invoice(make_item(BEATBOX, "BEATBOX MALT BLUEBER", "34.50"))
        [row] = build_case_mapping_status(invoice, {}, {})
        assert row.suggested_units_per_case is None
        assert row.suggestion_source is None
        assert row.reference_description is None


class TestDescriptionIsNeverUsedToMatch:
    def test_a_reference_entry_for_a_different_upc_is_not_applied(self):
        # Same wording, different product. Only the UPC decides.
        invoice = make_invoice(make_item(BUSCH, "BUSCH 4/6/160Z CAN", "18.75"))
        [row] = build_case_mapping_status(
            invoice, {}, {"99999999999": match("99999999999", "4.6875", "18.75",
                                              "BUSCH 4/6/160Z CAN")}
        )
        assert not row.suggestion_source.startswith("reference")
        assert row.reference_description is None

    def test_a_line_with_no_upc_can_never_match(self):
        invoice = make_invoice(make_item(None, "BUSCH 4/6/160Z CAN", "18.75"))
        [row] = build_case_mapping_status(
            invoice, {}, {BUSCH: match(BUSCH, "4.6875", "18.75", "BUSCH 4/6/160Z CAN")}
        )
        assert row.item_code is None
        assert row.reference_description is None
