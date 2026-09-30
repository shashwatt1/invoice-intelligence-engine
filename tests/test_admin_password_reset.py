"""
tests/test_admin_password_reset.py — an ADMIN resets another account's password.

Only the password changes; who acted is the authenticated session; the reset
is recorded without the password; nothing about the password is ever echoed
in a response, an audit record or a log line. Offline: accounts live in
memory, hashes are real bcrypt, and login goes through the real endpoint.
"""

from __future__ import annotations

import importlib.util
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.dependencies import require_authenticated_user
from app.core.security import hash_password, verify_password
from app.database.session import get_db
from app.models.user import SECURITY_EVENT_PASSWORD_RESET, User, UserRole, UserSecurityEvent

ROOT = Path(__file__).resolve().parent.parent
OLD = "old-password-1"
NEW = "brand-new-secret-9"


def account(username: str, role: str, *, active: bool = True, password: str = OLD) -> User:
    user = User(id=uuid.uuid4(), username=username, password_hash=hash_password(password), role=role,
                is_active=active)
    user.created_at = user.updated_at = datetime(2026, 9, 1, tzinfo=UTC)
    return user


class Accounts:
    """The in-memory stand-in for the users table and its security events."""

    def __init__(self, *users: User):
        self.by_id = {u.id: u for u in users}
        self.events: list[UserSecurityEvent] = []

    def repository(self, _session):
        accounts = self

        class Repository:
            async def get(self, user_id):
                return accounts.by_id.get(user_id)

            async def get_by_username(self, username):
                return next((u for u in accounts.by_id.values() if u.username == username.strip().lower()), None)

            async def set_password_hash(self, user, password_hash):
                user.password_hash = password_hash

            async def record_security_event(self, event):
                accounts.events.append(event)
                return event

        return Repository()


class _Session:
    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None


@pytest.fixture
def world(app, monkeypatch):
    """shashwatt1 (ADMIN), barj (MANAGER), vivek (USER); act as any of them."""
    import app.api.v1.auth as auth_module
    import app.api.v1.users as users_module

    admin, manager, user = (account("shashwatt1", UserRole.ADMIN.value), account("barj", UserRole.MANAGER.value),
                            account("vivek", UserRole.USER.value))
    accounts = Accounts(admin, manager, user)
    monkeypatch.setattr(users_module, "UserRepository", accounts.repository)
    monkeypatch.setattr(auth_module, "UserRepository", accounts.repository)

    async def fake_db():
        yield _Session()

    app.dependency_overrides[get_db] = fake_db

    def act_as(actor: User) -> None:
        app.dependency_overrides[require_authenticated_user] = lambda: actor

    act_as(admin)
    yield type("World", (), {"admin": admin, "manager": manager, "user": user, "accounts": accounts,
                             "act_as": staticmethod(act_as)})
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_authenticated_user, None)


def reset(client, target: User, new: str | None = NEW, confirm: str | None = NEW, **extra):
    body = {k: v for k, v in {"new_password": new, "confirm_password": confirm}.items() if v is not None}
    return client.post(f"/api/v1/users/{target.id}/reset-password", json={**body, **extra})


class TestAnAdminResetsAnotherAccount:
    async def test_only_the_password_changes_and_it_is_stored_as_a_bcrypt_hash(self, client, world):
        target = world.user
        before = (target.id, target.username, target.role, target.is_active, target.created_at)
        old_hash = target.password_hash
        response = await reset(client, target)
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert "password" not in response.text and old_hash not in response.text
        assert (body["id"], body["username"], body["role"], body["is_active"]) == (
            str(target.id), "vivek", "USER", True)
        assert (target.id, target.username, target.role, target.is_active, target.created_at) == before
        assert target.password_hash != old_hash and target.password_hash != NEW
        assert target.password_hash.startswith("$2")
        assert verify_password(NEW, target.password_hash) and not verify_password(OLD, target.password_hash)

    async def test_the_new_password_signs_in_and_the_old_one_no_longer_does(self, client, world):
        await reset(client, world.manager)
        old = await client.post("/api/v1/auth/login", json={"username": "barj", "password": OLD})
        new = await client.post("/api/v1/auth/login", json={"username": "barj", "password": NEW})
        assert old.status_code == 401
        assert new.status_code == 200 and new.json()["data"]["role"] == "MANAGER"

    async def test_an_inactive_account_stays_inactive(self, client, world):
        target = account("former", UserRole.USER.value, active=False)
        world.accounts.by_id[target.id] = target
        assert (await reset(client, target)).status_code == 200
        assert target.is_active is False and verify_password(NEW, target.password_hash)


