"""
Tests — the pilot store precondition for Item Sales store code 47708760.

The command creates one unresolved, nameless store carrying one identifier,
exactly as migration 0013 does for a store code it finds, and nothing else.
These pin what it creates, that it is idempotent, that it refuses rather
than attaching the code to a store that already has an identity, that it is
refused on a wrong target, and that a dry run writes nothing.

Offline: a fake DB-API connection that understands only the command's own
fixed statements and, like Postgres, rejects writes in a read-only session.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import uuid
from pathlib import Path

import pytest

import scripts.pilot_store_precondition as precondition
from scripts.pilot_seed_guard import TARGET_LOCAL, TARGET_PILOT, SeedTarget
from scripts.pilot_store_precondition import (
    INSERT_IDENTIFIER,
    INSERT_STORE,
    SELECT_IDENTIFIERS,
    SELECT_STORES,
    STORE_CODE,
    PreconditionRefused,
    StoreState,
    apply_plan,
    plan_precondition,
    read_state,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "scripts" / "pilot_store_precondition.py"

APPLE = str(uuid.uuid4())
RCM = str(uuid.uuid4())
EVIDENCE = [{"file": f"data/reference/store_{STORE_CODE}/Item_Sales_Summary.xlsx",
             "preamble": f"Store: {STORE_CODE}"}]


def pilot_stores() -> list[dict]:
    """The pilot as it is: Apple Foods II and RCM, both unresolved, both named."""
    def store(sid, name, customer, address, city, state, postal):
        return {"id": sid, "display_name": name, "customer_name": customer,
                "address_line_1": address, "address_line_2": None, "city": city,
                "state": state, "postal_code": postal, "status": "active",
                "identity_status": "unresolved", "notes": "NOT linked to any Item Sales code"}
    return [store(APPLE, "Apple Foods II", "PB Wolf Group Inc", "800 Wolf St", "Syracuse",
                  "NY", "13208-1224"),
            store(RCM, "RCM", "Red Cliff Market", "1409 E St George Blvd", "St George",
                  "UT", "84790")]


def pilot_identifiers() -> list[dict]:
    def ident(sid, itype, value):
        return {"id": str(uuid.uuid4()), "store_id": sid, "source_system": "document",
                "identifier_type": itype, "identifier_value": value}
    return [ident(APPLE, "customer_name", "APPLE FOODS II"), ident(APPLE, "postal_code", "13208"),
            ident(RCM, "customer_name", "RED CLIFF MARKET"), ident(RCM, "postal_code", "84790")]


class FakeConnection:
    """store tables in memory; only the command's statements are understood."""

    def __init__(self, stores=None, identifiers=None, *, break_insert=False):
        self.stores = pilot_stores() if stores is None else stores
        self.identifiers = pilot_identifiers() if identifiers is None else identifiers
        self.readonly = True
        self.executed: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self.break_insert = break_insert
        self._pending_stores: list[dict] = []
        self._pending_identifiers: list[dict] = []

    def set_session(self, readonly=None, autocommit=None):
        if readonly is not None:
            self.readonly = readonly

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1
        self.stores += self._pending_stores
        self.identifiers += self._pending_identifiers
        self._pending_stores, self._pending_identifiers = [], []

    def rollback(self):
        self.rollbacks += 1
        self._pending_stores, self._pending_identifiers = [], []

    def close(self):
        pass

    def snapshot(self):
        return copy.deepcopy((self.stores, self.identifiers))


