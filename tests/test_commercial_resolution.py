"""
tests/test_commercial_resolution.py — the transitional Product Master resolution.

  * Order: APPROVED physical-store override, then the APPROVED global
    mapping (held under Item Sales 47708760 — provenance, not the scope), then legacy, then Requires Mapping.
  * Two approved Product Master mappings that disagree are a CONFLICT: nothing
    is chosen and the line blocks export.
  * Only APPROVED mappings are used. 47708760 is never read as a physical
    store, and nothing is written to Store Master or anywhere else.
  * Off by default: every line resolves exactly as before, so the golden EDI
    is byte-for-byte unchanged.
  * The invoice's own description is kept; the normalized name is separate.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.models.product_master import STATE_APPROVED, STATE_REJECTED, STATE_REVIEW_REQUIRED
from app.models.store import KIND_PHYSICAL, KIND_SOURCE_IDENTITY
from app.services import case_mapping_service, mapping_queue_service
from app.services.export_service import (
    build_pdi_export,
    persisted_pdi_export_eligibility,
    unmapped_item_codes,
)
from app.services.product_master import commercial_resolution as cr

CODE = "01820000334"
PHYSICAL = cr.Scope("store-rcm", "LG - RCM")
GLOBAL = cr.Scope("store-47708760", cr.global_scope_label("47708760"))


def scoped(state=STATE_APPROVED, units=24, basis="UNIT_IS_SELLING_UNIT", mid="m1", pid="p1"):
    return cr.ScopedMapping(mid, pid, state, basis, units)


def resolve(**kw):
    return cr.resolve_code(CODE, physical_scope=PHYSICAL, global_scope=GLOBAL, **kw)


class TestResolutionOrder:
    def test_an_approved_physical_store_mapping_wins(self):
        r = resolve(physical=[scoped(mid="phys")], global_=[scoped(mid="src")], legacy_units=24)
        assert (r.path, r.units_per_case, r.mapping_id, r.scope_label) == (
            cr.PATH_PRODUCT_MASTER_PHYSICAL, 24, "phys", "LG - RCM")
        assert r.product_master_authoritative
        assert "global mapping agrees with the physical-store override" in r.notes

    def test_an_approved_global_mapping_is_used_and_named_global_with_its_provenance(self):
        r = resolve(global_=[scoped(units=12, mid="src")])
        assert (r.path, r.units_per_case, r.mapping_id) == (cr.PATH_PRODUCT_MASTER_GLOBAL, 12, "src")
        assert (r.scope_store_id, r.scope_label) == ("store-47708760", "Global (distributor evidence, held under Item Sales · 47708760)")

    def test_the_approved_product_master_value_is_used_over_a_different_legacy_value_and_said_so(self):
        r = resolve(global_=[scoped(units=12)], legacy_units=24)
        assert (r.path, r.units_per_case, r.legacy_units) == (cr.PATH_PRODUCT_MASTER_GLOBAL, 12, 24)
        assert r.notes == ("legacy mapping says 24; the approved Product Master value is used",)

    @pytest.mark.parametrize("state", [STATE_REVIEW_REQUIRED, "PENDING", STATE_REJECTED])
    def test_an_unapproved_source_mapping_is_never_used(self, state):
        r = resolve(global_=[scoped(state=state, units=12)])
        assert (r.path, r.units_per_case) == (cr.PATH_REQUIRES_MAPPING, None)
        assert r.notes == (f"Product Master mapping in Global (distributor evidence, held under Item Sales · 47708760) is {state} "
                           f"(UNIT_IS_SELLING_UNIT) — not used until approved",)

    def test_an_unapproved_product_master_mapping_leaves_legacy_exactly_where_it_was(self):
        r = resolve(global_=[scoped(state=STATE_REVIEW_REQUIRED, basis="CONFLICT", units=None)], legacy_units=24)
        assert (r.path, r.units_per_case) == (cr.PATH_LEGACY_FALLBACK, 24)
        assert not r.product_master_authoritative

    def test_nothing_anywhere_requires_a_mapping(self):
        r = resolve()
        assert (r.path, r.units_per_case, r.notes) == (cr.PATH_REQUIRES_MAPPING, None, ())

    def test_legacy_alone_is_a_visible_fallback(self):
        r = resolve(legacy_units=24)
        assert (r.path, r.units_per_case, r.legacy_units) == (cr.PATH_LEGACY_FALLBACK, 24, 24)


class TestConflictsAreNeverChosen:
    def test_a_physical_override_disagreeing_with_the_global_mapping_blocks(self):
        r = resolve(physical=[scoped(units=24)], global_=[scoped(units=12)], legacy_units=24)
        assert (r.path, r.units_per_case) == (cr.PATH_CONFLICT, None)
        assert r.notes == ("physical-store override (UNIT_IS_SELLING_UNIT x24) and global mapping "
                           "(UNIT_IS_SELLING_UNIT x12) disagree",)
        resolution = cr.CommercialResolution({CODE: r}, enabled=True)
        assert resolution.units == {}, "a conflict never reaches the exporter, and legacy does not paper over it"

    def test_two_approved_mappings_in_one_scope_blocks(self):
        r = resolve(global_=[scoped(mid="a", pid="p1"), scoped(mid="b", pid="p2")])
        assert r.path == cr.PATH_CONFLICT and "2 approved Product Master mappings" in r.notes[0]


# ---- loading, with the real code path over fakes ------------------------------

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    """Answers the one Product Master query; records every call so a write would show."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, statement):
        self.calls.append(statement)
        return _Result(self.rows)

    def __getattr__(self, name):   # add/flush/commit/delete would land here
        raise AssertionError(f"the resolver must not call session.{name}")


