"""
tests/integration/test_migrations_match_models.py — the migrations build
the schema the ORM expects.

Every other integration test builds its database with
Base.metadata.create_all, which silently carries the ORM's defaults and
constraints. A real deployment runs `alembic upgrade head`. When the two
disagree the app works in tests and fails in front of a person — which
is exactly what happened when migration 0015 created document_pages.id
without the gen_random_uuid() default every other table has: the first
two-photo upload died with a NOT NULL violation on id.

This test upgrades an empty scratch database with the migrations alone
and asks Alembic's autogenerate what it would still change. The answer
must be nothing.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.database.base import Base
from tests.integration.conftest import ADMIN_URL, requires_db

pytestmark = requires_db

SCRATCH_DB = "invoice_test_migrations"
_ADMIN_SYNC = ADMIN_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
_SCRATCH_SYNC = _ADMIN_SYNC.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"


@pytest.fixture(scope="module")
def migrated_scratch_db():
    admin = create_engine(_ADMIN_SYNC, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH_DB}"))
    env = dict(os.environ, DATABASE_URL_SYNC=_SCRATCH_SYNC,
               DATABASE_URL=_SCRATCH_SYNC.replace("+psycopg2", "+asyncpg"))
    run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                         capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stderr
    yield _SCRATCH_SYNC
    admin.dispose()


def test_upgrade_head_leaves_nothing_for_autogenerate(migrated_scratch_db):
    engine = create_engine(migrated_scratch_db)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True, "compare_server_default": True})
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    # Alembic cannot read a server default back verbatim for JSONB/text
    # defaults, so only report differences on the things that break at
    # runtime: missing tables/columns, nullability, and a NULL default
    # where the model has one.
    material = []
    for change in diff:
        kind = change[0] if isinstance(change, tuple) else change[0][0]
        if kind in ("add_table", "remove_table", "add_column", "remove_column") or kind == "modify_nullable":
            material.append(change)
        elif kind == "modify_default":
            _, _, table, column, _, existing, new = change[0] if isinstance(change[0], tuple) else change
            if existing is None and new is not None:
                material.append((kind, table, column, "database has no default, model expects one"))
    assert material == [], "\n".join(str(c) for c in material)


def test_document_pages_id_is_generated_by_the_database(migrated_scratch_db):
    """The specific defect, pinned: an insert without an id must succeed."""
    engine = create_engine(migrated_scratch_db)
    with engine.begin() as conn:
        default = conn.execute(text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'document_pages' AND column_name = 'id'"
        )).scalar()
        assert default == "gen_random_uuid()"
        # every table the ORM mixin governs must say the same
        rows = conn.execute(text(
            "SELECT c.table_name FROM information_schema.columns c "
            "JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = 'public' "
            "WHERE c.column_name = 'id' AND c.data_type = 'uuid' AND c.table_schema = 'public' "
            "AND (c.column_default IS NULL OR c.column_default <> 'gen_random_uuid()')"
        )).scalars().all()
        assert rows == [], f"uuid id without a server default: {rows}"
    engine.dispose()


def _alembic(migrated_scratch_db, *args):
    env = dict(os.environ, DATABASE_URL_SYNC=migrated_scratch_db,
               DATABASE_URL=migrated_scratch_db.replace("+psycopg2", "+asyncpg"))
    return subprocess.run([sys.executable, "-m", "alembic", *args], capture_output=True, text=True, env=env)


async def _guarded_paths_at(url, expected_schema):
    """
    The paths that run whatever INVENTORY_ENABLED says — the correction guard, invoice delete,
    reopening Retailed, marking Retailed — against the scratch database at `expected_schema`.
    Before 0035 they must not read a column 0035 adds (the full receipt row would: shown last).
    """
    from sqlalchemy import select
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.models.inventory import InventoryReceipt
    from app.services import inventory_approval, inventory_service

    engine = create_async_engine(url.replace("+psycopg2", "+asyncpg"), poolclass=NullPool)
    saved = inventory_service._approval_schema_seen
    inventory_service._approval_schema_seen = False         # this check must read the scratch database itself
    user = SimpleNamespace(id=uuid.uuid4(), username="probe", role="ADMIN", is_active=True)
    try:
        async with AsyncSession(engine) as session:
            assert await inventory_service.inventory_schema(session) == expected_schema
            invoice_id = uuid.uuid4()
            await inventory_service.ensure_invoice_changeable(session, invoice_id, "correct_line_item")
            assert await inventory_service.posted_receipt(session, invoice_id) is None
            release = await inventory_approval.release_invoice_for_deletion(session, invoice_id, user)
            assert (release.reversed, release.withdrawn) == (None, None)
            assert await inventory_approval.withdraw_pending(session, invoice_id, reason="probe", user=user) is None
            await session.rollback()
        if expected_schema == inventory_service.SCHEMA_LEDGER:
            async with AsyncSession(engine) as session:
                with pytest.raises(DBAPIError, match="does not exist"):
                    await session.execute(select(InventoryReceipt).limit(1))
    finally:
        inventory_service._approval_schema_seen = saved
        await engine.dispose()


def _receipt_rules(conn):
    checks = set(conn.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'inventory_receipts'::regclass AND contype = 'c'"
    )).scalars().all())
    indexes = dict(conn.execute(text(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'inventory_receipts'")).tuples().all())
    return checks, indexes


def test_0035_approval_rules_exist_and_the_downgrade_keeps_history(migrated_scratch_db):
    """Runs last in this module and leaves the scratch database at head again."""
    from app.models.inventory import RECEIPT_CHECKS

    engine = create_engine(migrated_scratch_db)
    with engine.connect() as conn:
        checks, indexes = _receipt_rules(conn)
    assert set(RECEIPT_CHECKS) <= checks
    active = indexes["uq_inventory_receipts_invoice_active"]   # Postgres renders: (status)::text = ANY (ARRAY[...])
    assert active.startswith("CREATE UNIQUE INDEX") and "(invoice_id)" in active
    assert "'POSTED'" in active and "'SUBMITTED'" in active and "= ANY" in active
    assert "'REVERSED'" not in active and "'REJECTED'" not in active
    assert "uq_inventory_receipts_invoice_posted" not in indexes and "idx_inventory_receipts_pending" in indexes

    # A submission in the table: downgrading would discard approval history, so it is refused.
    with engine.begin() as conn:
        store = conn.execute(text("INSERT INTO stores (display_name, identity_status) "
                                  "VALUES ('AF Migration Probe', 'confirmed') RETURNING id")).scalar()
        conn.execute(text(
            "INSERT INTO inventory_receipts (store_id, status, line_count, resolution_snapshot, submitted_at, "
            "submitted_by, submitted_role, attestation_sha256, submission_snapshot) "
            "VALUES (:s, 'SUBMITTED', 0, '{}', now(), 'probe', 'ADMIN', repeat('a', 64), '{}')"), {"s": store})
    refused = _alembic(migrated_scratch_db, "downgrade", "0034")
    assert refused.returncode != 0 and "approval-workflow history" in refused.stderr
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM inventory_receipts")).scalar() == 1

    # Scratch database only: with the probe removed, 0035 downgrades to 0034's table and upgrades again.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM inventory_receipts"))
        conn.execute(text("DELETE FROM stores WHERE id = :s"), {"s": store})
    down = _alembic(migrated_scratch_db, "downgrade", "0034")
    assert down.returncode == 0, down.stderr
    with engine.connect() as conn:
        checks, indexes = _receipt_rules(conn)
        nullable = conn.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'inventory_receipts' AND column_name = 'received_on'")).scalar()
    assert checks == {"ck_inventory_receipts_status", "ck_inventory_receipts_reversal"} and nullable == "NO"
    assert "uq_inventory_receipts_invoice_posted" in indexes
    # At 0034 (inventory tables, no approval columns) the always-on paths still work.
    from app.services.inventory_service import SCHEMA_APPROVAL, SCHEMA_LEDGER
    asyncio.run(_guarded_paths_at(migrated_scratch_db, SCHEMA_LEDGER))
    up = _alembic(migrated_scratch_db, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    with engine.connect() as conn:
        assert set(RECEIPT_CHECKS) <= _receipt_rules(conn)[0]
    asyncio.run(_guarded_paths_at(migrated_scratch_db, SCHEMA_APPROVAL))
    engine.dispose()