class _Cursor:
    def __init__(self, connection: FakeConnection):
        self.c = connection
        self._rows: list[tuple] = []

    def execute(self, sql, params=None):
        self.c.executed.append(sql)
        if sql == SELECT_STORES:
            rows = self.c.stores + self.c._pending_stores
            self._rows = [tuple(s[k] for k in precondition.STORE_COLUMNS) for s in rows]
        elif sql == SELECT_IDENTIFIERS:
            rows = self.c.identifiers + self.c._pending_identifiers
            self._rows = [tuple(i[k] for k in precondition.IDENTIFIER_COLUMNS) for i in rows]
        elif sql in (INSERT_STORE, INSERT_IDENTIFIER):
            if self.c.readonly:
                raise RuntimeError("cannot execute INSERT in a read-only transaction")
            if sql == INSERT_STORE:
                self.c._pending_stores.append({
                    "id": params["id"], **dict.fromkeys(precondition.IDENTITY_FIELDS),
                    "display_name": "BROKEN" if self.c.break_insert else None,
                    "status": params["status"], "identity_status": params["identity_status"],
                    "notes": params["notes"]})
            else:
                key = (params["source_system"], params["identifier_type"],
                       params["identifier_value"])
                if any((i["source_system"], i["identifier_type"], i["identifier_value"]) == key
                       for i in self.c.identifiers + self.c._pending_identifiers):
                    raise RuntimeError("duplicate key uq_store_identifier_source_value")
                self.c._pending_identifiers.append({
                    "id": params["id"], "store_id": params["store_id"],
                    "source_system": params["source_system"],
                    "identifier_type": params["identifier_type"],
                    "identifier_value": params["identifier_value"],
                    "evidence": json.loads(params["evidence"])})
        else:
            raise AssertionError(f"unexpected statement: {sql}")

    def fetchall(self):
        return list(self._rows)


def plan_for(connection: FakeConnection):
    return plan_precondition(read_state(connection), EVIDENCE)


def create(connection: FakeConnection) -> dict:
    return apply_plan(connection, plan_for(connection), EVIDENCE)


# ---------------------------------------------------------------------------


class TestWhatItCreates:

    def test_a_new_unresolved_store_with_no_identity(self):
        connection = FakeConnection()
        result = create(connection)
        new = [s for s in connection.stores if s["id"] == result["store_id"]]
        assert len(new) == 1
        store = new[0]
        assert store["identity_status"] == "unresolved"
        assert store["status"] == "active"
        for name in precondition.IDENTITY_FIELDS:
            assert store[name] is None, name
        assert f"Known only as Item Sales store code {STORE_CODE}" in store["notes"]
        assert "NOT linked to Apple Foods II or RCM" in store["notes"]

    def test_exactly_one_identifier_item_sales_store_code_47708760(self):
        connection = FakeConnection()
        result = create(connection)
        added = [i for i in connection.identifiers if i["store_id"] == result["store_id"]]
        assert len(added) == 1
        assert (added[0]["source_system"], added[0]["identifier_type"],
                added[0]["identifier_value"]) == ("item_sales", "store_code", "47708760")
        assert added[0]["evidence"]["verified"] is False
        assert added[0]["evidence"]["files"] == [EVIDENCE[0]["file"]]

    def test_it_is_not_attached_to_apple_foods_or_rcm(self):
        connection = FakeConnection()
        result = create(connection)
        owner = next(i["store_id"] for i in connection.identifiers
                     if i["identifier_value"] == STORE_CODE)
        assert owner == result["store_id"]
        assert owner not in (APPLE, RCM)

    def test_existing_stores_and_identifiers_are_not_modified(self):
        connection = FakeConnection()
        before_stores, before_identifiers = connection.snapshot()
        create(connection)
        assert connection.stores[:2] == before_stores
        assert connection.identifiers[:len(before_identifiers)] == before_identifiers
        assert len(connection.stores) == 3
        assert len(connection.identifiers) == len(before_identifiers) + 1

    def test_the_write_is_one_committed_transaction(self):
        connection = FakeConnection()
        create(connection)
        assert connection.commits == 1 and connection.rollbacks == 0
        assert [s for s in connection.executed if s.startswith("INSERT")] == \
            [INSERT_STORE, INSERT_IDENTIFIER]


