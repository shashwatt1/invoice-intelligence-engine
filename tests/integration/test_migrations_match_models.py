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

import os
import subprocess
import sys

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
