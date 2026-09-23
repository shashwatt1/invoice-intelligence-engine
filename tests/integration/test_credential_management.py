"""
tests/integration/test_credential_management.py — administrative
password reset and account activation/deactivation, against real
Postgres and (for reset) the real scripts/reset_password.py /
scripts/set_user_status.py CLIs.

Both CLIs are internal-only: there is no email reset link, no OTP, no
self-service recovery, and no web password-reset form — an ADMIN with
shell access runs the script, or (for activation only) uses the
existing ADMIN-only /users page, which already called
PATCH /users/{id}/active before this phase and is reused unchanged
here. Nothing here touches the four real named accounts
(shashwatt1/barj/prabh/vivek) — every user in this file is created and
destroyed within a single test via the disposable-account fixtures
already established in conftest.py.

Letters A-Z below correspond exactly to the checklist items given for
this phase (PASSWORD RESET A-K, ACCOUNT STATUS L-T, AUTH REGRESSION
U-Z).
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password, verify_password
from app.models.invoice import Invoice
from app.models.product_data_proposal import ProductDataProposal
from app.models.user import UserRole
from app.repositories.document_repository import DocumentRepository
from app.repositories.user_repository import UserRepository
from scripts import reset_password as reset_password_script
from scripts import set_user_status as set_user_status_script
from tests.integration.conftest import (
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    USER_PASSWORD,
    USER_USERNAME,
    requires_db,
    user_id,
)
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse

pytestmark = requires_db


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _login(client, username, password):
    return await client.post("/api/v1/auth/login", json={"username": username, "password": password})


async def _disposable_user(db_session, *, username: str, role: str = UserRole.USER.value, password: str = "OldPass1234"):
    user = await UserRepository(db_session).create(username=username, password_hash=hash_password(password), role=role)
    await db_session.commit()
    return user


@pytest.fixture
def _scripts_use_test_db(db_engine, monkeypatch):
    """
    Both CLIs call app.database.session.get_session_factory(), which is
    bound to the app's configured DATABASE_URL, not this test's isolated
    db_engine. Redirecting it, the same way test_api_db.py's api_client
    fixture redirects the API routers, lets the tests exercise the real
    CLI functions end-to-end against the test database.
    """
    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(reset_password_script, "get_session_factory", lambda: factory)
    monkeypatch.setattr(set_user_status_script, "get_session_factory", lambda: factory)
    return factory


class TestPasswordResetCli:
    """A-K: scripts/reset_password.py against a disposable account."""

    async def test_a_existing_user_can_have_password_reset(self, db_session, _scripts_use_test_db):
        user = await _disposable_user(db_session, username="reset-target-a")
        await reset_password_script._reset(user.username, "NewPass5678")
        await db_session.refresh(user)
        assert verify_password("NewPass5678", user.password_hash)

    async def test_b_new_password_authenticates(self, app, api_client, db_session, _scripts_use_test_db):  # noqa: F811
        user = await _disposable_user(db_session, username="reset-target-b")
        await reset_password_script._reset(user.username, "NewPass5678")

        client = await _client(app)
        try:
            response = await _login(client, user.username, "NewPass5678")
            assert response.status_code == 200
        finally:
            await client.aclose()

    async def test_c_old_password_no_longer_authenticates(self, app, api_client, db_session, _scripts_use_test_db):  # noqa: F811
        user = await _disposable_user(db_session, username="reset-target-c", password="OldPass1234")
        await reset_password_script._reset(user.username, "NewPass5678")

        client = await _client(app)
        try:
            response = await _login(client, user.username, "OldPass1234")
            assert response.status_code == 401
        finally:
            await client.aclose()

    async def test_d_nonexistent_username_produces_a_clean_error(self, db_session, _scripts_use_test_db, capsys):
        with pytest.raises(SystemExit) as exc_info:
            await reset_password_script._reset("no-such-user-xyz", "WhateverPass123")
        assert exc_info.value.code == 1
        assert "No account" in capsys.readouterr().err

    def test_e_short_password_rejected(self, monkeypatch, capsys):
        responses = iter(["short", "ValidPass123", "ValidPass123"])
        monkeypatch.setattr(reset_password_script.getpass, "getpass", lambda prompt="": next(responses))
        assert reset_password_script._read_new_password() == "ValidPass123"
        assert "at least" in capsys.readouterr().err

    def test_f_mismatched_confirmation_rejected(self, monkeypatch, capsys):
        responses = iter(["ValidPass123", "SomethingElse1", "ValidPass123", "ValidPass123"])
        monkeypatch.setattr(reset_password_script.getpass, "getpass", lambda prompt="": next(responses))
        assert reset_password_script._read_new_password() == "ValidPass123"
        assert "did not match" in capsys.readouterr().err

    async def test_g_password_is_stored_only_as_bcrypt_hash(self, db_session, _scripts_use_test_db):
        user = await _disposable_user(db_session, username="reset-target-g")
        await reset_password_script._reset(user.username, "AnotherPass456")
        await db_session.refresh(user)
        assert user.password_hash != "AnotherPass456"
        assert user.password_hash.startswith(("$2a$", "$2b$"))
        assert verify_password("AnotherPass456", user.password_hash)

    async def test_h_username_normalization_works(self, db_session, _scripts_use_test_db):
        user = await _disposable_user(db_session, username="norm-target")
        await reset_password_script._reset("  NORM-Target  ", "NewPass9999")
        await db_session.refresh(user)
        assert verify_password("NewPass9999", user.password_hash)

    async def test_i_role_remains_unchanged(self, db_session, _scripts_use_test_db):
        user = await _disposable_user(db_session, username="reset-target-i", role=UserRole.MANAGER.value)
        await reset_password_script._reset(user.username, "NewPass1111")
        await db_session.refresh(user)
        assert user.role == UserRole.MANAGER.value

    async def test_j_user_uuid_remains_unchanged(self, db_session, _scripts_use_test_db):
        user = await _disposable_user(db_session, username="reset-target-j")
        original_id = user.id
        await reset_password_script._reset(user.username, "NewPass2222")
        await db_session.refresh(user)
        assert user.id == original_id

    async def test_k_ownership_remains_unchanged(self, db_session, _scripts_use_test_db):
        user = await _disposable_user(db_session, username="reset-target-k")
        document = await DocumentRepository(db_session).create(
            filename="owned.pdf", mime_type="application/pdf", file_size_bytes=10,
            file_path="/uploads/owned.pdf", file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            uploaded_by_user_id=user.id,
        )
        await db_session.commit()

        await reset_password_script._reset(user.username, "NewPass3333")

        await db_session.refresh(document)
        assert document.uploaded_by_user_id == user.id


class TestAccountStatusCli:
    """L/N: scripts/set_user_status.py, the CLI counterpart to PATCH /users/{id}/active."""

    async def test_admin_cli_can_deactivate_and_reactivate(self, db_session, _scripts_use_test_db):
        user = await _disposable_user(db_session, username="status-cli-target")
        await set_user_status_script._set_status(user.username, False)
        await db_session.refresh(user)
        assert user.is_active is False

        await set_user_status_script._set_status(user.username, True)
        await db_session.refresh(user)
        assert user.is_active is True

    async def test_status_cli_leaves_role_and_password_untouched(self, db_session, _scripts_use_test_db):
        user = await _disposable_user(db_session, username="status-cli-untouched", role=UserRole.MANAGER.value)
        password_hash_before = user.password_hash
        await set_user_status_script._set_status(user.username, False)
        await db_session.refresh(user)
        assert user.role == UserRole.MANAGER.value
        assert user.password_hash == password_hash_before

    async def test_status_cli_nonexistent_username_produces_a_clean_error(self, db_session, _scripts_use_test_db, capsys):
        with pytest.raises(SystemExit) as exc_info:
            await set_user_status_script._set_status("no-such-user-xyz", False)
        assert exc_info.value.code == 1
        assert "No account" in capsys.readouterr().err

    def test_status_cli_rejects_an_invalid_status_word(self, capsys):
        with pytest.raises(SystemExit):
            set_user_status_script._parse_status("MAYBE")
        assert "not a valid status" in capsys.readouterr().err


class TestAccountStatusApi:
    """L/M/N/O/P/Q/R/S/T: ADMIN-only activation/deactivation via the existing web API."""

    async def test_l_admin_can_deactivate_a_user(self, api_client, db_session):  # noqa: F811
        target = await _disposable_user(db_session, username="status-target-l", password="Pass12345")
        response = await api_client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": False})
        assert response.status_code == 200
        assert response.json()["data"]["is_active"] is False

    async def test_m_deactivated_user_cannot_log_in(self, app, api_client, db_session):  # noqa: F811
        target = await _disposable_user(db_session, username="status-target-m", password="Pass12345")
        deactivate = await api_client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": False})
        assert deactivate.status_code == 200

        client = await _client(app)
        try:
            response = await _login(client, target.username, "Pass12345")
            assert response.status_code == 401
        finally:
            await client.aclose()

    async def test_n_admin_can_reactivate_a_user(self, api_client, db_session):  # noqa: F811
        target = await _disposable_user(db_session, username="status-target-n", password="Pass12345")
        await api_client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": False})
        response = await api_client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": True})
        assert response.status_code == 200
        assert response.json()["data"]["is_active"] is True

    async def test_o_reactivated_user_can_log_in(self, app, api_client, db_session):  # noqa: F811
        target = await _disposable_user(db_session, username="status-target-o", password="Pass12345")
        await api_client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": False})
        await api_client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": True})

        client = await _client(app)
        try:
            response = await _login(client, target.username, "Pass12345")
            assert response.status_code == 200
        finally:
            await client.aclose()

    async def test_p_manager_cannot_change_account_status(self, app, api_client, db_session):  # noqa: F811
        target = await _disposable_user(db_session, username="status-target-p", password="Pass12345")
        client = await _client(app)
        try:
            await _login(client, MANAGER_USERNAME, MANAGER_PASSWORD)
            response = await client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": False})
            assert response.status_code == 403
        finally:
            await client.aclose()

    async def test_q_user_cannot_change_account_status(self, app, api_client, db_session):  # noqa: F811
        target = await _disposable_user(db_session, username="status-target-q", password="Pass12345")
        client = await _client(app)
        try:
            await _login(client, USER_USERNAME, USER_PASSWORD)
            response = await client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": False})
            assert response.status_code == 403
        finally:
            await client.aclose()

    async def test_r_unauthenticated_caller_cannot_change_account_status(self, app, api_client, db_session):  # noqa: F811
        target = await _disposable_user(db_session, username="status-target-r", password="Pass12345")
        client = await _client(app)
        try:
            response = await client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": False})
            assert response.status_code == 401
        finally:
            await client.aclose()

    async def test_s_deactivation_does_not_modify_document_ownership(self, api_client, db_session):  # noqa: F811
        target = await _disposable_user(db_session, username="status-target-s", password="Pass12345")
        document = await DocumentRepository(db_session).create(
            filename="owned.pdf", mime_type="application/pdf", file_size_bytes=10,
            file_path="/uploads/owned-s.pdf", file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            uploaded_by_user_id=target.id,
        )
        await db_session.commit()

        deactivate = await api_client.patch(f"/api/v1/users/{target.id}/active", json={"is_active": False})
        assert deactivate.status_code == 200

        await db_session.refresh(document)
        assert document.uploaded_by_user_id == target.id

    async def test_t_deactivation_does_not_modify_proposals_mappings_or_invoices(
        self, app, api_client, db_session  # noqa: F811
    ):
        from tests.integration.test_data_team_user_permissions import (
            ITEM_CODE_11,
            ITEM_CODE_12,
            _owned_invoice,
        )

        user_client, invoice_id, _ = await _owned_invoice(app, api_client, db_session)
        try:
            submit = await user_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": ITEM_CODE_12, "units_per_case": 12}]},
            )
            assert submit.status_code == 200
        finally:
            await user_client.aclose()

        proposal = (await db_session.execute(
            select(ProductDataProposal).where(ProductDataProposal.entity_key == ITEM_CODE_11)
        )).scalars().one()
        status_before, proposed_by_before = proposal.status, proposal.proposed_by

        invoice = (await db_session.execute(
            select(Invoice).where(Invoice.id == uuid.UUID(invoice_id))
        )).scalar_one()
        grand_total_before = invoice.grand_total

        mappings_before = (await db_session.execute(text("SELECT count(*) FROM product_case_mappings"))).scalar_one()

        deactivate = await api_client.patch(f"/api/v1/users/{user_id('USER')}/active", json={"is_active": False})
        assert deactivate.status_code == 200

        await db_session.refresh(proposal)
        await db_session.refresh(invoice)
        assert proposal.status == status_before
        assert proposal.proposed_by == proposed_by_before
        assert invoice.grand_total == grand_total_before

        mappings_after = (await db_session.execute(text("SELECT count(*) FROM product_case_mappings"))).scalar_one()
        assert mappings_after == mappings_before


class TestAuthRegressionAfterCredentialManagement:
    """U/V/W/X/Y/Z: the P3 permission model is unchanged by this phase."""

    async def test_u_admin_permissions_remain_intact(self, api_client, db_session):  # noqa: F811
        response = await api_client.get("/api/v1/users")
        assert response.status_code == 200

    async def test_v_manager_permissions_remain_intact(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            await _login(client, MANAGER_USERNAME, MANAGER_PASSWORD)
            allowed = await client.get("/api/v1/dashboard/summary")
            assert allowed.status_code == 200
            forbidden = await client.get("/api/v1/users")
            assert forbidden.status_code == 403
        finally:
            await client.aclose()

    async def test_w_user_permissions_remain_intact(self, app, api_client, db_session):  # noqa: F811
        client = await _client(app)
        try:
            await _login(client, USER_USERNAME, USER_PASSWORD)
            allowed = await client.get("/api/v1/invoices")
            assert allowed.status_code == 200
            forbidden = await client.get("/api/v1/users")
            assert forbidden.status_code == 403
        finally:
            await client.aclose()

    async def test_x_y_z_user_proposal_workflow_boundaries_remain_intact(
        self, app, api_client, db_session  # noqa: F811
    ):
        from tests.integration.test_data_team_user_permissions import ITEM_CODE_12, _owned_invoice

        user_client, invoice_id, _ = await _owned_invoice(app, api_client, db_session)
        try:
            submit = await user_client.post(
                f"/api/v1/invoices/{invoice_id}/case-mappings",
                json={"mappings": [{"item_code": ITEM_CODE_12, "units_per_case": 12}]},
            )
            assert submit.status_code == 200                              # X: submission still works

            proposal = (await db_session.execute(
                select(ProductDataProposal).where(ProductDataProposal.status == "PENDING")
            )).scalars().first()
            assert proposal is not None

            approve = await user_client.post(
                f"/api/v1/proposals/{proposal.id}/approve", json={"reviewed_by": USER_USERNAME},
            )
            assert approve.status_code == 403                             # Y

            reject = await user_client.post(
                f"/api/v1/proposals/{proposal.id}/reject", json={"reviewed_by": USER_USERNAME},
            )
            assert reject.status_code == 403                              # Z (no path to an authoritative mapping)
        finally:
            await user_client.aclose()