class TestIdempotency:

    def test_a_second_run_is_already_satisfied_and_writes_nothing(self):
        connection = FakeConnection()
        first = create(connection)
        state = connection.snapshot()
        plan = plan_for(connection)
        assert plan.action == "already_satisfied"
        assert plan.existing_owner["id"] == first["store_id"]
        assert apply_plan(connection, plan, EVIDENCE) == {"written": False}
        assert connection.snapshot() == state
        assert connection.commits == 1

    def test_the_identifier_is_never_duplicated(self):
        connection = FakeConnection()
        create(connection)
        create(connection)
        assert sum(1 for i in connection.identifiers if i["identifier_value"] == STORE_CODE) == 1
        assert len(connection.stores) == 3


class TestRefusals:

    @pytest.mark.parametrize("owner", [APPLE, RCM])
    def test_refused_when_the_code_already_names_an_identified_store(self, owner):
        identifiers = pilot_identifiers() + [{
            "id": str(uuid.uuid4()), "store_id": owner, "source_system": "item_sales",
            "identifier_type": "store_code", "identifier_value": STORE_CODE}]
        connection = FakeConnection(identifiers=identifiers)
        state = connection.snapshot()
        with pytest.raises(PreconditionRefused) as refused:
            plan_for(connection)
        assert "already names store" in refused.value.reason
        assert connection.snapshot() == state

    def test_refused_when_the_code_names_an_unresolved_store_with_other_identifiers(self):
        other = str(uuid.uuid4())
        stores = pilot_stores() + [{"id": other, **dict.fromkeys(precondition.IDENTITY_FIELDS),
                                    "status": "active", "identity_status": "unresolved",
                                    "notes": None}]
        identifiers = pilot_identifiers() + [
            {"id": str(uuid.uuid4()), "store_id": other, "source_system": "item_sales",
             "identifier_type": "store_code", "identifier_value": STORE_CODE},
            {"id": str(uuid.uuid4()), "store_id": other, "source_system": "item_sales",
             "identifier_type": "store_code", "identifier_value": "86357232"}]
        with pytest.raises(PreconditionRefused):
            plan_for(FakeConnection(stores=stores, identifiers=identifiers))

    def test_refused_beside_an_ambiguous_nameless_store_without_identifiers(self):
        stores = pilot_stores() + [{"id": str(uuid.uuid4()),
                                    **dict.fromkeys(precondition.IDENTITY_FIELDS),
                                    "status": "active", "identity_status": "unresolved",
                                    "notes": None}]
        with pytest.raises(PreconditionRefused) as refused:
            plan_for(FakeConnection(stores=stores))
        assert "no name and no identifiers" in refused.value.reason

    def test_refused_without_evidence(self):
        with pytest.raises(PreconditionRefused) as refused:
            plan_precondition(StoreState(pilot_stores(), pilot_identifiers()), [])
        assert "no Item Sales export" in refused.value.reason

    def test_refused_when_the_tables_change_between_plan_and_write(self):
        connection = FakeConnection()
        plan = plan_for(connection)
        connection.identifiers.append({
            "id": str(uuid.uuid4()), "store_id": APPLE, "source_system": "item_sales",
            "identifier_type": "store_code", "identifier_value": STORE_CODE})
        state = connection.snapshot()
        with pytest.raises(PreconditionRefused):
            apply_plan(connection, plan, EVIDENCE)
        assert connection.rollbacks == 1 and connection.commits == 0
        assert connection.snapshot() == state

    def test_a_write_that_does_not_verify_is_rolled_back(self):
        connection = FakeConnection(break_insert=True)
        state = connection.snapshot()
        with pytest.raises(PreconditionRefused):
            create(connection)
        assert connection.commits == 0 and connection.rollbacks == 1
        assert connection.snapshot() == state


