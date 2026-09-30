"""
tests/test_phase_d_relationships.py — store / vendor / invoice relationship hardening.

  * USER and MANAGER name a physical store before processing; ADMIN may not.
  * Only an ADMIN may continue without a store (defer); a MANAGER confirms one.
  * Store decisions record the authenticated account, not a typed name.
  * Reprocessing never replaces a CONFIRMED vendor: the new reading is kept on
    the invoice and recorded as a discrepancy; nothing is merged or created.
  * The receiving-eligibility contract (future Phase E) is exact.
  * Every code path that writes an invoice's or a document's store is known
    and guarded; a new one fails here until it is reviewed.
  * Migration 0029 adds the two date indexes and nothing else.

Offline: no database. Repositories are monkeypatched; the first write after a
check raises a sentinel, proving the check passed without touching storage.
"""

from __future__ import annotations

import ast
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.v1 import documents as documents_module
from app.api.v1 import invoices as invoices_module
from app.core.dependencies import require_authenticated_user
from app.core.exceptions import ValidationError
from app.database.session import get_db
from app.models.document import DocumentStatus
from app.models.store import IDENTITY_UNRESOLVED, Store, StoreIdentifier
from app.models.user import User, UserRole
from app.models.vendor import VENDOR_CONFIRMED, VENDOR_UNRESOLVED, Vendor
from app.services import receiving_eligibility as rx
from app.services import reprocess_service
from app.services.store_guard import require_store_choice

ROOT = Path(__file__).resolve().parent.parent
PDF = ("ok.pdf", b"%PDF-1.4 " + b"x" * 2048, "application/pdf")
SENTINEL = "reached-the-first-write-after-the-check"


def physical_store(name: str = "PB Wolf") -> Store:
    return Store(id=uuid.uuid4(), display_name=name, identity_status=IDENTITY_UNRESOLVED,
                 identifiers=[StoreIdentifier(source_system="cstorepro", identifier_type="directory_name",
                                              identifier_value=name)])


def source_identity(code: str = "47708760") -> Store:
    return Store(id=uuid.uuid4(), display_name=None, identity_status=IDENTITY_UNRESOLVED,
                 identifiers=[StoreIdentifier(source_system="item_sales", identifier_type="store_code",
                                              identifier_value=code)])


def _sentinel(*_args, **_kwargs):
    raise ValidationError(message=SENTINEL, detail={"reached": SENTINEL})


def _async(fn):
    async def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper


class _Session:
    async def commit(self):
        raise AssertionError("nothing in these checks may commit")

    async def rollback(self):
        return None

    async def close(self):
        return None


@pytest.fixture
def act_as(app):
    async def fake_db():
        yield _Session()

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[invoices_module.get_pipeline] = lambda: SimpleNamespace()

    def bind(role: str) -> User:
        account = User(id=uuid.uuid4(), username=f"{role.lower()}-account", password_hash="x",
                       role=role, is_active=True)
        app.dependency_overrides[require_authenticated_user] = lambda: account
        return account
    yield bind
    for dependency in (get_db, invoices_module.get_pipeline, require_authenticated_user):
        app.dependency_overrides.pop(dependency, None)


def _directory(monkeypatch, *stores: Store) -> None:
    by_id = {s.id: s for s in stores}

    async def get(self, store_id):
        return by_id.get(store_id)

    monkeypatch.setattr(invoices_module.StoreRepository, "get", get)


# ---- A1: a physical store before processing (USER / MANAGER) -----------------

