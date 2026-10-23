"""
tests/test_store_access.py — per-user physical-store access for inventory (no DB).

The rule: an account uses inventory for a store only while it is active,
MANAGER or above, the store is physical, and it holds an active grant. There
is no implicit all-store access for any role. Refusals never reveal whether a
store exists. Grants and revocations are made by an active ADMIN with a reason
and kept as immutable history.

What needs Postgres (grant rows, locks, the partial unique index, revocation
taking effect on the next request, invoice-linked refusals writing nothing) is
in tests/integration/test_store_access_db.py.
"""

from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.exceptions import PermissionDeniedError, RecordNotFoundError, ValidationError
from app.models.store import KIND_PHYSICAL, KIND_SOURCE_IDENTITY
from app.models.user_store_access import (
    REVOCATION_FIELDS,
    ImmutableGrantError,
    UserStoreAccess,
    _grants_are_append_only,
    _grants_are_never_deleted,
)
from app.services import store_access as sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "alembic/versions/20261023_0034_user_store_access.py"
STORE_ID = uuid.uuid4()


def account(role="MANAGER", active=True, name="barj"):
    return SimpleNamespace(id=uuid.uuid4(), username=name, role=role, is_active=active)


class TestTheRoleRule:
    @pytest.mark.parametrize(("role", "active", "allowed"), [
        ("ADMIN", True, True), ("MANAGER", True, True), ("USER", True, False),
        ("ADMIN", False, False), ("MANAGER", False, False), ("DEVELOPER", True, False), (None, True, False),
    ])
    def test_only_active_managers_and_administrators_have_inventory(self, role, active, allowed):
        assert sa.has_inventory_role(account(role, active)) is allowed


class _Lookups:
    """Stands in for the grant query and the store repository, and records what was looked up."""

    def __init__(self, monkeypatch, *, grant=True, store_kind=KIND_PHYSICAL, store_exists=True):
        self.grant_lookups, self.store_lookups = [], []
        store = SimpleNamespace(id=STORE_ID, kind=store_kind, label="AF Fixture Store")

        async def active_grant(_session, user_id, store_id):
            self.grant_lookups.append((user_id, store_id))
            return object() if grant else None

        lookups = self

        class Stores:
            def __init__(self, _session):
                pass

            async def get(self, store_id):
                lookups.store_lookups.append(store_id)
                return store if store_exists else None

        monkeypatch.setattr(sa, "_active_grant", active_grant)
        monkeypatch.setattr(sa, "StoreRepository", Stores)
        self.store = store


class TestEnsureStoreAccess:
    async def test_a_granted_manager_gets_the_physical_store(self, monkeypatch):
        lookups = _Lookups(monkeypatch)
        assert await sa.ensure_store_access(object(), account("MANAGER"), STORE_ID) is lookups.store

    async def test_a_granted_administrator_gets_it_too_and_an_ungranted_one_does_not(self, monkeypatch):
        _Lookups(monkeypatch)
        assert (await sa.ensure_store_access(object(), account("ADMIN"), STORE_ID)).id == STORE_ID
        lookups = _Lookups(monkeypatch, grant=False)
        with pytest.raises(RecordNotFoundError):
            await sa.ensure_store_access(object(), account("ADMIN"), STORE_ID)
        assert lookups.store_lookups == []          # without a grant the store is never even loaded

    @pytest.mark.parametrize("case", ["no_grant", "source_identity", "unknown_store"])
    async def test_every_refusal_is_the_same_404_and_reveals_nothing(self, monkeypatch, case):
        _Lookups(monkeypatch, grant=case != "no_grant",
                 store_kind=KIND_SOURCE_IDENTITY if case == "source_identity" else KIND_PHYSICAL,
                 store_exists=case != "unknown_store")
        with pytest.raises(RecordNotFoundError) as refused:
            await sa.ensure_store_access(object(), account("MANAGER"), STORE_ID)
        assert refused.value.to_dict() == sa.store_not_found(STORE_ID).to_dict()
        assert refused.value.to_dict() == {"error_code": "ERR_NOT_FOUND", "message": "Store not found.",
                                           "detail": {"store_id": str(STORE_ID)}}

    @pytest.mark.parametrize("user", [account("USER"), account("ADMIN", active=False), account("MANAGER", False)])
    async def test_a_user_or_inactive_account_is_refused_before_anything_is_looked_up(self, monkeypatch, user):
        lookups = _Lookups(monkeypatch)                       # even with a grant on record
        with pytest.raises(PermissionDeniedError):
            await sa.ensure_store_access(object(), user, STORE_ID)
        assert lookups.grant_lookups == [] and lookups.store_lookups == []

    async def test_has_store_access_answers_without_raising(self, monkeypatch):
        _Lookups(monkeypatch)
        assert await sa.has_store_access(object(), account("MANAGER"), STORE_ID) is True
        assert await sa.has_store_access(object(), account("USER"), STORE_ID) is False
        assert await sa.has_store_access(object(), account("MANAGER"), None) is False
        _Lookups(monkeypatch, grant=False)
        assert await sa.has_store_access(object(), account("ADMIN"), STORE_ID) is False
        _Lookups(monkeypatch, store_kind=KIND_SOURCE_IDENTITY)
        assert await sa.has_store_access(object(), account("ADMIN"), STORE_ID) is False

    async def test_accessible_stores_is_empty_below_manager(self, monkeypatch):
        lookups = _Lookups(monkeypatch)
        assert await sa.accessible_stores(object(), account("USER")) == []
        assert lookups.grant_lookups == []