class TestTheTargetIsVerified:

    def _run(self, monkeypatch, tmp_path, argv, target=None, connection=None):
        monkeypatch.setattr(precondition, "ROOT", tmp_path)
        monkeypatch.setattr(precondition, "REPORT", tmp_path / "report.json")
        monkeypatch.setattr(precondition, "find_evidence", lambda: EVIDENCE)
        if target is not None:
            monkeypatch.setattr(precondition, "resolve_seed_target", lambda *a, **k: target)
        import psycopg2

        monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: connection or FakeConnection())
        monkeypatch.setattr(sys, "argv", ["pilot_store_precondition.py", *argv])
        precondition.main()

    PILOT_ARGS = ["--pilot-seed", "--expect-database", "postgres",
                  "--expect-host-contains", "supabase"]

    def test_a_wrong_database_name_is_refused_before_connecting(self, monkeypatch, tmp_path):
        import scripts.pilot_seed_guard as guard

        monkeypatch.setattr(guard, "_identity",
                            lambda dsn: ("db.x.supabase.co", "not_the_pilot", "0024"))
        monkeypatch.setattr(guard, "_check_schema", lambda dsn: [])
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "database_url",
                            "postgresql+asyncpg://u:p@db.x.supabase.co:5432/postgres")
        monkeypatch.setattr(settings, "database_url_sync",
                            "postgresql+psycopg2://u:p@db.x.supabase.co:5432/postgres")
        connection = FakeConnection()
        with pytest.raises(SystemExit) as refused:
            self._run(monkeypatch, tmp_path, self.PILOT_ARGS, connection=connection)
        assert "not_the_pilot" in str(refused.value)
        assert connection.executed == []

    def test_a_wrong_revision_is_refused(self, monkeypatch, tmp_path):
        import scripts.pilot_seed_guard as guard

        monkeypatch.setattr(guard, "_identity",
                            lambda dsn: ("db.x.supabase.co", "postgres", "0021"))
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "database_url",
                            "postgresql+asyncpg://u:p@db.x.supabase.co:5432/postgres")
        monkeypatch.setattr(settings, "database_url_sync",
                            "postgresql+psycopg2://u:p@db.x.supabase.co:5432/postgres")
        with pytest.raises(SystemExit) as refused:
            self._run(monkeypatch, tmp_path, self.PILOT_ARGS)
        assert "0024" in str(refused.value)

    def test_without_the_pilot_opt_in_it_refuses(self, monkeypatch, tmp_path):
        local = SeedTarget(TARGET_LOCAL, "localhost", "", None)
        with pytest.raises(SystemExit) as refused:
            self._run(monkeypatch, tmp_path, [], target=local)
        assert "--pilot-seed" in str(refused.value)

    def test_the_dry_run_writes_nothing(self, monkeypatch, tmp_path):
        connection = FakeConnection()
        state = connection.snapshot()
        verified = SeedTarget(TARGET_PILOT, "db.x.supabase.co", "postgres", "0024")
        self._run(monkeypatch, tmp_path, [*self.PILOT_ARGS, "--dry-run"],
                  target=verified, connection=connection)
        assert connection.snapshot() == state
        assert not any(s.startswith("INSERT") for s in connection.executed)
        assert connection.readonly is True and connection.commits == 0
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["dry_run"] is True and report["action"] == "create"
        assert report["result"] == {"written": False}
        assert report["identifier"]["identifier_value"] == STORE_CODE

    def test_the_write_mode_creates_the_store(self, monkeypatch, tmp_path):
        connection = FakeConnection()
        verified = SeedTarget(TARGET_PILOT, "db.x.supabase.co", "postgres", "0024")
        self._run(monkeypatch, tmp_path, self.PILOT_ARGS, target=verified,
                  connection=connection)
        assert len(connection.stores) == 3 and connection.commits == 1


class TestScope:

    def test_it_only_ever_touches_the_two_store_tables(self):
        source = SOURCE.read_text()
        statements = re.findall(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)", source)
        assert set(statements) == {"stores", "store_identifiers"}
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b", source)
        for table in ("master_", "product_case_mappings", "invoices", "invoice_items",
                      "documents"):
            assert f"INTO {table}" not in source

    def test_it_never_confirms_identity(self):
        source = SOURCE.read_text()
        assert "IDENTITY_CONFIRMED" not in source
        assert "'confirmed'" not in source and '"confirmed"' not in source

    def test_there_is_no_override_flag(self):
        source = SOURCE.read_text()
        for forbidden in ("--force", "--allow-remote", "--store-id", "--override", "--yes"):
            assert forbidden not in source, forbidden
