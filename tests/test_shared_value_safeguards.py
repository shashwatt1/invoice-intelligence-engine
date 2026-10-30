"""
The 18-pack regressions the mapping replay found — tests/test_shared_value_safeguards.py

Replay on the local corpus resolved TWISTED TEA HALF & HALF C-18 12OZ (08769200057)
and MICHELOB ULTRA C-18 12OZ (01820096721) at a store from a value another store
had decided (1), while that store's own answer was 18. Both readings fit the printed
"C-18". These tests pin the safeguard: a value decided for every store is used at a
store that never decided the item only when the line's printed pack agrees with it
(or prints none); otherwise that store confirms once.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.case_mapping_service import guard_printed_packs, printed_disagreement
from app.services.product_master import commercial_resolution as cr

MICHELOB, TWISTED = "01820096721", "08769200057"


def shared(code, units, legacy=None, path=cr.PATH_PRODUCT_MASTER_GLOBAL):
    return cr.CodeResolution(code, path, units, mapping_id="shared-1", scope_store_id=cr.SHARED_SCOPE_ID,
                             scope_label=cr.SHARED_SCOPE_LABEL, legacy_units=legacy)


def line(code_printed, pack=None, description="ITEM"):
    return SimpleNamespace(product_sku=code_printed, pack_size=pack, description=description,
                           line_type="product", quantity=1)


class TestTheTwoReplayCases:
    @pytest.mark.parametrize("code, printed, description", [
        (MICHELOB, "018200967214", "MICHELOB ULTRA C-18 12OZ"),
        (TWISTED, "087692000570", "TWISTED TEA HALF & HALF C-18 12OZ"),
    ])
    def test_a_shared_1_against_a_printed_c18_at_a_store_without_its_own_value_is_held(
            self, code, printed, description):
        resolution = cr.CommercialResolution({code: shared(code, 1)}, enabled=True)
        guarded = guard_printed_packs(resolution, [line(printed, "C-18 12OZ", description)])
        held = guarded.lines[code]
        assert (held.path, held.units_per_case) == (cr.PATH_SHARED_UNCONFIRMED, None)
        assert "prints 18" in held.notes[0]
        assert code not in guarded.units           # the export gate and receipts see it as unmapped

    def test_the_store_that_decided_the_item_is_never_held(self):
        # Its own value equals the shared one (legacy row present): the store has decided.
        assert printed_disagreement(shared(MICHELOB, 1, legacy=1), "C-18 12OZ", "MICHELOB ULTRA C-18 12OZ") is None


class TestWhenTheSharedValueIsUsed:
    def test_a_printed_pack_that_agrees_is_used(self):
        assert printed_disagreement(shared("01200000129", 24), "24/12OZ", "SYNTH 24/12OZ") is None

    def test_a_line_printing_no_pack_is_used(self):
        assert printed_disagreement(shared("01200000129", 12), None, "SYNTH COLA") is None

    def test_an_ambiguous_pack_holds_only_a_value_it_cannot_mean(self):
        assert printed_disagreement(shared("01820000063", 24), "4/6/16OZ", "BUSCH 4/6/16OZ") is None
        assert printed_disagreement(shared("01820000063", 6), None, "BUSCH 4/6/16OZ") is not None

    @pytest.mark.parametrize("path", [cr.PATH_PRODUCT_MASTER_PHYSICAL, cr.PATH_LEGACY_FALLBACK, cr.PATH_CONFLICT])
    def test_only_a_value_decided_for_every_store_is_ever_held(self, path):
        assert printed_disagreement(shared(MICHELOB, 1, path=path), "C-18 12OZ", "C-18") is None

    def test_one_disagreeing_line_holds_the_code_for_the_whole_invoice(self):
        resolution = cr.CommercialResolution({MICHELOB: shared(MICHELOB, 1)}, enabled=True)
        guarded = guard_printed_packs(resolution, [line("018200967214", None), line("018200967214", "C-18")])
        assert guarded.lines[MICHELOB].path == cr.PATH_SHARED_UNCONFIRMED


class TestSharedValuesAreForPhysicalStores:
    def test_a_source_identity_invoice_never_resolves_from_a_shared_value(self):
        # resolve_code itself is scope-agnostic; the loader passes no shared rows without a
        # physical scope. Pinned here at the pure level: no shared input, no shared answer.
        r = cr.resolve_code(MICHELOB, physical_scope=None, global_scope=None, shared=[])
        assert r.path == cr.PATH_REQUIRES_MAPPING
