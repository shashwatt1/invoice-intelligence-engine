"""
tests/integration/test_store_reference_db.py — the reference layer
against real Postgres.

Covers what only a database can prove: the unique key, idempotent
re-import, exact-code lookup, and that a NULL cost survives a round trip
as NULL rather than becoming 0.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.repositories.store_product_reference_repository import (
    StoreProductReferenceRepository,
)
from tests.integration.conftest import requires_db, store_id

pytestmark = requires_db

STORE = "47708760"


def make_row(item_code, avg_cost="4.6875", description="Busch 6pack cans",
             source_file="a.xlsx", store=STORE):
    return {
        "store_id": store_id(store),
        "item_code": item_code,
        "scan_code_raw": item_code,
        "description": description,
        "avg_cost": None if avg_cost is None else Decimal(avg_cost),
        "avg_price": Decimal("6.99"),
        "source_file": source_file,
        "imported_at": datetime.now(UTC),
    }


class TestUpsert:
    async def test_rows_insert_then_update_in_place(self, db_session):
        repo = StoreProductReferenceRepository(db_session)

        inserted, updated = await repo.upsert_many([make_row("01820000063")])
        assert (inserted, updated) == (1, 0)

        inserted, updated = await repo.upsert_many(
            [make_row("01820000063", avg_cost="5.25", source_file="b.xlsx")]
        )
        assert (inserted, updated) == (0, 1)

        row = await repo.get(store_id(STORE), "01820000063")
        assert row.avg_cost == Decimal("5.2500")
        assert row.source_file == "b.xlsx"
        assert (await repo.stats(store_id(STORE)))["total"] == 1

    async def test_reimporting_the_same_batch_changes_no_row_count(self, db_session):
        repo = StoreProductReferenceRepository(db_session)
        batch = [make_row(f"0182000{i:04d}") for i in range(25)]

        await repo.upsert_many(batch)
        first = await repo.stats(store_id(STORE))
        inserted, updated = await repo.upsert_many(batch)

        assert (inserted, updated) == (0, 25)
        assert (await repo.stats(store_id(STORE)))["total"] == first["total"] == 25

    async def test_the_same_code_in_two_stores_is_two_rows(self, db_session):
        repo = StoreProductReferenceRepository(db_session)
        await repo.upsert_many([
            make_row("01820000063", avg_cost="4.6875", store="47708760"),
            make_row("01820000063", avg_cost="9.99", store="86357232"),
        ])
        assert (await repo.get(store_id("47708760"), "01820000063")).avg_cost == Decimal("4.6875")
        assert (await repo.get(store_id("86357232"), "01820000063")).avg_cost == Decimal("9.9900")


class TestNullCostSurvives:
    async def test_an_uncosted_product_round_trips_as_null(self, db_session):
        repo = StoreProductReferenceRepository(db_session)
        await repo.upsert_many([make_row("01200000108", avg_cost=None, description="WATER")])

        row = await repo.get(store_id(STORE), "01200000108")
        assert row.avg_cost is None
        assert row.avg_cost != Decimal("0")
        assert row.description == "WATER"      # identity still usable

    async def test_stats_separate_costed_from_uncosted(self, db_session):
        repo = StoreProductReferenceRepository(db_session)
        await repo.upsert_many([
            make_row("01820000063", avg_cost="4.6875"),
            make_row("01200000108", avg_cost=None),
            make_row("01200000109", avg_cost=None),
        ])
        stats = await repo.stats(store_id(STORE))
        assert stats == {"total": 3, "costed": 1, "uncosted": 2, "distinct_item_codes": 3}


class TestExactLookup:
    async def test_lookup_is_by_code_and_returns_only_matches(self, db_session):
        repo = StoreProductReferenceRepository(db_session)
        await repo.upsert_many([
            make_row("01820000063", description="Busch 6pack cans"),
            make_row("85005919539", description="BEATBOX MALT BLUEBERRY LEMONADE"),
        ])

        found = await repo.by_item_codes(store_id(STORE), ["01820000063", "99999999999"])
        assert set(found) == {"01820000063"}
        assert found["01820000063"].description == "Busch 6pack cans"

    async def test_an_empty_or_none_code_list_is_safe(self, db_session):
        repo = StoreProductReferenceRepository(db_session)
        assert await repo.by_item_codes(store_id(STORE), []) == {}
        assert await repo.by_item_codes(store_id(STORE), [None, ""]) == {}

    async def test_another_stores_catalogue_is_never_returned(self, db_session):
        repo = StoreProductReferenceRepository(db_session)
        await repo.upsert_many([make_row("01820000063", store="86357232")])
        assert await repo.by_item_codes(store_id(STORE), ["01820000063"]) == {}
