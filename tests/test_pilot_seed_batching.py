"""
Tests — the pilot seed does not send one INSERT per row.

The first live seed attempt timed out on remote round trips: every product,
identifier and description was its own INSERT, because the ORM fetches a
server-generated UUID key back with RETURNING and cannot batch that. A fix
that batched products alone passed every test and still sent ~24k single-row
INSERTs for the other tables, because the in-memory fake cannot see
statements.

These run the real orchestrator and stages through SQLAlchemy's real ORM and
SQL compiler (tests/seed_sql_harness.py) and count what reaches the driver.
The same seed is run at two sizes; the INSERT count per table must not grow
with the number of rows.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.models.store import SOURCE_ITEM_SALES, TYPE_STORE_CODE
from app.services.product_master.candidates import MasterSourceRow
from app.services.product_master.identifiers import DISTRIBUTOR_WORKBOOK
from app.services.product_master.stores import StoreIdentifierIndex, StoreRecord
from scripts import backfill_commercial_source_snapshot as snapshot_stage
from scripts import pilot_seed
from scripts import pilot_seed_plan as planner
from scripts import seed_product_master_commercial as commercial_stage
from scripts import seed_product_master_identity as identity_stage
from scripts.pilot_seed_guard import TARGET_PILOT, SeedTarget
from tests.seed_sql_harness import SQLDatabase

STORE_CODE = "47708760"
TARGET = SeedTarget(TARGET_PILOT, "db.abcdefgh.supabase.co", "postgres", "0024")

WRITTEN_TABLES = ("master_products", "master_product_identifiers",
                  "master_product_descriptions", "master_pack_compositions",
                  "master_commercial_mappings")


def upc_a(body: int) -> str:
    """A valid 12-digit UPC-A for an 11-digit body."""
    digits = f"{body:011d}"
    odd = sum(int(d) for d in digits[0::2])
    even = sum(int(d) for d in digits[1::2])
    return digits + str((10 - (odd * 3 + even) % 10) % 10)


def synthetic_rows(n: int) -> list[MasterSourceRow]:
    """n cases, each with a commercial statement; every third also a pack."""
    rows: list[MasterSourceRow] = []
    for i in range(n):
        case = upc_a(70000000000 + i)
        rows.append(MasterSourceRow(
            source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
            source_file="zink.xlsx", source_sheet="Sheet1", source_row=100 + i,
            raw_identifier=case, description=f"PRODUCT {i}",
            commercial_units_statement="1.0" if i % 2 else "24.0",
            commercial_statement_evidence="Sheet1 items/case column",
            # Mixed empty fields: rows must still batch together.
            case_cost=None if i % 5 == 0 else f"{10 + i}.00",
            store_context=STORE_CODE))
        if i % 3 == 0:
            rows.append(MasterSourceRow(
                source_system="distributor_price_sheet", profile=DISTRIBUTOR_WORKBOOK,
                source_file="zink.xlsx", source_sheet="Zink - Tiki", source_row=5000 + i,
                raw_identifier=case, raw_unit_identifier=upc_a(80000000000 + i),
                package_notation="24/12OZ CANS", description=f"PRODUCT {i} CASE",
                store_context=STORE_CODE))
    return rows


@pytest.fixture
def seed_at(monkeypatch):
    """Run the real seed over n synthetic cases; return the SQL database."""
    import app.database.session as session_module

    index = StoreIdentifierIndex([(SOURCE_ITEM_SALES, TYPE_STORE_CODE, STORE_CODE,
                                   StoreRecord(uuid.uuid4(), "unresolved", None))])

    def run(n: int, *, plan: bool = False):
        rows = synthetic_rows(n)
        for module in (identity_stage, commercial_stage, snapshot_stage, planner):
            monkeypatch.setattr(module, "load_raw_records", lambda: ["synthetic"])
            monkeypatch.setattr(module, "to_master_rows", lambda _r, _s: list(rows))
        for module in (commercial_stage, planner):
            monkeypatch.setattr(module, "_load_store_index", lambda: (index, None))
        db = SQLDatabase()
        monkeypatch.setattr(session_module, "get_session_factory", db.get_session_factory)
        planned = asyncio.run(planner.plan_pipeline(TARGET, None)) if plan else None
        db.reset_log()
        results = asyncio.run(pilot_seed.run_stages(TARGET, False, None))
        assert not any(pilot_seed.stage_failure(r) for r in results.values()), results
        return SimpleNamespace(db=db, plan=planned, results=results)
    return run


class TestInsertsDoNotScaleWithRows:

    def test_insert_statements_per_table_are_the_same_at_three_times_the_rows(self, seed_at):
        small = seed_at(30).db
        large = seed_at(90).db
        assert large.count("master_products") == 3 * small.count("master_products")
        assert small.inserts_by_table() == large.inserts_by_table()

    # One INSERT per table per stage that writes it. Descriptions are written
    # twice: source descriptions by identity, canonical ones by descriptions.
    INSERTS_PER_TABLE = {"master_products": 1, "master_product_identifiers": 1,
                         "master_product_descriptions": 2, "master_pack_compositions": 1,
                         "master_commercial_mappings": 1}

    @pytest.mark.parametrize("table", WRITTEN_TABLES)
    def test_each_table_is_written_once_per_writing_stage(self, seed_at, table):
        db = seed_at(90).db
        assert db.count(table) > 1, table
        assert db.inserts_by_table()[table] == self.INSERTS_PER_TABLE[table], \
            db.inserts_by_table()

    def test_the_whole_seed_sends_a_handful_of_statements(self, seed_at):
        db = seed_at(90).db
        rows_written = sum(db.count(t) for t in WRITTEN_TABLES)
        assert rows_written > 300
        assert len(db.statements) < 40, len(db.statements)

    def test_no_insert_is_a_single_row_statement_for_a_multi_row_table(self, seed_at):
        db = seed_at(90).db
        single_row_inserts = [s for s, many, rows in db.statements
                              if s.startswith("INSERT") and not many and "VALUES (?" in s
                              and s.count("), (") == 0]
        assert single_row_inserts == []


class TestTheRealRowsMatchThePlan:

    def test_row_counts_equal_the_dry_run_plan(self, seed_at):
        seeded = seed_at(60, plan=True)
        for table in WRITTEN_TABLES:
            assert seeded.db.count(table) == seeded.plan["would_create"][table], table

    def test_every_reference_resolves(self, seed_at):
        db = seed_at(60).db
        with db.engine.connect() as connection:
            from sqlalchemy import text

            for table, column in (("master_product_identifiers", "product_id"),
                                  ("master_product_descriptions", "product_id"),
                                  ("master_pack_compositions", "parent_product_id"),
                                  ("master_pack_compositions", "child_product_id"),
                                  ("master_commercial_mappings", "product_id")):
                dangling = connection.execute(text(
                    f"SELECT count(*) FROM {table} t LEFT JOIN master_products p "
                    f"ON p.id = t.{column} WHERE p.id IS NULL")).scalar_one()
                assert dangling == 0, (table, column)

    def test_snapshots_and_canonical_descriptions_are_written(self, seed_at):
        db = seed_at(60).db
        with db.engine.connect() as connection:
            from sqlalchemy import text

            empty = connection.execute(text(
                "SELECT count(*) FROM master_commercial_mappings "
                "WHERE source_snapshot NOT LIKE '%source_rows%'")).scalar_one()
            canonical = connection.execute(text(
                "SELECT count(*) FROM master_products "
                "WHERE canonical_description IS NOT NULL")).scalar_one()
        assert empty == 0
        assert canonical > 0

    def test_a_second_run_inserts_nothing(self, seed_at, monkeypatch):
        seeded = seed_at(30)
        before = {t: seeded.db.count(t) for t in WRITTEN_TABLES}
        seeded.db.reset_log()
        results = asyncio.run(pilot_seed.run_stages(TARGET, False, None))
        assert not any(pilot_seed.stage_failure(r) for r in results.values())
        assert {t: seeded.db.count(t) for t in WRITTEN_TABLES} == before
        assert not seeded.db.inserts_by_table()


class TestBulkInsertKeepsDefaults:
    """render_nulls is used only when it cannot bypass a column default."""

    def test_none_in_a_defaulted_column_still_gets_its_default(self):
        from sqlalchemy import text

        from app.models.product_master import MasterPackComposition

        db = SQLDatabase()
        session = db.get_session_factory()()
        product = uuid.uuid4()
        with db.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO master_products (id, canonical_key, identity_basis, identity_state) "
                "VALUES (:id, 'k', 'UPC_A', 'AUTO_MATCHED')"), {"id": product.hex})
        row = {"parent_product_id": product, "child_product_id": None, "child_quantity": 2,
               "composition_basis": "PACKAGE_NOTATION", "evidence_state": None,
               "evidence": {}, "source_system": "s"}

        async def go():
            await identity_stage.bulk_insert(session, MasterPackComposition, [row])
            await session.commit()
        asyncio.run(go())
        with db.engine.connect() as connection:
            state = connection.execute(text(
                "SELECT evidence_state FROM master_pack_compositions")).scalar_one()
        default = MasterPackComposition.__table__.c.evidence_state.default.arg
        assert state == default
