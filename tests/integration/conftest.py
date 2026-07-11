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

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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
        await conn.run_sync(Base.metadata.create_all)
    yield engine

    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE processing_logs, invoice_items, invoices, vendors, documents CASCADE")
        )
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    """A fresh session per test against an empty schema."""
    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        # Start from clean tables — pipeline tests commit real transactions.
        await session.execute(
            text("TRUNCATE processing_logs, invoice_items, invoices, vendors, documents CASCADE")
        )
        await session.commit()
        yield session