class TestTheStoreRuleAtProcessing:
    @pytest.mark.parametrize("role", [UserRole.USER.value, UserRole.MANAGER.value])
    @pytest.mark.parametrize("store_value", [None, "", "   "])
    async def test_user_and_manager_cannot_process_without_a_store(self, client, act_as, monkeypatch,
                                                                   role, store_value):
        act_as(role)
        touched = []
        monkeypatch.setattr(invoices_module.UploadService, "handle_upload", _async(lambda *a: touched.append(a)))
        data = {} if store_value is None else {"store_id": store_value}
        response = await client.post("/api/v1/invoices/process", files={"file": PDF}, data=data)
        assert response.status_code == 422
        assert response.json()["error"]["detail"] == {"field": "store_id", "reason": "required_for_role",
                                                      "role": role}
        assert touched == [], "refused before any file is stored"

    async def test_an_admin_may_process_without_a_store(self, client, act_as, monkeypatch):
        act_as(UserRole.ADMIN.value)
        monkeypatch.setattr(invoices_module.UploadService, "handle_upload", _async(_sentinel))
        response = await client.post("/api/v1/invoices/process", files={"file": PDF})
        assert response.json()["error"]["detail"] == {"reached": SENTINEL}

    @pytest.mark.parametrize("role", [UserRole.USER.value, UserRole.MANAGER.value])
    async def test_a_physical_store_satisfies_the_rule(self, client, act_as, monkeypatch, role):
        act_as(role)
        store = physical_store()
        _directory(monkeypatch, store)
        monkeypatch.setattr(invoices_module.UploadService, "handle_upload", _async(_sentinel))
        response = await client.post("/api/v1/invoices/process", files={"file": PDF}, data={"store_id": str(store.id)})
        assert response.json()["error"]["detail"] == {"reached": SENTINEL}

    @pytest.mark.parametrize("role", [UserRole.USER.value, UserRole.MANAGER.value, UserRole.ADMIN.value])
    @pytest.mark.parametrize("code", ["47708760", "99990001"])
    async def test_a_source_identity_never_satisfies_it(self, client, act_as, monkeypatch, role, code):
        act_as(role)
        store = source_identity(code)
        _directory(monkeypatch, store)
        touched = []
        monkeypatch.setattr(invoices_module.UploadService, "handle_upload", _async(lambda *a: touched.append(a)))
        response = await client.post("/api/v1/invoices/process", files={"file": PDF}, data={"store_id": str(store.id)})
        assert response.status_code == 422
        assert response.json()["error"]["detail"]["reason"] == "source_identity"
        assert touched == []

    def test_the_rule_itself(self):
        require_store_choice(UserRole.ADMIN.value, None)
        require_store_choice(UserRole.USER.value, str(uuid.uuid4()))
        for role in (UserRole.USER.value, UserRole.MANAGER.value, "UNKNOWN"):
            with pytest.raises(ValidationError):
                require_store_choice(role, " ")


# ---- A2: deferral is ADMIN-only; a MANAGER confirms a store -------------------

class TestStoreResolutionAfterAPause:
    def _paused(self, monkeypatch, *, set_status=None):
        document = SimpleNamespace(id=uuid.uuid4(), status=DocumentStatus.STORE_CONFIRMATION_REQUIRED,
                                   store_id="unset", store_candidates=[])

        async def get(self, document_id):
            return document if document_id == document.id else None

        monkeypatch.setattr(documents_module.DocumentRepository, "get", get)
        monkeypatch.setattr(documents_module.DocumentRepository, "set_status", set_status or _async(_sentinel))
        return document

    @pytest.mark.parametrize("role", [UserRole.USER.value, UserRole.MANAGER.value])
    async def test_user_and_manager_cannot_continue_without_a_store(self, client, act_as, monkeypatch, role):
        act_as(role)
        document = self._paused(monkeypatch)
        response = await client.post(f"/api/v1/documents/{document.id}/defer-store", json={"deferred_by": "x"})
        assert response.status_code == 403
        assert document.store_id == "unset" and document.status == DocumentStatus.STORE_CONFIRMATION_REQUIRED

    async def test_an_admin_may_defer_and_resolve_later(self, client, act_as, monkeypatch):
        act_as(UserRole.ADMIN.value)
        document = self._paused(monkeypatch)
        response = await client.post(f"/api/v1/documents/{document.id}/defer-store", json={"deferred_by": "admin"})
        assert response.json()["error"]["detail"] == {"reached": SENTINEL}
        assert document.store_id is None

    async def test_a_manager_confirms_a_physical_store_and_the_session_account_is_recorded(
        self, client, act_as, monkeypatch,
    ):
        manager = act_as(UserRole.MANAGER.value)
        store = physical_store()
        _directory(monkeypatch, store)
        document = self._paused(monkeypatch, set_status=_async(lambda *a: None))
        logged = []

        async def add(self, **kwargs):
            logged.append(kwargs)
            _sentinel()

        monkeypatch.setattr(documents_module.ProcessingLogRepository, "add", add)
        response = await client.post(f"/api/v1/documents/{document.id}/confirm-store",
                                     json={"store_id": str(store.id), "confirmed_by": "someone-else"})
        assert response.json()["error"]["detail"] == {"reached": SENTINEL}
        assert document.store_id == store.id
        payload = logged[0]["payload"]
        assert (payload["actor_user_id"], payload["actor_username"], payload["actor_role"]) == (
            str(manager.id), manager.username, "MANAGER")
        assert payload["confirmed_by"] == "someone-else", "the typed name is kept, not trusted"

    async def test_a_manager_cannot_confirm_a_source_identity(self, client, act_as, monkeypatch):
        act_as(UserRole.MANAGER.value)
        store = source_identity()
        _directory(monkeypatch, store)
        document = self._paused(monkeypatch)
        response = await client.post(f"/api/v1/documents/{document.id}/confirm-store",
                                     json={"store_id": str(store.id)})
        assert response.status_code == 422
        assert response.json()["error"]["detail"]["reason"] == "source_identity"
        assert document.store_id == "unset"