def store(sid, kind, label):
    return SimpleNamespace(id=uuid.UUID(sid), kind=kind, label=label)


RCM = store("00000000-0000-0000-0000-00000000000a", KIND_PHYSICAL, "LG - RCM")
ITEM_SALES = store("00000000-0000-0000-0000-00000000000b", KIND_SOURCE_IDENTITY, "Item Sales · 47708760")


def mapping_row(store_obj, state=STATE_APPROVED, units=12, code=CODE):
    return SimpleNamespace(id=uuid.uuid4(), product_id=uuid.uuid4(), store_id=store_obj.id, pdi_item_code=code,
                           approval_state=state, commercial_unit_basis="UNIT_IS_SELLING_UNIT",
                           units_accounted_for=units)


@pytest.fixture
def world(monkeypatch):
    def bind(*, enabled=True, source_code="47708760", legacy=None, stores=(RCM, ITEM_SALES), by_code=ITEM_SALES):
        settings = SimpleNamespace(product_master_commercial_resolution=enabled, commercial_source_identity=source_code)
        monkeypatch.setattr(cr, "get_settings", lambda: settings)

        class Legacy:
            def __init__(self, _s):
                pass

            async def units_by_item_code(self, store_id, codes):
                return {c: u for c, u in (legacy or {}).items() if c in codes}

        class Stores:
            def __init__(self, _s):
                pass

            async def get(self, store_id):
                return next((s for s in stores if s.id == store_id), None)

            async def by_identifier(self, source_system, identifier_type, value):
                assert (source_system, identifier_type, value) == ("item_sales", "store_code", source_code)
                return by_code

        monkeypatch.setattr(cr, "ProductCaseMappingRepository", Legacy)
        monkeypatch.setattr(cr, "StoreRepository", Stores)
    return bind


