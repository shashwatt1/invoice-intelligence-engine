"""
tests/test_edi_export_ledger.py — a delivered PDI file is recorded, not re-created anonymously.

  * The ledger row carries the exact bytes and their SHA-256, who exported it
    (the signed-in account), a per-invoice export number, the commercial
    resolution behind every line, and the build (git SHA, Alembic revision).
  * Off by default: the export behaves exactly as before and writes nothing.
  * On: the body the client receives is byte-identical to what was recorded.
  * Migration 0030 chains from 0029 and matches the model column for column.
"""

from __future__ import annotations

import ast
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1 import exports as api
from app.models.edi_export import EdiExport
from app.models.user import User, UserRole
from app.services import edi_export_ledger as ledger
from app.services.product_master import commercial_resolution as cr

ROOT = Path(__file__).resolve().parent.parent
CONTENT = "AMOUNT 0101497   20260910+00108076\r\nB01820053030BUD LIGHT                0000000023500100000100010000000000\r\n"
CODE = "01820053030"


def resolution():
    return cr.CommercialResolution({CODE: cr.CodeResolution(
        CODE, cr.PATH_PRODUCT_MASTER_GLOBAL, 1, mapping_id="m1",
        scope_label="Global (distributor evidence, held under Item Sales · 47708760)")}, enabled=True)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _Session:
    def __init__(self, previous=None, revisions=("0030",)):
        self.previous, self.revisions = previous, revisions
        self.added, self.flushed, self.committed = [], 0, 0

    async def execute(self, statement):
        if "alembic_version" in str(statement):
            return _Result(list(self.revisions))
        return _Result(self.previous)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed += 1

    async def commit(self):
        self.committed += 1


INVOICE = SimpleNamespace(id=uuid.uuid4(), document_id=uuid.uuid4(), invoice_number="101497")
USER = SimpleNamespace(id=uuid.uuid4(), username="barj", role="MANAGER")


class TestTheLedgerRow:
    async def test_the_exact_bytes_their_hash_who_and_why_are_recorded(self, monkeypatch):
        monkeypatch.setenv("RENDER_GIT_COMMIT", "885182fadbb9870c2f3c7f3665d3974fa5cff532")
        monkeypatch.delenv("GIT_SHA", raising=False)
        session = _Session(previous=2)
        row = await ledger.record_pdi_export(session, INVOICE, CONTENT, resolution(), USER)
        assert session.added == [row] and session.flushed == 1 and session.committed == 0
        assert (row.content, row.byte_size) == (CONTENT, len(CONTENT.encode()))
        assert row.sha256 == hashlib.sha256(CONTENT.encode()).hexdigest()
        assert "\r\n" in row.content, "CRLF is kept exactly"
        assert row.export_number == 3
        assert (row.exported_by, row.exported_by_role, row.exported_by_user_id) == ("barj", "MANAGER", USER.id)
        assert (row.invoice_id, row.document_id, row.invoice_number) == (INVOICE.id, INVOICE.document_id, "101497")
        assert row.resolution_snapshot["lines"][CODE] == {
            "path": "PRODUCT_MASTER_GLOBAL", "units": 1, "mapping_id": "m1",
            "scope": "Global (distributor evidence, held under Item Sales · 47708760)",
            "legacy_units": None, "notes": []}
        assert row.build["git_sha"] == "885182fadbb9870c2f3c7f3665d3974fa5cff532"
        assert row.build["alembic_revision"] == "0030"

    async def test_the_first_export_is_number_one_and_an_unknown_build_is_left_empty(self, monkeypatch):
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
        monkeypatch.delenv("GIT_SHA", raising=False)
        row = await ledger.record_pdi_export(_Session(previous=None, revisions=()), INVOICE, CONTENT,
                                             resolution(), USER)
        assert row.export_number == 1
        assert (row.build["git_sha"], row.build["alembic_revision"]) == (None, None)


@pytest.fixture
def endpoint(app, monkeypatch):
    from app.core.dependencies import require_authenticated_user
    from app.database.session import get_db

    session = _Session()
    recorded = []
    manager = User(id=uuid.uuid4(), username="barj", password_hash="x", role=UserRole.MANAGER.value, is_active=True)

    async def fake_db():
        yield session

    class Invoices:
        def __init__(self, _db):
            pass

        async def get_detail(self, _iid):
            return SimpleNamespace(**vars(INVOICE), status="VALIDATED", items=[])

    async def resolve(_db, _invoice):
        return resolution()

    async def record(db, invoice, content, res, user):
        recorded.append((content, user.username, res.lines[CODE].path))
        return SimpleNamespace(id=uuid.uuid4(), export_number=1, sha256=hashlib.sha256(content.encode()).hexdigest())

    monkeypatch.setattr(api, "InvoiceRepository", Invoices)
    monkeypatch.setattr(api, "invoice_commercial_resolution", resolve)
    monkeypatch.setattr(api, "persisted_pdi_export_eligibility",
                        lambda invoice, units: SimpleNamespace(allowed=True, blocked_reason=None))
    monkeypatch.setattr(api, "build_pdi_export", lambda invoice, units: CONTENT if units == {CODE: 1} else "wrong")
    monkeypatch.setattr(api, "record_pdi_export", record)
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[require_authenticated_user] = lambda: manager

    def flag(on):
        monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(edi_export_ledger=on))
    yield SimpleNamespace(session=session, recorded=recorded, flag=flag)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_authenticated_user, None)


class TestTheExportEndpoint:
    async def test_off_by_default_the_export_is_unchanged_and_nothing_is_recorded(self, client, endpoint):
        from app.core.config import Settings

        assert Settings.model_fields["edi_export_ledger"].default is False
        endpoint.flag(False)
        r = await client.get(f"/api/v1/invoices/{INVOICE.id}/export", params={"format": "pdi"})
        assert r.status_code == 200, r.text
        assert r.content == CONTENT.encode()
        assert endpoint.recorded == [] and endpoint.session.committed == 0
        assert not any(h.lower().startswith("x-edi") for h in r.headers)

    async def test_on_the_delivered_file_is_exactly_what_was_recorded(self, client, endpoint):
        endpoint.flag(True)
        r = await client.get(f"/api/v1/invoices/{INVOICE.id}/export", params={"format": "pdi"})
        assert r.status_code == 200, r.text
        assert endpoint.recorded == [(CONTENT, "barj", "PRODUCT_MASTER_GLOBAL")]
        assert endpoint.session.committed == 1
        assert r.content == CONTENT.encode()
        assert r.headers["x-edi-sha256"] == hashlib.sha256(r.content).hexdigest()
        assert r.headers["x-edi-export-number"] == "1"


class TestTheMigration:
    def test_0030_chains_from_0029_and_creates_only_edi_exports(self):
        source = (ROOT / "alembic/versions/20261008_0030_edi_exports.py").read_text()
        assert 'revision = "0030"' in source and 'down_revision = "0029"' in source
        assert source.count("op.create_table(") == 1 and '"edi_exports"' in source
        for forbidden in ("op.alter_column", "op.drop_column", "op.execute", "UPDATE ", "DELETE "):
            assert forbidden not in source, forbidden

    def test_the_migration_matches_the_model_column_for_column(self):
        tree = ast.parse((ROOT / "alembic/versions/20261008_0030_edi_exports.py").read_text())
        migration = {node.args[0].value for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "Column"}
        assert migration == set(EdiExport.__table__.columns.keys())
