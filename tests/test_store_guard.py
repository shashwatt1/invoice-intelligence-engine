"""
tests/test_store_guard.py — only a physical store can be assigned.

Every endpoint that assigns a physical store (upload with a store chosen up
front, paused-document store confirmation, store assignment for a
store-pending invoice) refuses a Store Master record whose kind is a source
identity, through the one shared guard (app/services/store_guard.py).

Offline: no database. Store rows are real transient Store objects, so the
guard reads the model's own kind rule; repository lookups and the first
write after the guard are monkeypatched. A request that passes the guard
reaches that first write, which raises a sentinel error — proof the store was
accepted without touching a database.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.api.v1 import documents as documents_module
from app.api.v1 import invoices as invoices_module
from app.core.dependencies import require_authenticated_user
from app.core.exceptions import ValidationError
from app.database.session import get_db
from app.models.document import DocumentStatus
from app.models.store import (
    IDENTITY_UNRESOLVED,
    KIND_PHYSICAL,
    KIND_SOURCE_IDENTITY,
    SOURCE_CSTOREPRO,
    SOURCE_ITEM_SALES,
    SOURCE_OPERATOR,
    TYPE_DIRECTORY_NAME,
    TYPE_STORE_ALIAS,
    TYPE_STORE_CODE,
    Store,
    StoreIdentifier,
)
from app.models.user import User, UserRole
from app.services.store_guard import REASON_SOURCE_IDENTITY, ensure_physical_store

PDF = ("ok.pdf", b"%PDF-1.4 " + b"x" * 2048, "application/pdf")
SENTINEL = "reached-the-first-write-after-the-guard"


def _store(*, name: str | None = None, address: str | None = None,
           identifiers: tuple[tuple[str, str, str], ...] = ()) -> Store:
    return Store(
        id=uuid.uuid4(), display_name=name, address_line_1=address, identity_status=IDENTITY_UNRESOLVED,
        identifiers=[StoreIdentifier(source_system=s, identifier_type=t, identifier_value=v)
                     for s, t, v in identifiers],
    )


def _directory_store(name: str = "PB Wolf") -> Store:
    return _store(name=name, address="1 Main St", identifiers=((SOURCE_CSTOREPRO, TYPE_DIRECTORY_NAME, name),))


def _source_identity(code: str) -> Store:
    return _store(identifiers=((SOURCE_ITEM_SALES, TYPE_STORE_CODE, code),))


class _FakeSession:
    async def commit(self) -> None:
        raise AssertionError("a refused or sentinel-stopped request must not commit")

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _manager_without_database(app):
    manager = User(id=uuid.uuid4(), username="guard-test", password_hash="x",
                   role=UserRole.MANAGER.value, is_active=True)

    async def fake_db():
        yield _FakeSession()

    app.dependency_overrides[require_authenticated_user] = lambda: manager
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[invoices_module.get_pipeline] = lambda: SimpleNamespace()
    yield
    for dependency in (require_authenticated_user, get_db, invoices_module.get_pipeline):
        app.dependency_overrides.pop(dependency, None)


def _directory_of(monkeypatch, *stores: Store) -> None:
    by_id = {s.id: s for s in stores}

    async def get(self, store_id):
        return by_id.get(store_id)

    monkeypatch.setattr(invoices_module.StoreRepository, "get", get)


def _sentinel(*_args, **_kwargs):
    raise ValidationError(message=SENTINEL, detail={"reached": SENTINEL})


def _assert_refused_as_source_identity(response, store: Store) -> None:
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["detail"]["reason"] == REASON_SOURCE_IDENTITY
    assert error["detail"]["field"] == "store_id"
    assert error["detail"]["value"] == str(store.id)
    assert "not a physical store" in error["message"]


def _assert_accepted(response) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["error"]["detail"] == {"reached": SENTINEL}


# ---- the rule itself --------------------------------------------------------

class TestKindRule:
    def test_directory_named_and_addressed_stores_are_physical(self):
        for store in (_directory_store(), _store(name="Some Store"), _store(address="9 Elm St")):
            assert store.kind == KIND_PHYSICAL
            assert ensure_physical_store(store) is store

    def test_a_record_known_only_by_a_source_code_is_refused(self):
        store = _source_identity("47708760")
        assert store.kind == KIND_SOURCE_IDENTITY
        with pytest.raises(ValidationError) as refused:
            ensure_physical_store(store)
        assert refused.value.detail["reason"] == REASON_SOURCE_IDENTITY

    def test_the_rule_is_the_kind_not_the_code(self):
        # A different source code, or none at all: still refused.
        for store in (_source_identity("99999999"), _store()):
            with pytest.raises(ValidationError):
                ensure_physical_store(store)
        # The same code on a physical store is accepted: the code is not the rule.
        physical = _store(name="PB Wolf", identifiers=((SOURCE_CSTOREPRO, TYPE_DIRECTORY_NAME, "PB Wolf"),
                                                        (SOURCE_ITEM_SALES, TYPE_STORE_CODE, "47708760")))
        assert ensure_physical_store(physical) is physical

    def test_an_alias_does_not_make_a_record_a_store(self):
        # Aliases are governance labels: a record holding only an alias is not physical.
        alias_only = _store(identifiers=((SOURCE_OPERATOR, TYPE_STORE_ALIAS, "WOLF"),))
        assert alias_only.kind == KIND_SOURCE_IDENTITY
        with pytest.raises(ValidationError):
            ensure_physical_store(alias_only)

    def test_the_guard_names_no_store_code(self):
        from pathlib import Path

        import app.services.store_guard as guard
        assert "47708760" not in Path(guard.__file__).read_text()


# ---- upload / process with a store chosen up front -----------------------

class TestProcessUpload:
    async def _post(self, client, store_id: str):
        return await client.post("/api/v1/invoices/process", files={"file": PDF}, data={"store_id": store_id})

    async def test_a_physical_store_is_accepted(self, client, monkeypatch):
        store = _directory_store()
        _directory_of(monkeypatch, store)
        monkeypatch.setattr(invoices_module.UploadService, "handle_upload", _async(_sentinel))
        _assert_accepted(await self._post(client, str(store.id)))

    async def test_item_sales_47708760_is_refused_before_the_file_is_touched(self, client, monkeypatch):
        store = _source_identity("47708760")
        _directory_of(monkeypatch, store)
        touched = []
        monkeypatch.setattr(invoices_module.UploadService, "handle_upload", _async(lambda *a: touched.append(a)))
        _assert_refused_as_source_identity(await self._post(client, str(store.id)), store)
        assert touched == []

    async def test_any_source_identity_is_refused(self, client, monkeypatch):
        store = _source_identity("12345678")
        _directory_of(monkeypatch, store)
        _assert_refused_as_source_identity(await self._post(client, str(store.id)), store)


# ---- paused-document store confirmation ----------------------------------

class TestConfirmStore:
    def _paused(self, monkeypatch):
        document = SimpleNamespace(id=uuid.uuid4(), status=DocumentStatus.STORE_CONFIRMATION_REQUIRED,
                                   store_id=None, store_candidates=[])

        async def get(self, document_id):
            return document if document_id == document.id else None

        monkeypatch.setattr(documents_module.DocumentRepository, "get", get)
        monkeypatch.setattr(documents_module.DocumentRepository, "set_status", _async(_sentinel))
        return document

    async def _post(self, client, document, store: Store):
        return await client.post(f"/api/v1/documents/{document.id}/confirm-store",
                                 json={"store_id": str(store.id), "confirmed_by": "guard-test"})

    async def test_a_physical_store_is_accepted(self, client, monkeypatch):
        document, store = self._paused(monkeypatch), _directory_store()
        _directory_of(monkeypatch, store)
        _assert_accepted(await self._post(client, document, store))
        assert document.store_id == store.id

    async def test_item_sales_47708760_is_refused(self, client, monkeypatch):
        document, store = self._paused(monkeypatch), _source_identity("47708760")
        _directory_of(monkeypatch, store)
        _assert_refused_as_source_identity(await self._post(client, document, store), store)
        assert document.store_id is None
        assert document.status == DocumentStatus.STORE_CONFIRMATION_REQUIRED

    async def test_any_source_identity_is_refused(self, client, monkeypatch):
        document, store = self._paused(monkeypatch), _source_identity("55555555")
        _directory_of(monkeypatch, store)
        _assert_refused_as_source_identity(await self._post(client, document, store), store)
        assert document.store_id is None


# ---- store assignment for a store-pending invoice ------------------------

class TestAssignStore:
    def _pending(self, monkeypatch):
        invoice = SimpleNamespace(id=uuid.uuid4(), store_id=None, document_id=uuid.uuid4(),
                                  document=SimpleNamespace(store_id=None))

        async def get_detail(self, invoice_id):
            return invoice if invoice_id == invoice.id else None

        monkeypatch.setattr(invoices_module.InvoiceRepository, "get_detail", get_detail)
        monkeypatch.setattr(invoices_module.ProcessingLogRepository, "add", _async(_sentinel))
        return invoice

    async def _post(self, client, invoice, store: Store):
        return await client.post(f"/api/v1/invoices/{invoice.id}/assign-store",
                                 json={"store_id": str(store.id), "assigned_by": "guard-test"})

    async def test_a_physical_store_is_accepted(self, client, monkeypatch):
        invoice, store = self._pending(monkeypatch), _directory_store("AF NV Midler")
        _directory_of(monkeypatch, store)
        _assert_accepted(await self._post(client, invoice, store))
        assert invoice.store_id == store.id

    async def test_item_sales_47708760_is_refused(self, client, monkeypatch):
        invoice, store = self._pending(monkeypatch), _source_identity("47708760")
        _directory_of(monkeypatch, store)
        _assert_refused_as_source_identity(await self._post(client, invoice, store), store)
        assert invoice.store_id is None and invoice.document.store_id is None

    async def test_any_source_identity_is_refused(self, client, monkeypatch):
        invoice, store = self._pending(monkeypatch), _source_identity("00000001")
        _directory_of(monkeypatch, store)
        _assert_refused_as_source_identity(await self._post(client, invoice, store), store)
        assert invoice.store_id is None


def _async(fn):
    async def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper
