"""
tests/integration/conftest.py — Real-Postgres fixtures for pipeline tests.

These tests need the compose database running:

    docker compose up -d db
    RUN_DB_TESTS=1 .venv/bin/python -m pytest tests/integration -q --no-cov

A dedicated `invoice_test` database is created on the same server so the
development database is never touched. Tables come from Base.metadata
(the migration's structural match to the models is asserted separately).
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import UniqueConstraint, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.store import SOURCE_ITEM_SALES, TYPE_STORE_CODE
from app.repositories.store_repository import StoreRepository

RUN_DB_TESTS = os.getenv("RUN_DB_TESTS") == "1"

ADMIN_URL = os.getenv(
    "TEST_DATABASE_ADMIN_URL",
    "postgresql+asyncpg://invoice_user:invoice_pass@localhost:5432/invoice_db",
)
TEST_DB_NAME = "invoice_test"
TEST_URL = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"

requires_db = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="DB integration test — set RUN_DB_TESTS=1 with the compose db running.",
)


async def _schema_is_stale(conn, metadata) -> bool:
    """True when any model column is missing from the live test schema."""
    rows = await conn.execute(text(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public'"
    ))
    live: dict[str, set[str]] = {}
    for table, column in rows:
        live.setdefault(table, set()).add(column)
    for table in metadata.sorted_tables:
        if table.name not in live:
            continue                      # create_all will make it
        if {c.name for c in table.columns} - live[table.name]:
            return True
    # A unique constraint whose columns changed (0011 added store_number
    # to product_pricing's source-row key) is drift too: create_all never
    # alters, and the old key would make a cross-store test pass or fail
    # for the wrong reason.
    rows = await conn.execute(text(
        "SELECT tc.table_name, tc.constraint_name, kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
        "WHERE tc.table_schema = 'public' AND tc.constraint_type = 'UNIQUE'"
    ))
    live_unique: dict[tuple[str, str], set[str]] = {}
    for table, name, column in rows:
        live_unique.setdefault((table, name), set()).add(column)
    for table in metadata.sorted_tables:
        if table.name not in live:
            continue
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and constraint.name:
                wanted = {c.name for c in constraint.columns}
                if live_unique.get((table.name, constraint.name), wanted) != wanted:
                    return True
    return False


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """Engine bound to a dedicated test database (created on demand)."""
    admin_engine = create_async_engine(ADMIN_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    await admin_engine.dispose()

    engine = create_async_engine(TEST_URL, poolclass=NullPool)
    import app.models  # noqa: F401 — register all tables on Base.metadata
    from app.database.base import Base

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        # create_all never ALTERs an existing table, so a column added by a
        # migration is silently missing from a test database created before
        # it — every new migration then fails integration tests with
        # "column does not exist" until someone drops the database by hand.
        # Detect drift once and rebuild instead.
        if await _schema_is_stale(conn, Base.metadata):
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE processing_logs, invoice_items, invoices, vendors, "
                "documents, product_case_mappings, store_product_references, product_data_proposals, "
                "product_pricing, product_identifier, product_identity, store_identifiers, stores CASCADE"
            )
        )
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    """A fresh session per test against an empty schema."""
    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Start from clean tables — pipeline tests commit real transactions.
        await session.execute(
            text(
                "TRUNCATE processing_logs, invoice_items, invoices, vendors, "
                "documents, product_case_mappings, store_product_references, product_data_proposals, "
                "product_pricing, product_identifier, product_identity, store_identifiers, stores CASCADE"
            )
        )
        await session.commit()
        # Every integration test runs with the two reference stores present,
        # known by their Item Sales store codes, identity unresolved — as
        # the migration left production. Tests refer to them through
        # KNOWN_STORES / store_id() so no test carries a raw UUID.
        KNOWN_STORES.clear()
        for code in ("47708760", "86357232"):
            store = await StoreRepository(session).create(
                notes=f"test fixture: Item Sales store code {code}")
            await StoreRepository(session).add_identifier(
                store, SOURCE_ITEM_SALES, TYPE_STORE_CODE, code,
                evidence={"origin": "test fixture", "verified": True})
            KNOWN_STORES[code] = store.id
        await session.commit()
        yield session


# Item Sales store code -> Store.id for the current test. Filled by db_session.
KNOWN_STORES: dict[str, uuid.UUID] = {}


def store_id(code: str) -> uuid.UUID:
    """The Store id behind an Item Sales store code, in the current test."""
    return KNOWN_STORES[code]


async def approve_all_pending(session, *, reviewed_by: str = "test:reviewer") -> int:
    """
    Play the data reviewer: promote every PENDING proposal.

    The frontend can only propose; a test that needs an authoritative
    mapping has to approve it the way a reviewer would. Returns how many
    were approved. Commits, so the app's own session sees the result.
    """
    from app.models.product_data_proposal import STATUS_PENDING
    from app.repositories.product_data_proposal_repository import (
        ProductDataProposalRepository,
    )
    from app.services import proposal_service

    pending = await ProductDataProposalRepository(session).list(status=STATUS_PENDING)
    for proposal in pending:
        await proposal_service.approve(session, proposal, reviewed_by=reviewed_by)
    await session.commit()
    return len(pending)