class _NoDatabase:
    """Any database use fails the test: the refusals below must come first."""

    def __getattr__(self, name):
        raise AssertionError(f"the database was used ({name}) before the request was refused")


class TestAdministrationRefusesFirst:
    @pytest.mark.parametrize("actor", [account("MANAGER"), account("USER"), account("ADMIN", active=False)])
    async def test_only_an_active_administrator_grants_or_revokes(self, actor):
        for change in (sa.grant_store_access, sa.revoke_store_access):
            with pytest.raises(PermissionDeniedError):
                await change(_NoDatabase(), user_id=uuid.uuid4(), store_id=STORE_ID, actor=actor, reason="pilot")

    @pytest.mark.parametrize("reason", [None, "", "   ", "x" * 501])
    async def test_a_reason_is_required_and_bounded(self, reason):
        for change in (sa.grant_store_access, sa.revoke_store_access):
            with pytest.raises(ValidationError):
                await change(_NoDatabase(), user_id=uuid.uuid4(), store_id=STORE_ID, actor=account("ADMIN"),
                             reason=reason)


class TestTheGrantModel:
    def test_only_the_revocation_fields_may_ever_change(self):
        expected = {"revoked_at", "revoked_by_user_id", "revoked_by", "revoked_by_role", "revoke_reason"}
        assert expected == REVOCATION_FIELDS
        columns = set(UserStoreAccess.__table__.columns.keys())
        assert columns > REVOCATION_FIELDS
        assert {"user_id", "username", "store_id", "granted_at", "granted_by_user_id", "granted_by",
                "granted_by_role", "grant_reason"} <= columns

    @staticmethod
    def _connection(stored_revoked_at):
        """The flush's connection: answers the guard's read of the stored row's revoked_at."""
        return SimpleNamespace(execute=lambda _stmt: SimpleNamespace(scalar=lambda: stored_revoked_at))

    def test_a_stored_revocation_refuses_any_change_even_to_another_revocation_field(self):
        from datetime import UTC, datetime

        row = UserStoreAccess(revoke_reason="rewritten")      # only a revocation field set (id stays unchanged)
        with pytest.raises(ImmutableGrantError, match="revoked"):
            _grants_are_append_only(None, self._connection(datetime.now(UTC)), row)

    def test_an_active_grant_may_take_only_the_revocation_fields(self):
        from datetime import UTC, datetime

        revoking = UserStoreAccess(revoked_at=datetime.now(UTC), revoked_by="admin",
                                   revoked_by_role="ADMIN", revoke_reason="cover ended")
        _grants_are_append_only(None, self._connection(None), revoking)             # allowed
        editing = UserStoreAccess(grant_reason="changed my mind")
        with pytest.raises(ImmutableGrantError, match="Only a revocation"):
            _grants_are_append_only(None, self._connection(None), editing)

    def test_a_grant_is_never_deleted(self):
        with pytest.raises(ImmutableGrantError, match="never deleted"):
            _grants_are_never_deleted(None, None, UserStoreAccess(id=uuid.uuid4()))

    def test_the_foreign_keys_keep_history(self):
        fks = {fk.parent.name: fk.ondelete for fk in UserStoreAccess.__table__.foreign_keys}
        assert fks == {"user_id": "RESTRICT", "store_id": "RESTRICT",
                       "granted_by_user_id": "SET NULL", "revoked_by_user_id": "SET NULL"}


