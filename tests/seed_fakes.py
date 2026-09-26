"""
An in-memory stand-in for the async session the Product Master seed uses.

Just enough of AsyncSession for the seed stages: `execute` answers the
SELECTs they issue from rows held in memory, `add`/`flush`/`commit`/
`rollback` behave transactionally, and every call is recorded so a test can
assert exactly what was — or was not — attempted.

It lets the real write path and the dry-run plan run against the same
starting state without a Postgres server, and be compared.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import Insert
from sqlalchemy.sql import Select


class _Result:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    def all(self) -> list[tuple]:
        return list(self._rows)

    def scalars(self) -> _Result:
        return _Result([row[0] for row in self._rows])


class FakeSeedDatabase:
    """Committed rows by model class, plus a log of everything attempted."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.committed: dict[type, list] = defaultdict(list)
        self.statements: list[str] = []
        self.writes: list[str] = []      # add / flush / bulk-insert / commit
        self.sessions: list[FakeSession] = []
        # A write log prefix that raises, standing in for a dropped connection.
        self.fail_on = fail_on

    def record_write(self, entry: str) -> None:
        self.writes.append(entry)
        if self.fail_on and entry.startswith(self.fail_on):
            raise ConnectionError(f"connection was closed in the middle of operation ({entry})")

    def get_session_factory(self):
        """Drop-in for app.database.session.get_session_factory."""
        return self._open

    def _open(self) -> FakeSession:
        session = FakeSession(self)
        self.sessions.append(session)
        return session

    def rows(self, model) -> list:
        return list(self.committed.get(model, []))


class FakeSession:
    def __init__(self, db: FakeSeedDatabase) -> None:
        self.db = db
        self.pending: list = []
        self.flushed: list = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc) -> None:
        self.pending.clear()
        self.flushed.clear()

    # Session state a caller may inspect, as on AsyncSession.
    @property
    def new(self) -> list:
        return list(self.pending)

    dirty: list = []
    deleted: list = []

    def add(self, obj) -> None:
        self.db.record_write(f"add {type(obj).__name__}")
        self.pending.append(obj)

    @staticmethod
    def _insert_defaults(obj) -> None:
        """Server-generated id and Python-side column defaults, as on INSERT."""
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        for column in type(obj).__table__.columns:
            if column.default is not None and getattr(obj, column.key, None) is None:
                default = column.default.arg
                value = default(None) if callable(default) else default
                setattr(obj, column.key, value)

    async def flush(self) -> None:
        for obj in self.pending:
            self.db.record_write(f"flush {type(obj).__name__}")
            self._insert_defaults(obj)
            self.flushed.append(obj)
        self.pending.clear()

    async def commit(self) -> None:
        await self.flush()
        self.db.record_write("commit")
        for obj in self.flushed:
            self.db.committed[type(obj)].append(obj)
        self.flushed.clear()

    async def rollback(self) -> None:
        self.pending.clear()
        self.flushed.clear()

    async def execute(self, statement, params=None):
        self.db.statements.append(str(statement).split("\n")[0])
        if isinstance(statement, Insert):
            return self._bulk_insert(statement, params or [])
        if not isinstance(statement, Select):
            text = str(statement).strip().upper()
            if not text.startswith("SET TRANSACTION READ ONLY"):
                self.db.writes.append(f"execute {text[:40]}")
            return _Result([])
        return _Result(self._select(statement))

    def _bulk_insert(self, statement, rows: list[dict]) -> _Result:
        """An ORM bulk INSERT ... RETURNING: one statement for every row."""
        model = statement.entity_description["entity"]
        self.db.record_write(f"bulk-insert {model.__name__} x{len(rows)}")
        created = []
        for fields in rows:
            obj = model(**fields)
            self._insert_defaults(obj)
            created.append(obj)
        self.flushed.extend(created)
        names = [d["name"] for d in statement.returning_column_descriptions]
        return _Result([tuple(getattr(obj, n) for n in names) for obj in created])

    def _visible(self, model) -> list:
        return self.db.rows(model) + [o for o in self.flushed if isinstance(o, model)]

    def _select(self, statement) -> list[tuple]:
        descriptions = statement.column_descriptions
        entities = [d["entity"] for d in descriptions]
        whole = [d["expr"] is d["entity"] for d in descriptions]

        if len(set(entities)) == 2 and all(whole):
            # select(Mapping, Product).join(Product, Product.id == Mapping.product_id)
            left, right = entities
            by_id = {obj.id: obj for obj in self._visible(right)}
            return [(obj, by_id[obj.product_id]) for obj in self._visible(left)
                    if obj.product_id in by_id]

        (model,) = set(entities)
        rows = []
        for obj in self._visible(model):
            rows.append(tuple(
                obj if is_whole else getattr(obj, d["expr"].key)
                for d, is_whole in zip(descriptions, whole, strict=True)
            ))
        return rows