# ---- A3: reprocessing keeps a confirmed vendor -------------------------------

def _vendor(name: str, status: str = VENDOR_UNRESOLVED, display_name: str | None = None) -> Vendor:
    return Vendor(id=uuid.uuid4(), name=name, tax_id=None, identity_status=status, display_name=display_name)


class _VendorWorld:
    def __init__(self, *vendors: Vendor):
        self.by_id = {v.id: v for v in vendors}
        self.created: list[str] = []
        self.enriched: list[str] = []

    def session(self):
        world = self

        class Session:
            async def get(self, model, key):
                assert model is Vendor
                return world.by_id.get(key)

        return Session()

    def repository(self, _session):
        world = self

        class Repository:
            async def find_existing(self, normalized):
                return next((v for v in world.by_id.values() if v.name == normalized.vendor_name), None) \
                    if normalized.vendor_name else None

            async def get_or_create(self, normalized):
                if normalized.vendor_name is None:
                    return None, False
                found = await self.find_existing(normalized)
                if found is not None:
                    world.enriched.append(found.name)
                    return found, False
                made = _vendor(normalized.vendor_name)
                world.by_id[made.id] = made
                world.created.append(made.name)
                return made, True

        return Repository()


def reading(name: str | None, tax_id: str | None = None):
    return SimpleNamespace(vendor_name=name, vendor_tax_id=tax_id)