class TestTheMigration:
    def test_0034_chains_from_0033_and_is_additive(self):
        source = MIGRATION.read_text()
        assert 'revision = "0034"' in source and 'down_revision = "0033"' in source
        assert source.count("op.create_table(") == 1
        for forbidden in ("op.alter_column", "op.drop_column", "UPDATE ", "DELETE ", "INSERT ",
                          "FORCE ROW LEVEL SECURITY", "CREATE POLICY"):
            assert forbidden not in source, forbidden
        assert 'op.execute("ALTER TABLE user_store_access ENABLE ROW LEVEL SECURITY")' in source

    def test_the_migration_matches_the_model_column_for_column(self):
        tree = ast.parse(MIGRATION.read_text())
        tables: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "create_table":
                tables[node.args[0].value] = {a.args[0].value for a in node.args[1:]
                                              if isinstance(a, ast.Call) and getattr(a.func, "attr", None) == "Column"}
        assert tables == {"user_store_access": set(UserStoreAccess.__table__.columns.keys())}

    def test_the_constraints_that_keep_history_are_in_both(self):
        source = MIGRATION.read_text()
        model = {c.name for c in UserStoreAccess.__table__.constraints if c.name} | {
            i.name for i in UserStoreAccess.__table__.indexes}
        for name in ("uq_user_store_access_active", "ck_user_store_access_revocation",
                     "ck_user_store_access_grant_reason", "idx_user_store_access_user", "idx_user_store_access_store"):
            assert name in source and name in model, name

    def test_the_migration_grants_nothing(self):
        assert "bulk_insert" not in MIGRATION.read_text()


class TestEveryInventoryPathIsAuthorized:
    """Structural guarantees, so a new route or service path cannot skip the check unnoticed."""

    API = (ROOT / "app/api/v1/inventory.py").read_text()
    SERVICE = (ROOT / "app/services/inventory_service.py").read_text()

    def _routes(self):
        return re.findall(r'@router\.(get|post)\("([^"]+)"[\s\S]*?\nasync def (\w+)\(([\s\S]*?)\n(?=@|\Z)',
                          self.API + "\n")

    def test_no_route_uses_the_old_role_only_store_lookup(self):
        assert "physical_store(" not in self.API and "def physical_store" not in self.SERVICE

    def test_every_store_route_checks_access_and_every_invoice_route_passes_the_account(self):
        routes = self._routes()
        assert len(routes) == 9, [r[1] for r in routes]
        for _verb, path, name, body in routes:
            if "{store_id}" in path and not path.endswith("/opening-counts"):
                assert "store_access.ensure_store_access(db, user, store_id)" in body, name
            elif path.endswith("/opening-counts"):
                assert "user=user" in body, name          # authorized in record_opening_counts
            elif "{invoice_id}" in path:
                # The service authorizes the invoice's (or receipt's) own store for this account.
                assert "_preview_out(db, invoice_id, user)" in body or "user=user" in body, name
            else:
                assert path == "/inventory/stores" and "store_access.accessible_stores(db, user)" in body

    @pytest.mark.parametrize(("function", "check"), [
        ("receipt_preview", "store_access.has_store_access(session, user, invoice.store_id)"),
        ("reverse_receipt", "store_access.has_store_access(session, user, receipt.store_id)"),
        ("record_opening_counts", "store_access.ensure_store_access(session, user, store_id)"),
    ])
    def test_the_service_authorizes_each_store_it_writes_or_reads(self, function, check):
        body = self.SERVICE.split(f"async def {function}(")[1].split("\nasync def ")[0]
        assert check in body, function

    def test_posting_authorizes_after_taking_the_invoice_lock(self):
        body = self.SERVICE.split("async def post_receipt(")[1].split("\nasync def ")[0]
        assert body.index("await lock_invoice(session, invoice_id)") < body.index(
            "await receipt_preview(session, invoice_id, user)")

    def test_reversal_authorizes_after_taking_the_invoice_lock(self):
        body = self.SERVICE.split("async def reverse_receipt(")[1].split("\nasync def ")[0]
        assert body.index("await lock_invoice(session, invoice_id)") < body.index("has_store_access(")
