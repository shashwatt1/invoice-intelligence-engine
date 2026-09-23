"""
tests/integration/test_auth.py — accounts, username login, authorization
matrix, and response redaction, against real Postgres.

Covers P3's ACCOUNT, AUTHORIZATION, DATA LEAKAGE and ADMIN
user-management test groups, now under username authentication
(this-phase items A-Q from the username-auth conversion): valid login,
wrong password, unknown username, inactive account, username
normalization, duplicate rejection, JWT subject identity, and role
read from the database rather than the token or the client. Password
hashing and JWT mechanics themselves are covered offline in
tests/test_security.py — this file is about what the real
login/authorization/redaction endpoints do.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import get_settings
from app.core.security import ALGORITHM
from app.repositories.user_repository import UserRepository
from tests.integration.conftest import (
    ADMIN_USERNAME,
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    USER_PASSWORD,
    USER_USERNAME,
    requires_db,
    user_id,
)
from tests.integration.test_api_db import (  # noqa: F401 — fixture reuse
    INVOICE_PDF,
    STORE,
    api_client,
    process_file,
)

pytestmark = requires_db


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _login(client, username, password):
    return await client.post("/api/v1/auth/login", json={"username": username, "password": password})


class TestAccountCreationAndLogin:
    """A/B/C/D/F/H: create account, valid/invalid login, inactive rejected, duplicate rejected."""

    async def test_admin_can_create_an_account(self, api_client, db_session):  # noqa: F811
        response = await api_client.post(
            "/api/v1/users", json={"username": "new-manager", "password": "NewPass1234", "role": "MANAGER"},
        )
        assert response.status_code == 200, response.text
        body = response.json()["data"]
        assert body["username"] == "new-manager"
        assert body["role"] == "MANAGER"
        assert "password" not in body and "password_hash" not in body

    async def test_a_valid_username_and_password_logs_in(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            response = await _login(client, USER_USERNAME, USER_PASSWORD)
            assert response.status_code == 200
            assert response.json()["data"]["username"] == USER_USERNAME
            assert "access_token" in response.cookies
            assert response.json()["data"].get("password") is None
        finally:
            await client.aclose()

    async def test_wrong_password_is_401(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            response = await _login(client, USER_USERNAME, "totally-wrong-password")
            assert response.status_code == 401
        finally:
            await client.aclose()

    async def test_unknown_username_is_401_with_the_same_message_as_wrong_password(
        self, app, api_client, db_session  # noqa: F811
    ):
        client = await _client(app)
        try:
            unknown = await _login(client, "nobody-registered", "whatever12345")
            wrong = await _login(client, USER_USERNAME, "totally-wrong-password")
            assert unknown.status_code == wrong.status_code == 401
            assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]
        finally:
            await client.aclose()

    async def test_inactive_user_is_rejected_at_login(self, api_client, app, db_session):  # noqa: F811
        target = user_id("USER")
        deactivate = await api_client.patch(f"/api/v1/users/{target}/active", json={"is_active": False})
        assert deactivate.status_code == 200

        client = await _client(app)
        try:
            response = await _login(client, USER_USERNAME, USER_PASSWORD)
            assert response.status_code == 401
        finally:
            await client.aclose()

    async def test_inactive_user_cannot_use_a_protected_api_even_with_a_still_valid_cookie(
        self, api_client, app, db_session  # noqa: F811
    ):
        # Log in first (account still active), THEN deactivate — the
        # existing token must stop working on the very next request,
        # not merely block a fresh login.
        client = await _client(app)
        try:
            login = await _login(client, USER_USERNAME, USER_PASSWORD)
            assert login.status_code == 200

            target = user_id("USER")
            deactivate = await api_client.patch(f"/api/v1/users/{target}/active", json={"is_active": False})
            assert deactivate.status_code == 200

            me = await client.get("/api/v1/auth/me")
            assert me.status_code == 401
        finally:
            await client.aclose()

    async def test_username_normalization_lets_mixed_case_log_in_to_the_same_account(
        self, api_client, app, db_session  # noqa: F811
    ):
        created = await api_client.post(
            "/api/v1/users", json={"username": "MixedCase.User", "password": "MixedCasePass1", "role": "USER"},
        )
        assert created.status_code == 200
        assert created.json()["data"]["username"] == "mixedcase.user"  # stored normalized

        for typed in ("MixedCase.User", "mixedcase.user", "MIXEDCASE.USER"):
            client = await _client(app)
            try:
                response = await _login(client, typed, "MixedCasePass1")
                assert response.status_code == 200, f"{typed!r} should log in to the same account"
                assert response.json()["data"]["id"] == created.json()["data"]["id"]
            finally:
                await client.aclose()

    async def test_duplicate_username_is_rejected_case_insensitively(
        self, api_client, db_session  # noqa: F811
    ):
        for variant in (ADMIN_USERNAME, ADMIN_USERNAME.upper(), ADMIN_USERNAME.capitalize()):
            response = await api_client.post(
                "/api/v1/users", json={"username": variant, "password": "Whatever1234", "role": "USER"},
            )
            assert response.status_code == 422, f"{variant!r} should collide with the existing account"

    async def test_role_is_read_from_the_database_row_not_inferred_from_username(
        self, api_client, app, db_session  # noqa: F811
    ):
        # A username containing "admin" that is actually registered as
        # USER must be treated as USER — the row decides, never the name.
        response = await api_client.post(
            "/api/v1/users", json={"username": "admin-lookalike", "password": "NotActuallyAdmin1", "role": "USER"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["role"] == "USER"

        client = await _client(app)
        try:
            login = await _login(client, "admin-lookalike", "NotActuallyAdmin1")
            assert login.status_code == 200
            assert login.json()["data"]["role"] == "USER"
            admin_only = await client.get("/api/v1/users")
            assert admin_only.status_code == 403
        finally:
            await client.aclose()


class TestJwtSubjectIsTheImmutableUuid:
    """G: JWT `sub` is the user UUID, never the username, never a trusted role claim."""

    async def test_jwt_subject_is_the_user_id_not_the_username(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            login = await _login(client, USER_USERNAME, USER_PASSWORD)
            assert login.status_code == 200
            user_uuid = login.json()["data"]["id"]

            token = client.cookies.get("access_token")
            assert token is not None
            payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
            assert payload["sub"] == user_uuid
            assert "role" not in payload            # never cached in the token
            assert "username" not in payload
        finally:
            await client.aclose()

    async def test_role_change_takes_effect_on_the_very_next_request_same_cookie(
        self, api_client, app, db_session  # noqa: F811
    ):
        client = await _client(app)
        try:
            await _login(client, USER_USERNAME, USER_PASSWORD)
            before = await client.get("/api/v1/users")
            assert before.status_code == 403           # still USER

            await api_client.patch(f"/api/v1/users/{user_id('USER')}/role", json={"role": "ADMIN"})

            after = await client.get("/api/v1/users")   # same session cookie, no re-login
            assert after.status_code == 200             # role re-read from DB, not the old token
        finally:
            await client.aclose()


class TestAuthorizationMatrix:
    """I/J/K/Q: role access boundaries, enforced server-side."""

    async def test_admin_reaches_an_admin_only_endpoint(self, api_client, db_session):  # noqa: F811
        response = await api_client.get("/api/v1/users")
        assert response.status_code == 200

    async def test_manager_reaches_a_manager_endpoint(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            await _login(client, MANAGER_USERNAME, MANAGER_PASSWORD)
            response = await client.get("/api/v1/dashboard/summary")
            assert response.status_code == 200
        finally:
            await client.aclose()

    async def test_user_reaches_a_user_endpoint(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            await _login(client, USER_USERNAME, USER_PASSWORD)
            response = await client.get("/api/v1/invoices")
            assert response.status_code == 200
        finally:
            await client.aclose()

    async def test_user_is_denied_an_admin_only_endpoint(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            await _login(client, USER_USERNAME, USER_PASSWORD)
            response = await client.get("/api/v1/users")
            assert response.status_code == 403
        finally:
            await client.aclose()

    async def test_user_is_denied_a_manager_only_endpoint(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            await _login(client, USER_USERNAME, USER_PASSWORD)
            response = await client.get("/api/v1/dashboard/summary")
            assert response.status_code == 403
        finally:
            await client.aclose()

    async def test_manager_is_denied_an_admin_only_endpoint(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            await _login(client, MANAGER_USERNAME, MANAGER_PASSWORD)
            response = await client.get("/api/v1/users")
            assert response.status_code == 403
        finally:
            await client.aclose()

    async def test_manager_cannot_change_a_users_role(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            await _login(client, MANAGER_USERNAME, MANAGER_PASSWORD)
            response = await client.patch(
                f"/api/v1/users/{user_id('USER')}/role", json={"role": "ADMIN"}
            )
            assert response.status_code == 403
        finally:
            await client.aclose()

    async def test_only_admin_can_create_an_admin_account(self, app, api_client, db_session):  # noqa: F811
        manager_client = await _client(app)
        try:
            await _login(manager_client, MANAGER_USERNAME, MANAGER_PASSWORD)
            denied = await manager_client.post(
                "/api/v1/users", json={"username": "rogue-admin", "password": "TryToBeAdmin1", "role": "ADMIN"},
            )
            assert denied.status_code == 403
        finally:
            await manager_client.aclose()

    async def test_unauthenticated_request_to_a_protected_endpoint_is_401(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            response = await client.get("/api/v1/invoices")
            assert response.status_code == 401
        finally:
            await client.aclose()


class TestDataLeakageRedaction:
    """USER/MANAGER responses never carry ADMIN technical internals."""

    async def test_user_document_status_carries_no_stage_payloads_or_technical_error_detail(
        self, app, api_client, db_session  # noqa: F811
    ):
        client = await _client(app)
        try:
            await _login(client, USER_USERNAME, USER_PASSWORD)
            accepted = await process_file(client)
            status = await client.get(accepted["status_url"])
            assert status.status_code == 200
            data = status.json()["data"]
            assert data["stages"] == []
        finally:
            await client.aclose()

    async def test_user_invoice_detail_has_no_ocr_llm_or_validation_internals(
        self, api_client, app, db_session  # noqa: F811
    ):
        accepted = await process_file(api_client)  # ADMIN session — full pipeline run
        status = (await api_client.get(accepted["status_url"])).json()["data"]
        invoice_id = status["invoice_id"]

        client = await _client(app)
        try:
            await _login(client, USER_USERNAME, USER_PASSWORD)
            # USER doesn't own this invoice, so this also proves the 403
            # ownership boundary; a MANAGER-owned-equivalent redaction
            # check is below for the fields themselves.
            response = await client.get(f"/api/v1/invoices/{invoice_id}")
            assert response.status_code == 403
        finally:
            await client.aclose()

        manager_client = await _client(app)
        try:
            await _login(manager_client, MANAGER_USERNAME, MANAGER_PASSWORD)
            response = await manager_client.get(f"/api/v1/invoices/{invoice_id}")
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["ocr_text"] is None
            assert data["raw_extraction"] is None
            assert data["llm_metadata"] is None
            assert data["validation_report"] is None
            assert data["database"] is None
            assert data["extraction_model"] is None
            # MANAGER still gets business fields.
            assert data["line_items"] != [] or data["case_mappings"] is not None
        finally:
            await manager_client.aclose()

    async def test_admin_invoice_detail_keeps_the_full_technical_model(
        self, api_client, db_session  # noqa: F811
    ):
        accepted = await process_file(api_client)
        status = (await api_client.get(accepted["status_url"])).json()["data"]
        response = await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["ocr_text"] is not None
        assert data["database"] is not None


class TestAdminUserManagement:
    async def test_list_users_never_includes_a_password_hash(self, api_client, db_session):  # noqa: F811
        response = await api_client.get("/api/v1/users")
        assert response.status_code == 200
        for row in response.json()["data"]:
            assert "password" not in row
            assert "password_hash" not in row

    async def test_admin_can_deactivate_and_reactivate_an_account(self, api_client, db_session):  # noqa: F811
        target = user_id("USER")
        off = await api_client.patch(f"/api/v1/users/{target}/active", json={"is_active": False})
        assert off.status_code == 200
        assert off.json()["data"]["is_active"] is False
        on = await api_client.patch(f"/api/v1/users/{target}/active", json={"is_active": True})
        assert on.json()["data"]["is_active"] is True

    async def test_admin_can_change_a_users_role(self, api_client, db_session):  # noqa: F811
        target = user_id("USER")
        response = await api_client.patch(f"/api/v1/users/{target}/role", json={"role": "MANAGER"})
        assert response.status_code == 200
        assert response.json()["data"]["role"] == "MANAGER"


class TestDocumentOwnershipIsUuidBased:
    """P: ownership (documents.uploaded_by_user_id) stays the immutable UUID FK, never the username."""

    async def test_ownership_column_is_the_user_uuid_and_survives_a_username_change(
        self, api_client, app, db_session  # noqa: F811
    ):
        from sqlalchemy import select

        from app.models.document import Document

        client = await _client(app)
        try:
            await _login(client, USER_USERNAME, USER_PASSWORD)
            accepted = await process_file(client)
        finally:
            await client.aclose()

        document = (await db_session.execute(
            select(Document).where(Document.id == accepted["document_id"])
        )).scalar_one()
        assert document.uploaded_by_user_id == user_id("USER")

        # Username is only a login label — changing it (impossible via the
        # current API, but the repository can) must never touch ownership.
        user = await UserRepository(db_session).get(user_id("USER"))
        user.username = "renamed-user"
        await db_session.commit()

        await db_session.refresh(document)
        assert document.uploaded_by_user_id == user_id("USER")  # unchanged UUID FK