class TestReprocessingKeepsAConfirmedVendor:
    async def _settle(self, monkeypatch, world, invoice, normalized):
        monkeypatch.setattr(reprocess_service, "VendorRepository", world.repository)
        return await reprocess_service.settle_reprocess_vendor(world.session(), invoice, normalized)

    async def test_the_same_reading_keeps_it_with_no_discrepancy(self, monkeypatch):
        confirmed = _vendor("TESTANI DISTRIBUTORS INC.", VENDOR_CONFIRMED, "Rocco J. Testani")
        world = _VendorWorld(confirmed)
        settled = await self._settle(monkeypatch, world, SimpleNamespace(vendor_id=confirmed.id),
                                     reading("TESTANI DISTRIBUTORS INC."))
        assert (settled.vendor_id, settled.discrepancy) == (confirmed.id, None)
        assert world.enriched == ["TESTANI DISTRIBUTORS INC."], "contact fields still filled, as always"

    async def test_another_existing_vendor_is_recorded_not_switched_to(self, monkeypatch):
        confirmed = _vendor("TESTANI DISTRIBUTORS INC.", VENDOR_CONFIRMED, "Rocco J. Testani")
        other = _vendor("ACME BEVERAGE")
        world = _VendorWorld(confirmed, other)
        settled = await self._settle(monkeypatch, world, SimpleNamespace(vendor_id=confirmed.id),
                                     reading("ACME BEVERAGE", "99-1"))
        assert settled.vendor_id == confirmed.id
        assert settled.discrepancy == {
            "event": "vendor_identity_discrepancy", "kept_vendor_id": str(confirmed.id),
            "kept_vendor_label": "Rocco J. Testani", "observed_vendor_id": str(other.id),
            "observed_vendor_name": "ACME BEVERAGE", "observed_vendor_tax_id": "99-1",
        }

    @pytest.mark.parametrize("name", ["TESTANI DIST. (NEW WORDING)", None])
    async def test_an_unknown_or_missing_vendor_creates_nothing(self, monkeypatch, name):
        confirmed = _vendor("TESTANI DISTRIBUTORS INC.", VENDOR_CONFIRMED, "Rocco J. Testani")
        world = _VendorWorld(confirmed)
        settled = await self._settle(monkeypatch, world, SimpleNamespace(vendor_id=confirmed.id), reading(name))
        assert settled.vendor_id == confirmed.id
        assert settled.discrepancy["observed_vendor_id"] is None
        assert settled.discrepancy["observed_vendor_name"] == name
        assert world.created == [] and len(world.by_id) == 1, "no vendor made, merged or re-keyed"

    async def test_an_unconfirmed_vendor_is_rematched_exactly_as_before(self, monkeypatch):
        unresolved = _vendor("TESTANI")
        other = _vendor("ACME BEVERAGE")
        world = _VendorWorld(unresolved, other)
        settled = await self._settle(monkeypatch, world, SimpleNamespace(vendor_id=unresolved.id),
                                     reading("ACME BEVERAGE"))
        assert (settled.vendor_id, settled.discrepancy) == (other.id, None)

    async def test_an_invoice_without_a_vendor_gets_the_governed_match(self, monkeypatch):
        world = _VendorWorld()
        settled = await self._settle(monkeypatch, world, SimpleNamespace(vendor_id=None), reading("NEW VENDOR"))
        assert world.created == ["NEW VENDOR"] and settled.vendor_id is not None

    def test_reprocess_uses_the_rule_logs_the_discrepancy_and_never_decides_identity(self):
        source = (ROOT / "app/services/reprocess_service.py").read_text()
        body = source.split("async def reprocess_document")[1]
        assert "settle_reprocess_vendor(" in body and "get_or_create(" not in body
        assert '"vendor_identity_discrepancy"' in source and "settled.discrepancy" in body
        tree = ast.parse(source)
        written = {t.attr for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   for t in node.targets if isinstance(t, ast.Attribute)}
        assert not written & {"identity_status", "display_name"}, "reprocessing never confirms or names a vendor"


# ---- A4: the receiving-eligibility contract ----------------------------------

class TestReceivingEligibility:
    def _case(self, status="VALIDATED", store: Store | None = None, document="COMPLETED", store_id=...):
        store = store if store is not None else physical_store()
        invoice = SimpleNamespace(status=status, store_id=store.id if store_id is ... else store_id)
        return invoice, store, SimpleNamespace(status=document)

    def test_a_validated_invoice_for_a_physical_store_is_eligible(self):
        result = rx.receiving_eligibility(*self._case())
        assert (result.eligible, result.reasons) == (True, [])

    @pytest.mark.parametrize(("case", "reasons"), [
        ({"status": "REVIEW_REQUIRED"}, [rx.NOT_VALIDATED]),
        ({"status": "EXTRACTED"}, [rx.NOT_VALIDATED]),
        ({"document": "BINNED"}, [rx.DOCUMENT_WITHDRAWN]),
        ({"document": "STOPPED"}, [rx.DOCUMENT_WITHDRAWN]),
        ({"store_id": None}, [rx.STORE_PENDING]),
        ({"store": source_identity()}, [rx.NOT_PHYSICAL_STORE]),
        ({"store": source_identity("12345678")}, [rx.NOT_PHYSICAL_STORE]),
        ({"store_id": uuid.uuid4()}, [rx.STORE_NOT_LOADED]),
        ({"status": "REVIEW_REQUIRED", "document": "BINNED", "store_id": None},
         [rx.NOT_VALIDATED, rx.STORE_PENDING, rx.DOCUMENT_WITHDRAWN]),
    ])
    def test_every_failed_condition_is_reported(self, case, reasons):
        result = rx.receiving_eligibility(*self._case(**case))
        assert (result.eligible, result.reasons) == (False, reasons)

    def test_a_binned_validated_invoice_keeps_its_status_yet_is_not_eligible(self):
        invoice, store, document = self._case(document=DocumentStatus.BINNED.value)
        assert invoice.status == "VALIDATED"
        assert not rx.receiving_eligibility(invoice, store, document).eligible

    def test_it_is_a_contract_nothing_calls_yet(self):
        callers = [str(p.relative_to(ROOT)) for p in (ROOT / "app").rglob("*.py")
                   if p.name != "receiving_eligibility.py" and "receiving_eligibility" in p.read_text()]
        assert callers == []