class TestLoading:
    async def test_off_by_default_resolves_exactly_as_before_and_reads_no_product_master(self, world):
        assert Settings.model_fields["product_master_commercial_resolution"].default is False
        assert Settings.model_fields["commercial_source_identity"].default == ""
        world(enabled=False, legacy={CODE: 24})
        session = _Session([mapping_row(ITEM_SALES)])
        r = await cr.resolve_store_codes(session, RCM.id, [CODE, "09999999999"])
        assert r.units == {CODE: 24}
        assert {c: x.path for c, x in r.lines.items()} == {
            CODE: cr.PATH_LEGACY_FALLBACK, "09999999999": cr.PATH_REQUIRES_MAPPING}
        assert session.calls == []

    async def test_a_physical_store_invoice_uses_the_global_mapping(self, world):
        world(legacy={})
        session = _Session([mapping_row(ITEM_SALES, units=12)])
        r = await cr.resolve_store_codes(session, RCM.id, [CODE])
        line = r.lines[CODE]
        assert (line.path, line.units_per_case, line.scope_label) == (
            cr.PATH_PRODUCT_MASTER_GLOBAL, 12, "Global (distributor evidence, held under Item Sales · 47708760)")
        assert r.units == {CODE: 12}
        assert len(session.calls) == 1, "one read; nothing written (the session refuses writes)"

    async def test_a_global_holder_that_is_a_physical_store_is_refused(self, world):
        linked = store("00000000-0000-0000-0000-00000000000b", KIND_PHYSICAL, "PB Wolf")
        world(legacy={}, stores=(RCM, linked), by_code=linked)
        r = await cr.resolve_store_codes(_Session([mapping_row(linked)]), RCM.id, [CODE])
        assert r.lines[CODE].path == cr.PATH_REQUIRES_MAPPING
        assert r.units == {}

    async def test_47708760_is_never_the_physical_scope(self, world):
        # An older invoice still filed under the holding location itself: its
        # mapping applies as GLOBAL, never as a physical store's own override.
        world(legacy={})
        r = await cr.resolve_store_codes(_Session([mapping_row(ITEM_SALES, units=12)]), ITEM_SALES.id, [CODE])
        assert r.lines[CODE].path == cr.PATH_PRODUCT_MASTER_GLOBAL

    async def test_no_global_holder_configured_uses_only_the_physical_store(self, world):
        world(source_code="", legacy={CODE: 24})
        r = await cr.resolve_store_codes(_Session([]), RCM.id, [CODE])
        assert (r.lines[CODE].path, r.units) == (cr.PATH_LEGACY_FALLBACK, {CODE: 24})


# ---- the existing workflow sees the same answer ---------------------------------

def item(sku="018200003349", description="BUD LIGHT 24/12 CAN", order=1):
    return SimpleNamespace(product_sku=sku, description=description, unit_price=Decimal("18.25"),
                           quantity=Decimal("2"), sort_order=order, line_type="product", line_total=Decimal("36.50"),
                           pack_size=None, discount=None, deposit=None, duplicate_candidate=None)


def an_invoice(*items, store_id=RCM.id):
    return SimpleNamespace(id=uuid.uuid4(), invoice_number="1000540", grand_total=Decimal("36.50"),
                           invoice_date=None, store_id=store_id, items=list(items), status="VALIDATED",
                           document_id=uuid.uuid4())


class TestTheExistingWorkflow:
    async def test_invoice_units_come_from_the_resolution(self, monkeypatch):
        async def fake(session, store_id, codes):
            return cr.CommercialResolution({CODE: cr.CodeResolution(CODE, cr.PATH_PRODUCT_MASTER_GLOBAL, 12)}, True)
        monkeypatch.setattr(case_mapping_service, "resolve_store_codes", fake)
        assert await case_mapping_service.invoice_units_by_item_code(None, an_invoice(item())) == {CODE: 12}
        assert await case_mapping_service.invoice_units_by_item_code(None, an_invoice(item(), store_id=None)) == {}

    def test_a_line_without_an_approved_mapping_blocks_export_and_asks_for_a_mapping(self):
        resolution = cr.CommercialResolution({CODE: cr.CodeResolution(CODE, cr.PATH_REQUIRES_MAPPING, None)}, True)
        invoice = an_invoice(item())
        assert unmapped_item_codes(invoice, resolution.units) == [CODE]
        assert not persisted_pdi_export_eligibility(invoice, resolution.units).allowed

    async def test_the_requires_mapping_queue_uses_the_same_resolution(self, monkeypatch):
        mapped, missing = an_invoice(item()), an_invoice(item(sku="012345678905", order=1))

        class Session:
            async def execute(self, _statement):
                return _Result([mapped, missing])

        async def fake(session, store_id, codes):
            return cr.CommercialResolution({CODE: cr.CodeResolution(CODE, cr.PATH_PRODUCT_MASTER_GLOBAL, 12)}, True)

        async def no_pending(*_a, **_k):
            return {}
        monkeypatch.setattr(mapping_queue_service, "resolve_store_codes", fake)
        monkeypatch.setattr(mapping_queue_service, "pending_by_item_code", no_pending)
        groups = await mapping_queue_service.list_unresolved_mapping_groups(Session())
        assert [g.item_code for g in groups] == ["01234567890"], "a resolved line is not queued; a missing one is"


