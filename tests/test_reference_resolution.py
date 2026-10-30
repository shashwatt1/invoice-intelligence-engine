"""
Reviewed commercial reference values for inventory — tests/test_reference_resolution.py (no DB).

The units-per-case rules live in commercial_resolution.resolve_code (reused, not copied); these tests pin what
inventory makes of its answer: provenance on every value, store-specific over shared, shared only without a
conflicting store value, a confirmed store value never silently replaced, and ambiguous or conflicting values
held rather than chosen.
"""

from __future__ import annotations

import pytest

from app.models.product_master import STATE_APPROVED, STATE_PENDING, STATE_REVIEW_REQUIRED
from app.services.product_master import commercial_resolution as cr
from app.services.product_master import reference_resolution as rr

CODE = "03600029145"
PHYSICAL = cr.Scope("store-1", "AF Fixture Store")
GLOBAL = cr.Scope("holder", "Global (distributor evidence)")


def mapping(units, *, state=STATE_APPROVED, mapping_id="m1", override=None, product="p1"):
    return cr.ScopedMapping(mapping_id, product, state, "UNIT_IS_SELLING_UNIT", units, override)


def resolve(**kwargs):
    return cr.resolve_code(CODE, physical_scope=PHYSICAL, global_scope=GLOBAL, **kwargs)


class TestUnitsPerCase:
    def test_an_approved_store_specific_mapping_resolves_with_its_provenance(self):
        value = rr.units_value(resolve(physical=[mapping(24, mapping_id="own")]))
        assert (value.value, value.status, value.source) == (24, rr.STATUS_RESOLVED, cr.PATH_PRODUCT_MASTER_PHYSICAL)
        assert (value.scope, value.approval, value.mapping_id) == ("AF Fixture Store", rr.APPROVAL_APPROVED, "own")

    def test_a_shared_value_is_the_fallback_when_the_store_has_none(self):
        value = rr.units_value(resolve(shared=[mapping(12, mapping_id="shared")]))
        assert (value.value, value.status, value.source) == (12, rr.STATUS_RESOLVED, cr.PATH_PRODUCT_MASTER_GLOBAL)
        assert value.scope == cr.SHARED_SCOPE_LABEL and value.mapping_id == "shared"

    def test_a_store_specific_mapping_wins_over_a_shared_one_only_with_a_recorded_reason(self):
        wins = rr.units_value(resolve(physical=[mapping(18, override="sold as singles")], shared=[mapping(1, mapping_id="s")]))
        assert (wins.value, wins.status, wins.source) == (18, rr.STATUS_RESOLVED, cr.PATH_PRODUCT_MASTER_PHYSICAL)
        assert any("sold as singles" in n for n in wins.notes)

    def test_a_store_value_that_disagrees_with_a_shared_one_without_a_reason_is_held_not_chosen(self):
        value = rr.units_value(resolve(physical=[mapping(18)], shared=[mapping(1, mapping_id="s")]))
        assert (value.value, value.status) == (None, rr.STATUS_HELD)
        assert any("disagree" in n for n in value.notes)

    def test_a_stores_confirmed_case_mapping_is_never_silently_replaced_by_a_shared_value(self):
        value = rr.units_value(resolve(legacy_units=18, shared=[mapping(1)]))
        assert (value.value, value.status) == (None, rr.STATUS_HELD)
        assert any("18" in n for n in value.notes)

    def test_a_stores_confirmed_case_mapping_is_used_with_its_own_provenance_when_nothing_else_exists(self):
        value = rr.units_value(resolve(legacy_units=6))
        assert (value.value, value.status, value.source, value.approval) == (
            6, rr.STATUS_RESOLVED, cr.PATH_LEGACY_FALLBACK, rr.APPROVAL_STORE_CONFIRMED)

    def test_the_same_value_everywhere_resolves_to_the_stores_own_mapping(self):
        value = rr.units_value(resolve(physical=[mapping(12)], shared=[mapping(12, mapping_id="s")]))
        assert (value.value, value.source) == (12, cr.PATH_PRODUCT_MASTER_PHYSICAL)

    def test_two_approved_values_in_one_scope_are_held(self):
        value = rr.units_value(resolve(shared=[mapping(12, mapping_id="a"), mapping(24, mapping_id="b")]))
        assert (value.value, value.status) == (None, rr.STATUS_HELD)

    @pytest.mark.parametrize("state", [STATE_PENDING, STATE_REVIEW_REQUIRED])
    def test_a_mapping_that_is_not_approved_is_never_used_and_is_reported(self, state):
        value = rr.units_value(resolve(shared=[mapping(12, state=state)]))
        assert (value.value, value.status) == (None, rr.STATUS_MISSING)
        assert any(state in n for n in value.notes)

    def test_nothing_at_all_is_missing_not_zero_or_one(self):
        value = rr.units_value(resolve())
        assert (value.value, value.status, value.source) == (None, rr.STATUS_MISSING, None)
        assert rr.units_value(None).status == rr.STATUS_MISSING

    @pytest.mark.parametrize("kwargs", [
        {"physical": [mapping(24)]}, {"shared": [mapping(12)]}, {"legacy_units": 6},
    ])
    def test_an_ambiguous_product_identity_holds_every_source(self, kwargs):
        value = rr.units_value(resolve(**kwargs), "2 master products share the UPC of item 03600029145")
        assert (value.value, value.status) == (None, rr.STATUS_HELD)
        assert any("ambiguous" in n for n in value.notes)

    def test_ambiguity_does_not_invent_a_value_for_a_missing_one(self):
        assert rr.units_value(resolve(), "ambiguous").status == rr.STATUS_MISSING


class TestDescription:
    def test_a_confirmed_canonical_description_wins_over_the_display_name(self):
        value = rr.description_value("NORTHWIND LAGER 12PK", "Northwind Lager")
        assert (value.value, value.status, value.source) == (
            "NORTHWIND LAGER 12PK", rr.STATUS_RESOLVED, cr.NAME_FROM_CANONICAL_DESCRIPTION)
        assert (value.approval, value.scope) == (rr.APPROVAL_CONFIRMED, "Product Master")

    def test_the_canonical_name_is_used_when_there_is_no_description(self):
        value = rr.description_value(None, "Northwind Lager")
        assert (value.value, value.source) == ("Northwind Lager", cr.NAME_FROM_CANONICAL_NAME)

    def test_no_reviewed_wording_is_missing(self):
        assert rr.description_value(None, None) == rr.ResolvedValue(None, rr.STATUS_MISSING)

    def test_an_ambiguous_identity_holds_the_description(self):
        value = rr.description_value("NORTHWIND LAGER 12PK", None, "two products")
        assert (value.value, value.status) == (None, rr.STATUS_HELD)


class TestAmbiguity:
    def test_one_product_or_none_is_not_ambiguous(self):
        assert rr.ambiguity_reason(CODE, ["p1"], None) is None
        assert rr.ambiguity_reason(CODE, [], None) is None
        assert rr.ambiguity_reason(CODE, [], "p9") is None             # no master product names this UPC at all
        assert rr.ambiguity_reason(CODE, ["p1", "p1"], "p1") is None   # the same product twice is one product

    def test_two_products_sharing_a_upc_are_ambiguous(self):
        assert "share the UPC" in rr.ambiguity_reason(CODE, ["p1", "p2"], None)

    def test_a_mapping_that_belongs_to_another_product_than_the_upc_names_is_ambiguous(self):
        assert "different master product" in rr.ambiguity_reason(CODE, ["p1"], "p2")