# ---- A6: every store write path is known and guarded -------------------------

ALLOWED_STORE_WRITES = {
    ("app/api/v1/documents.py", "confirm_store"),          # after ensure_physical_store
    ("app/api/v1/documents.py", "defer_store"),            # clears it — ADMIN only
    ("app/api/v1/invoices.py", "assign_store"),            # after ensure_physical_store
    ("app/services/pipeline_service.py", "intake_pages"),  # the operator's choice, via resolve_chosen_store
    ("app/services/pipeline_service.py", "_settle_store"), # the same choice, when the document agrees
    ("app/repositories/invoice_repository.py", "create_with_items"),  # persists the document's store
    ("app/services/persistence_service.py", "persist_invoice"),       # passes the document's store on
}


def _store_writes() -> set[tuple[str, str]]:
    found = set()
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text())
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

        def enclosing(node, parents=parents):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node.name
            return "<module>"

        rel = str(path.relative_to(ROOT))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "store_id":
                        owner = ast.unparse(target.value)
                        if owner in {"invoice", "document"} or owner.endswith(".document"):
                            found.add((rel, enclosing(node)))
            elif isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if any(k.arg == "store_id" for k in node.keywords) and (
                        name in {"Invoice", "Document"} or name.endswith("create_with_items")):
                    found.add((rel, enclosing(node)))
    return found


def _function(path: str, name: str) -> ast.AST:
    tree = ast.parse((ROOT / path).read_text())
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _calls(node: ast.AST) -> set[str]:
    return {ast.unparse(n.func).split(".")[-1] for n in ast.walk(node) if isinstance(n, ast.Call)}


class TestStoreWritePaths:
    def test_every_invoice_or_document_store_write_is_a_known_path(self):
        assert _store_writes() == ALLOWED_STORE_WRITES

    def test_the_entry_points_apply_the_physical_store_guard(self):
        assert "ensure_physical_store" in _calls(_function("app/api/v1/documents.py", "confirm_store"))
        assert "ensure_physical_store" in _calls(_function("app/api/v1/invoices.py", "assign_store"))
        assert "ensure_physical_store" in _calls(_function("app/api/v1/invoices.py", "resolve_chosen_store"))
        process = _calls(_function("app/api/v1/invoices.py", "process_invoice"))
        assert {"resolve_chosen_store", "require_store_choice"} <= process

    def test_deferral_is_admin_only_and_only_clears_the_store(self):
        defer = _function("app/api/v1/documents.py", "defer_store")
        assert "require_admin" in ast.unparse(defer.args)
        values = {ast.unparse(n.value) for n in ast.walk(defer) if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Attribute) and t.attr == "store_id"}
        assert values == {"None"}


# ---- A5: migration 0029 ------------------------------------------------------

class TestTheIndexes:
    def test_0029_adds_exactly_the_two_date_indexes_after_the_deployed_head(self):
        path = ROOT / "alembic/versions/20260930_0029_invoice_store_vendor_date_indexes.py"
        spec = importlib.util.spec_from_file_location("m0029", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert (module.revision, module.down_revision) == ("0029", "0028")
        upgrade = path.read_text().split("def upgrade")[1].split("def downgrade")[0]
        assert upgrade.count("op.create_index(") == 2
        assert '"idx_invoices_store_date", "invoices", ["store_id", "invoice_date"]' in upgrade
        assert '"idx_invoices_vendor_date", "invoices", ["vendor_id", "invoice_date"]' in upgrade
        for forbidden in ("drop_", "add_column", "alter_column", "execute(", "create_unique", "UPDATE", "DELETE"):
            assert forbidden not in upgrade, forbidden

    def test_the_model_declares_them(self):
        from app.models.invoice import Invoice

        indexes = {i.name: [c.name for c in i.columns] for i in Invoice.__table__.indexes}
        assert indexes["idx_invoices_store_date"] == ["store_id", "invoice_date"]
        assert indexes["idx_invoices_vendor_date"] == ["vendor_id", "invoice_date"]
        assert indexes["idx_invoices_store"] == ["store_id"], "the existing index stays"