class TestEdiStaysDeterministic:
    def test_same_units_give_byte_identical_files_whichever_path_supplied_them(self):
        invoice = an_invoice(item(), item(sku="012345678905", description="CHIPS", order=2))
        legacy = build_pdi_export(invoice, {CODE: 24, "01234567890": 6})
        master = cr.CommercialResolution({
            CODE: cr.CodeResolution(CODE, cr.PATH_PRODUCT_MASTER_GLOBAL, 24),
            "01234567890": cr.CodeResolution("01234567890", cr.PATH_LEGACY_FALLBACK, 6),
        }, True)
        assert build_pdi_export(invoice, master.units) == legacy
        assert build_pdi_export(invoice, master.units) == build_pdi_export(invoice, master.units)


class TestNormalizedProductName:
    def test_priority_canonical_description_then_canonical_name_then_the_invoice_wording(self):
        assert cr.choose_product_name("Bud Lt 24pk", "BUD LIGHT 24/12 CAN", "BUD LIGHT CAN") == (
            "BUD LIGHT 24/12 CAN", cr.NAME_FROM_CANONICAL_DESCRIPTION)
        assert cr.choose_product_name("Bud Lt 24pk", None, "BUD LIGHT CAN") == ("BUD LIGHT CAN", cr.NAME_FROM_CANONICAL_NAME)
        assert cr.choose_product_name("Bud Lt 24pk", None, None) == ("Bud Lt 24pk", cr.NAME_FROM_INVOICE)

    async def test_each_line_keeps_its_own_description_beside_the_normalized_name(self, monkeypatch):
        async def names(session, codes, product_ids=None):
            return {CODE: ("BUD LIGHT 24/12 CAN", None)}
        monkeypatch.setattr(cr, "canonical_names", names)
        invoice = an_invoice(item(description="Bud Lt 24pk"), item(sku=None, description="DELIVERY", order=2))
        found = await cr.line_product_names(None, invoice, None)
        assert found == {
            1: {"normalized_description": "BUD LIGHT 24/12 CAN",
                "normalized_description_source": cr.NAME_FROM_CANONICAL_DESCRIPTION},
            2: {"normalized_description": "DELIVERY", "normalized_description_source": cr.NAME_FROM_INVOICE},
        }
        assert [i.description for i in invoice.items] == ["Bud Lt 24pk", "DELIVERY"], "source wording untouched"

    def test_a_case_mapping_row_reports_its_path_and_scope(self):
        resolution = cr.CommercialResolution({CODE: cr.CodeResolution(
            CODE, cr.PATH_PRODUCT_MASTER_GLOBAL, 12, mapping_id="m1", scope_label=GLOBAL.label)}, True)
        assert cr.resolution_fields(resolution, CODE) == {
            "resolution_path": cr.PATH_PRODUCT_MASTER_GLOBAL, "resolution_scope": GLOBAL.label,
            "resolution_mapping_id": "m1", "resolution_notes": []}
        assert cr.resolution_fields(resolution, None) == {} and cr.resolution_fields(None, CODE) == {}

    def test_the_resolver_never_writes_a_mapping_store_or_product(self):
        import ast
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "app/services/product_master/commercial_resolution.py").read_text()
        calls = {node.func.attr for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and ast.unparse(node.func.value) == "session"}
        assert calls <= {"execute"}, calls
        assert not any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) for t in n.targets)
                       for n in ast.walk(ast.parse(source))), "no attribute of any record is assigned"