class TestAuthorization:
    @pytest.mark.parametrize("acting", ["user", "manager"])
    async def test_a_user_or_manager_is_refused_and_nothing_changes(self, client, world, acting):
        world.act_as(getattr(world, acting))
        target = world.admin if acting == "user" else world.user
        old_hash = target.password_hash
        response = await reset(client, target)
        assert response.status_code == 403
        assert target.password_hash == old_hash and world.accounts.events == []

    async def test_an_admin_cannot_reset_their_own_password_here(self, client, world):
        old_hash = world.admin.password_hash
        response = await reset(client, world.admin)
        assert response.status_code == 422
        assert response.json()["error"]["detail"] == {"field": "user_id", "reason": "own_account"}
        assert world.admin.password_hash == old_hash and world.accounts.events == []

    async def test_an_unknown_account_is_not_found(self, client, world):
        ghost = account("ghost", UserRole.USER.value)
        response = await reset(client, ghost)
        assert response.status_code == 404
        assert world.accounts.events == []


class TestTheResetIsRecordedWithoutThePassword:
    async def test_the_session_admin_is_recorded_whatever_the_request_claims(self, client, world):
        response = await reset(client, world.user, admin_id=str(uuid.uuid4()), admin_username="barj",
                               admin_role="USER", actor="barj", reviewer="barj", confirmed_by="barj")
        assert response.status_code == 200
        [event] = world.accounts.events
        assert (event.action, event.target_user_id, event.target_username) == (
            SECURITY_EVENT_PASSWORD_RESET, world.user.id, "vivek")
        assert (event.actor_user_id, event.actor_username, event.actor_role) == (
            world.admin.id, "shashwatt1", "ADMIN")

    async def test_no_password_or_hash_is_kept_in_the_event(self, client, world):
        await reset(client, world.user)
        [event] = world.accounts.events
        columns = {c.name for c in UserSecurityEvent.__table__.columns}
        assert not any("password" in name or "hash" in name for name in columns)
        values = [str(getattr(event, name, "")) for name in columns]
        assert not any(NEW in v or world.user.password_hash in v for v in values)

    async def test_the_log_line_names_the_accounts_and_never_the_password(self, client, world, caplog, capsys):
        with caplog.at_level(logging.INFO):
            await reset(client, world.user)
        logged = caplog.text + capsys.readouterr().out
        assert "user_password_reset" in logged and "shashwatt1" in logged
        assert NEW not in logged and world.user.password_hash not in logged


class TestPasswordValidation:
    @pytest.mark.parametrize(("new", "confirm", "field", "reason"), [
        (None, None, "new_password", "required"),
        ("", "", "new_password", "required"),
        ("        ", "        ", "new_password", "required"),
        ("short-7", "short-7", "new_password", "too_short"),
        ("x" * 201, "x" * 201, "new_password", "too_long"),
        (NEW, None, "confirm_password", "mismatch"),
        (NEW, NEW + "x", "confirm_password", "mismatch"),
    ])
    async def test_an_invalid_password_is_refused_without_echoing_it(
        self, client, world, caplog, capsys, new, confirm, field, reason,
    ):
        old_hash = world.user.password_hash
        with caplog.at_level(logging.INFO):
            response = await reset(client, world.user, new=new, confirm=confirm)
        assert response.status_code == 422
        assert response.json()["error"]["detail"] == {"field": field, "reason": reason}
        logged = caplog.text + capsys.readouterr().out
        for secret in {v for v in (new, confirm) if v and v.strip()}:
            assert secret not in response.text and secret not in logged
        assert world.user.password_hash == old_hash and world.accounts.events == []

    async def test_a_non_text_password_is_refused_without_echoing_it(self, client, world):
        response = await client.post(f"/api/v1/users/{world.user.id}/reset-password",
                                     json={"new_password": 123456789012, "confirm_password": 123456789012})
        assert response.status_code == 422
        assert "123456789012" not in response.text

    def test_the_reset_policy_is_the_account_creation_policy(self):
        from app.schemas.auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, CreateUserRequest

        field = CreateUserRequest.model_fields["password"]
        bounds = {type(m).__name__: m for m in field.metadata}
        assert (PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH) == (8, 200)
        assert bounds["MinLen"].min_length == PASSWORD_MIN_LENGTH
        assert bounds["MaxLen"].max_length == PASSWORD_MAX_LENGTH


class TestTheMigration:
    def test_0028_is_one_additive_table_after_the_deployed_head(self):
        path = ROOT / "alembic/versions/20260930_0028_user_security_events.py"
        spec = importlib.util.spec_from_file_location("m0028", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert (module.revision, module.down_revision) == ("0028", "0027")
        upgrade = path.read_text().split("def upgrade")[1].split("def downgrade")[0]
        assert "create_table(\n        \"user_security_events\"" in upgrade
        for forbidden in ("drop_", "add_column", "alter_column", "DELETE", "UPDATE", "execute(", "password"):
            assert forbidden not in upgrade, forbidden
