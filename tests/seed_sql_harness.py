"""
Real SQL for the Product Master seed stages, counted.

tests/seed_fakes.py answers the stages' queries from memory, which is right
for checking WHAT is written but blind to HOW: an ORM flush that sends one
INSERT per row and a single batched INSERT look the same to it. That is
exactly how a per-row identifier insert survived a batching fix.

This runs the stages through SQLAlchemy's real ORM and SQL compiler against
an in-memory SQLite database built from the models, and records every
statement the driver receives, so a test can assert the number of INSERTs
per table does not grow with the number of rows.

The stages are async; no async SQLite driver is installed, so a thin adapter
gives a synchronous Session the few awaitable methods the stages call.
"""

from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.database.base import Base


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(_type, _compiler, **_kw) -> str:
    return "JSON"


# Postgres server defaults, rewritten into SQLite's parenthesised form.
_DEFAULTS = {
    "gen_random_uuid()": "(lower(hex(randomblob(16))))",
    "now()": "CURRENT_TIMESTAMP",
    "'{}'::jsonb": "'{}'",
}


def _sqlite_metadata() -> MetaData:
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            default = column.server_default
            if isinstance(default, DefaultClause):
                arg = str(getattr(default.arg, "text", default.arg))
                replacement = _DEFAULTS.get(arg)
                if replacement is None and "::" in arg:
                    replacement = arg.split("::", 1)[0]
                column.server_default = DefaultClause(text(replacement or arg))
    return metadata


class SQLDatabase:
    """An in-memory SQLite database with the model schema and a statement log."""

    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False})
        _sqlite_metadata().create_all(self.engine)
        self.statements: list[tuple[str, bool, int]] = []
        event.listen(self.engine, "before_cursor_execute", self._record)

    def _record(self, _conn, _cursor, statement, parameters, _context, executemany):
        rows = len(parameters) if executemany else 1
        self.statements.append((" ".join(statement.split()), executemany, rows))

    def get_session_factory(self):
        """Drop-in for app.database.session.get_session_factory."""
        return lambda: AsyncSessionAdapter(Session(self.engine, autoflush=False,
                                                   expire_on_commit=False))

    def reset_log(self) -> None:
        self.statements.clear()

    def inserts_by_table(self) -> Counter:
        """INSERT statements sent to the driver, per table."""
        counts: Counter = Counter()
        for statement, _many, _rows in self.statements:
            match = re.match(r"INSERT INTO (\w+)", statement)
            if match:
                counts[match.group(1)] += 1
        return counts

    def updates_by_table(self) -> Counter:
        counts: Counter = Counter()
        for statement, _many, _rows in self.statements:
            match = re.match(r"UPDATE (\w+)", statement)
            if match:
                counts[match.group(1)] += 1
        return counts

    def count(self, table: str) -> int:
        with self.engine.connect() as connection:
            return connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


class AsyncSessionAdapter:
    """The awaitable subset of AsyncSession the seed stages use."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSessionAdapter:
        return self

    async def __aexit__(self, *exc) -> None:
        self._session.close()

    async def execute(self, statement, params=None):
        if str(statement).strip().upper() == "SET TRANSACTION READ ONLY":
            # The planner's read-only guard; SQLite has no such statement.
            return None
        return self._session.execute(statement, params)

    def add(self, obj) -> None:
        self._session.add(obj)

    async def flush(self) -> None:
        self._session.flush()

    async def commit(self) -> None:
        self._session.commit()

    async def rollback(self) -> None:
        self._session.rollback()

    @property
    def new(self):
        return self._session.new

    @property
    def dirty(self):
        return self._session.dirty

    @property
    def deleted(self):
        return self._session.deleted
