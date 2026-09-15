"""
tests/integration/test_importer_store_resolution.py — importers reach a
store only through the store master.

An Item Sales export names its store by a code the POS uses; a Beer
Inventory export names nothing. Neither code nor absence is a store:
the master says which Store a code belongs to, and a person says whose
a codeless workbook is.
"""

from __future__ import annotations

import pytest

from app.repositories.store_repository import StoreRepository
from app.services.store_resolution import (
    StoreResolutionError,
    resolve_explicit_store,
    resolve_item_sales_store,
)
from tests.integration.conftest import requires_db, store_id

pytestmark = requires_db

A, B = "47708760", "86357232"


class TestItemSales:
    async def test_a_known_code_resolves_to_its_store(self, db_session):
        r = await resolve_item_sales_store(db_session, source_code=A, requested=None, filename="a.xlsx")
        assert r.store.id == store_id(A) and r.created is False

    async def test_the_requested_store_may_be_named_by_id_or_by_code(self, db_session):
        by_code = await resolve_item_sales_store(db_session, source_code=A, requested=A, filename="a.xlsx")
        by_id = await resolve_item_sales_store(db_session, source_code=A, requested=str(store_id(A)), filename="a.xlsx")
        assert by_code.store.id == by_id.store.id == store_id(A)

    async def test_a_code_belonging_to_another_store_than_requested_is_refused(self, db_session):
        with pytest.raises(StoreResolutionError, match="Refusing to file one store's data under another"):
            await resolve_item_sales_store(db_session, source_code=A, requested=B, filename="a.xlsx")

    async def test_an_unknown_code_is_refused_unless_a_store_is_created_for_it(self, db_session):
        with pytest.raises(StoreResolutionError, match="not attached to any store"):
            await resolve_item_sales_store(db_session, source_code="55555555", requested=None, filename="n.xlsx")
        r = await resolve_item_sales_store(db_session, source_code="55555555", requested=None,
                                           filename="n.xlsx", create_missing=True)
        assert r.created is True
        assert r.store.identity_status == "unresolved" and r.store.display_name is None
        assert r.store.source_codes == ["55555555"]
        assert r.store.label == "Store 55555555 (location not yet confirmed)"

    async def test_an_unknown_code_is_never_attached_to_a_requested_store_by_an_import(self, db_session):
        with pytest.raises(StoreResolutionError, match="attached to a store by a person"):
            await resolve_item_sales_store(db_session, source_code="55555555", requested=A, filename="n.xlsx")
        assert (await StoreRepository(db_session).get(store_id(A))).source_codes == [A]

    async def test_no_code_and_no_request_is_refused(self, db_session):
        with pytest.raises(StoreResolutionError, match="nothing can say whose data this is"):
            await resolve_item_sales_store(db_session, source_code=None, requested=None, filename="x.xlsx")


class TestBeerInventory:
    async def test_requires_an_explicit_store(self, db_session):
        with pytest.raises(StoreResolutionError, match="--store .* is required"):
            await resolve_explicit_store(db_session, None, "Beer Inventory.xlsx")

    async def test_resolves_the_named_store_and_nothing_else(self, db_session):
        assert (await resolve_explicit_store(db_session, B, "Beer Inventory.xlsx")).id == store_id(B)
        with pytest.raises(StoreResolutionError):
            await resolve_explicit_store(db_session, "nope", "Beer Inventory.xlsx")


class TestOneCodeOneStore:
    async def test_a_code_cannot_identify_two_stores(self, db_session):
        repo = StoreRepository(db_session)
        other = await repo.get(store_id(B))
        with pytest.raises(ValueError, match="already identifies store"):
            await repo.add_identifier(other, "item_sales", "store_code", A)
