"""
tests/test_source_identity_presentation.py — a source identity is never shown as a store.

A Store Master record known only by a source system's identifier (the pilot's
Item Sales store code 47708760) is a source identity, not a physical store.
Wherever the API describes the store behind an invoice, mapping or Product
Master candidate, it says so — from the record's own classification
(Store.kind), never from a store id or a particular code. Physical stores are
described exactly as before.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.models.store import (
    IDENTITY_CONFIRMED,
    IDENTITY_UNRESOLVED,
    KIND_PHYSICAL,
    KIND_SOURCE_IDENTITY,
    Store,
    StoreIdentifier,
)
from app.schemas.processing import SourceIdentityRef, StoreRef


def _store(*, name=None, status=IDENTITY_UNRESOLVED, identifiers=()) -> Store:
    return Store(id=uuid.uuid4(), display_name=name, identity_status=status,
                 identifiers=[StoreIdentifier(source_system=s, identifier_type=t, identifier_value=v)
                              for s, t, v in identifiers])


def source_identity(code="47708760") -> Store:
    return _store(identifiers=(("item_sales", "store_code", code),))


def physical(name="PB Wolf", status=IDENTITY_UNRESOLVED, extra=()) -> Store:
    return _store(name=name, status=status,
                  identifiers=(("cstorepro", "directory_name", name), *extra))


class TestTheStoreSaysWhatItIs:
    def test_a_source_identity_is_known_by_its_source_identifier(self):
        store = source_identity()
        assert store.kind == KIND_SOURCE_IDENTITY
        ident = store.source_identity
        assert (ident.source_system, ident.identifier_type, ident.identifier_value) == (
            "item_sales", "store_code", "47708760")

    def test_a_physical_store_has_no_source_identity_even_carrying_a_source_code(self):
        store = physical(extra=(("item_sales", "store_code", "47708760"),))
        assert store.kind == KIND_PHYSICAL and store.source_identity is None

    def test_governance_labels_never_name_a_source_identity(self):
        # An alias is not what a record is: with nothing else, there is no identifier to show.
        assert _store(identifiers=(("operator", "store_alias", "WOLF"),)).source_identity is None
        # Another source's identifier is used when there is no Item Sales code.
        other = _store(identifiers=(("document", "customer_name", "RED CLIFF TEXACO"),))
        assert other.source_identity.identifier_value == "RED CLIFF TEXACO"


class TestTheApiDescribesIt:
    @pytest.mark.parametrize("code", ["47708760", "12345678"])
    def test_a_source_identity_is_described_by_system_and_code_for_any_code(self, code):
        ref = SourceIdentityRef.from_store(source_identity(code))
        assert (ref.source_label, ref.identifier_type, ref.identifier_value, ref.label) == (
            "Item Sales", "store_code", code, f"Item Sales · {code}")

    def test_store_ref_carries_the_classification(self):
        ref = StoreRef.from_store(source_identity())
        assert ref.kind == KIND_SOURCE_IDENTITY
        assert ref.source_identity.label == "Item Sales · 47708760"

    def test_a_physical_store_ref_is_unchanged(self):
        for store, label in [(physical("LG - RCM", IDENTITY_CONFIRMED), "LG - RCM"),
                             (physical("PB Wolf"), "PB Wolf (identity unconfirmed)")]:
            ref = StoreRef.from_store(store)
            assert (ref.kind, ref.source_identity, ref.label, ref.display_name) == (
                KIND_PHYSICAL, None, label, store.display_name)


class TestTheProductMasterRow:
    @staticmethod
    def _row(store):
        from app.api.v1.product_master import _row

        mapping = SimpleNamespace(
            id=uuid.uuid4(), pdi_item_code="01820000115", commercial_unit_basis="UNIT_IS_SELLING_UNIT",
            units_accounted_for=4, case_cost=None, cost_basis=None, approval_state="REVIEW_REQUIRED",
            evidence={"source_store_identifier": "47708760"}, reviewed_by=None, reviewed_at=None,
            proposed_units_accounted_for=None, proposed_by=None, proposed_note=None)
        product = SimpleNamespace(id=uuid.uuid4(), canonical_upc="018200001154")
        return _row(mapping, product, store, [], [])

    def test_the_queue_names_a_source_identity_by_its_source_not_as_a_store(self):
        row = self._row(source_identity())
        assert row.store_label == "Item Sales · 47708760"
        assert not row.store_label.startswith("Store ")
        assert row.store_kind == KIND_SOURCE_IDENTITY
        assert (row.store_source_identity.source_label, row.store_source_identity.identifier_value) == (
            "Item Sales", "47708760")

    def test_a_physical_store_row_is_exactly_as_before(self):
        row = self._row(physical("AF McKinley", extra=(("item_sales", "store_code", "47708760"),)))
        assert (row.store_label, row.store_kind, row.store_source_identity) == ("AF McKinley", KIND_PHYSICAL, None)


class TestOverTheApi:
    async def test_the_commercial_queue_serves_the_classification(self, app, client, monkeypatch):
        import app.api.v1.product_master as module
        from app.core.dependencies import require_authenticated_user
        from app.database.session import get_db
        from app.models.user import User

        store = source_identity()
        mapping = SimpleNamespace(
            id=uuid.uuid4(), pdi_item_code=None, commercial_unit_basis="CONFLICT", units_accounted_for=None,
            case_cost=None, cost_basis=None, approval_state="REVIEW_REQUIRED", evidence={}, reviewed_by=None,
            reviewed_at=None, proposed_units_accounted_for=None, proposed_by=None, proposed_note=None)
        product = SimpleNamespace(id=uuid.uuid4(), canonical_upc=None)

        class Repository:
            def __init__(self, _session):
                pass

            async def list_candidates(self, **_):
                return [(mapping, product, store)]

            async def count_candidates(self, **_):
                return 1

            async def legacy_mappings_for(self, _codes):
                return {}

            async def descriptions_for(self, _ids):
                return {}

        async def no_db():
            yield None

        monkeypatch.setattr(module, "MasterCommercialRepository", Repository)
        app.dependency_overrides[get_db] = no_db
        app.dependency_overrides[require_authenticated_user] = lambda: User(
            id=uuid.uuid4(), username="viewer", password_hash="x", role="USER", is_active=True)
        try:
            item = (await client.get("/api/v1/product-master/commercial")).json()["items"][0]
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(require_authenticated_user, None)
        assert item["store_label"] == "Item Sales · 47708760"
        assert item["store_kind"] == "source_identity"
        assert item["store_source_identity"]["label"] == "Item Sales · 47708760"
