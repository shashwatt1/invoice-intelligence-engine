"""
tests/integration/test_store_access_db.py — per-user physical-store access, against real Postgres.

The rule (app.services.store_access): an account uses inventory for a store
only while it is active, MANAGER or above, the store is physical, and it holds
an active grant. ADMIN included — there is no implicit all-store access.
An unknown, non-physical or ungranted store is one and the same 404, and so
is an invoice or receipt at a store the account cannot use. Grants and
revocations are immutable history; every change takes effect on the next
request.

Stores: B is the `received` fixture's store (86357232, physical; the seeded
ADMIN is granted it there), A is the physical fixture store 90000001, and
47708760 is a source identity.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models.inventory import InventoryMovement, InventoryReceipt
from app.models.user_store_access import ImmutableGrantError, UserStoreAccess
from tests.integration.conftest import (
    KNOWN_USERS,
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    PHYSICAL_STORE,
    SOURCE_ONLY_STORE,
    USER_PASSWORD,
    USER_USERNAME,
    requires_db,
    store_id,
)
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.integration.test_inventory_db import (  # noqa: F401 — fixture reuse
    LAGER_CODE,
    _clean_inventory,
    approve_submission,
    grant,
    ledger_counts,
    preview_of,
    received,
    received_and_mapped,
)

pytestmark = requires_db


async def client_as(app, username, password) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return client


@pytest.fixture
async def manager(app, api_client):  # noqa: F811 — api_client wires the app to the test database
    client = await client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
    yield client
    await client.aclose()


def store_routes(sid):
    base = f"/api/v1/inventory/stores/{sid}"
    count = {"count_date": "2026-10-05", "reason": "full count", "entries": [{"item_code": LAGER_CODE, "quantity": 5}]}
    return [("GET", f"{base}/overview", None), ("GET", f"{base}/items", None),
            ("GET", f"{base}/items/{LAGER_CODE}/movements", None), ("GET", f"{base}/invoices", None),
            ("GET", f"{base}/approvals", None), ("POST", f"{base}/opening-counts", count)]


def assert_store_not_found(response, sid):
    assert response.status_code == 404, response.text
    assert response.json()["error"] == {"error_code": "ERR_NOT_FOUND", "message": "Store not found.",
                                        "detail": {"store_id": str(sid)}}


def assert_invoice_not_found(response, iid):
    assert response.status_code == 404, response.text
    assert response.json()["error"] == {"error_code": "ERR_NOT_FOUND", "message": "Invoice not found.",
                                        "detail": {"invoice_id": str(iid)}}


async def active_grants(db_session, role, store):
    return (await db_session.execute(select(func.count()).select_from(UserStoreAccess).where(
        UserStoreAccess.user_id == KNOWN_USERS[role], UserStoreAccess.store_id == store,
        UserStoreAccess.revoked_at.is_(None)))).scalar()


class TestEveryStoreRoute:
    async def test_without_a_grant_every_route_is_one_404_for_real_unknown_and_source_stores(
        self, received, manager, api_client, db_session,  # noqa: F811
    ):
        for sid in (received.store_id, uuid.uuid4(), store_id(SOURCE_ONLY_STORE)):
            for method, path, body in store_routes(sid):
                assert_store_not_found(await manager.request(method, path, json=body), sid)
        assert (await manager.get("/api/v1/inventory/stores")).json()["data"] == []
        # ADMIN is not exempt: granted B only, refused on A.
        a = store_id(PHYSICAL_STORE)
        for method, path, body in store_routes(a):
            assert_store_not_found(await api_client.request(method, path, json=body), a)
        assert await ledger_counts(db_session) == (0, 0)
        assert (await db_session.execute(select(func.count()).select_from(InventoryMovement))).scalar() == 0

    async def test_a_grant_opens_exactly_that_store(self, received, manager, db_session):  # noqa: F811
        a, b = store_id(PHYSICAL_STORE), received.store_id
        await grant(db_session, "MANAGER", a)
        for method, path, body in store_routes(a):
            r = await manager.request(method, path, json=body)
            assert r.status_code in (200, 201), (path, r.text)
        for method, path, body in store_routes(b):
            assert_store_not_found(await manager.request(method, path, json=body), b)
        assert [s["id"] for s in (await manager.get("/api/v1/inventory/stores")).json()["data"]] == [str(a)]
        stores = set((await db_session.execute(select(InventoryMovement.store_id))).scalars().all())
        assert stores == {a}                     # the one opening count went to A, nothing to B


class TestInvoiceLinkedOperations:
    async def test_preview_approve_reject_and_reverse_cannot_reach_an_ungranted_store_and_write_nothing(
        self, received, manager, api_client, db_session,  # noqa: F811
    ):
        await grant(db_session, "MANAGER", store_id(PHYSICAL_STORE))     # granted A, the invoice is at B
        url = f"/api/v1/inventory/invoices/{received.invoice_id}/receipt"
        unknown = uuid.uuid4()

        assert_invoice_not_found(await manager.get(url), received.invoice_id)
        assert_invoice_not_found(await manager.get(f"/api/v1/inventory/invoices/{unknown}/receipt"), unknown)

        # Approving with the real ids (read as the ADMIN who may see them) is refused the same way.
        receipt = await received_and_mapped(api_client, db_session, received)          # ADMIN receives it
        assert (await api_client.get(url)).json()["data"]["receipt"] is not None
        assert await ledger_counts(db_session) == (1, 2)
        # ADMIN reverses it and submits it again (reopen + re-mark Retailed), giving the manager a real
        # pending submission to attempt.
        reversal = await api_client.post(f"{url}/reverse", json={"receipt_id": receipt["id"], "reason": "probe"})
        assert reversal.status_code == 200
        base = f"/api/v1/invoices/{received.invoice_id}"
        assert (await api_client.post(f"{base}/retailed/reopen", json={})).status_code == 200
        assert (await api_client.post(f"{base}/retailed")).status_code == 200
        preview = await preview_of(api_client, received.invoice_id)
        pending_id = preview["submission"]["receipt"]["id"]
        before = await ledger_counts(db_session)
        for r in (await approve_submission(manager, received.invoice_id, preview, received_on="2026-10-10"),
                  await manager.post(f"{url}/reject", json={"receipt_id": pending_id, "reason": "not mine"})):
            assert r.status_code == 404 and r.json()["error"]["message"] == "Receipt not found for this invoice."
        assert_store_not_found(await manager.get(f"/api/v1/inventory/stores/{received.store_id}/approvals"),
                               received.store_id)
        assert await ledger_counts(db_session) == before                                # the refusals wrote nothing
        status = (await db_session.execute(select(InventoryReceipt.status).where(
            InventoryReceipt.id == uuid.UUID(pending_id)))).scalar()
        assert status == "SUBMITTED"

        approved = await approve_submission(api_client, received.invoice_id, preview, received_on="2026-10-10")
        posted = approved.json()["data"]["receipt"]                                     # ADMIN approves it
        refused = await manager.post(f"{url}/reverse", json={"receipt_id": posted["id"], "reason": "not mine"})
        missing = await manager.post(f"{url}/reverse", json={"receipt_id": str(uuid.uuid4()), "reason": "x"})
        for r in (refused, missing):
            assert r.status_code == 404 and r.json()["error"]["message"] == "Receipt not found for this invoice."
        assert set(refused.json()["error"]["detail"]) == set(missing.json()["error"]["detail"]) == {
            "invoice_id", "receipt_id"}

        status = (await db_session.execute(select(InventoryReceipt.status).where(
            InventoryReceipt.id == uuid.UUID(posted["id"])))).scalar()
        assert status == "POSTED"
        assert await ledger_counts(db_session) == (before[0], before[1] + 2)              # only the ADMIN's approval


class TestChangesTakeEffectOnTheNextRequest:
    async def test_revocation_is_immediate_and_grant_history_is_immutable(
        self, received, manager, api_client, db_session,  # noqa: F811
    ):
        b, mid = received.store_id, KNOWN_USERS["MANAGER"]
        granted = (await api_client.post(f"/api/v1/users/{mid}/store-access",
                                         json={"store_id": str(b), "reason": "covers B this week"})).json()["data"]
        assert granted["created"] is True and granted["grant"]["self_granted"] is False
        assert (granted["grant"]["granted_by"], granted["grant"]["granted_by_role"]) == ("testadmin", "ADMIN")
        assert (await manager.get(f"/api/v1/inventory/stores/{b}/overview")).status_code == 200

        revoked = await api_client.post(f"/api/v1/users/{mid}/store-access/{b}/revoke", json={"reason": "cover ended"})
        assert revoked.status_code == 200 and revoked.json()["data"]["revoked_by"] == "testadmin"
        assert_store_not_found(await manager.get(f"/api/v1/inventory/stores/{b}/overview"), b)

        again = await api_client.post(f"/api/v1/users/{mid}/store-access/{b}/revoke", json={"reason": "twice"})
        assert again.status_code == 404
        history = (await api_client.get(f"/api/v1/users/{mid}/store-access")).json()["data"]
        assert [h["id"] for h in history] == [granted["grant"]["id"]]
        assert history[0]["revoke_reason"] == "cover ended"                 # not overwritten by the second try

        regrant = (await api_client.post(f"/api/v1/users/{mid}/store-access",
                                         json={"store_id": str(b), "reason": "back on B"})).json()["data"]
        assert regrant["created"] is True and regrant["grant"]["id"] != granted["grant"]["id"]
        history = (await api_client.get(f"/api/v1/users/{mid}/store-access")).json()["data"]
        assert [(h["active"], h["grant_reason"]) for h in history] == [(False, "covers B this week"),
                                                                       (True, "back on B")]

        first = (await db_session.execute(select(UserStoreAccess).where(
            UserStoreAccess.id == uuid.UUID(granted["grant"]["id"])))).scalars().one()
        first.revoke_reason = "rewritten"
        with pytest.raises(ImmutableGrantError):
            await db_session.flush()
        await db_session.rollback()
        first = (await db_session.execute(select(UserStoreAccess).where(
            UserStoreAccess.id == uuid.UUID(granted["grant"]["id"])))).scalars().one()
        await db_session.delete(first)
        with pytest.raises(ImmutableGrantError):
            await db_session.flush()
        await db_session.rollback()

    async def test_demotion_and_deactivation_end_access_on_the_next_request(
        self, received, manager, api_client, db_session,  # noqa: F811
    ):
        b, mid = received.store_id, KNOWN_USERS["MANAGER"]
        await grant(db_session, "MANAGER", b)
        assert (await manager.get(f"/api/v1/inventory/stores/{b}/overview")).status_code == 200

        assert (await api_client.patch(f"/api/v1/users/{mid}/role", json={"role": "USER"})).status_code == 200
        assert (await manager.get(f"/api/v1/inventory/stores/{b}/overview")).status_code == 403
        assert (await manager.get("/api/v1/inventory/stores")).status_code == 403
        assert await active_grants(db_session, "MANAGER", b) == 1          # the grant remains; the role decides

        assert (await api_client.patch(f"/api/v1/users/{mid}/role", json={"role": "MANAGER"})).status_code == 200
        assert (await manager.get(f"/api/v1/inventory/stores/{b}/overview")).status_code == 200

        assert (await api_client.patch(f"/api/v1/users/{mid}/active", json={"is_active": False})).status_code == 200
        assert (await manager.get(f"/api/v1/inventory/stores/{b}/overview")).status_code == 401

    async def test_a_user_account_is_never_granted_and_never_has_inventory(
        self, received, app, api_client,  # noqa: F811
    ):
        uid = KNOWN_USERS["USER"]
        r = await api_client.post(f"/api/v1/users/{uid}/store-access",
                                  json={"store_id": str(received.store_id), "reason": "try"})
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "account_not_eligible"
        user = await client_as(app, USER_USERNAME, USER_PASSWORD)
        try:
            assert (await user.get(f"/api/v1/inventory/stores/{received.store_id}/overview")).status_code == 403
            assert (await user.get("/api/v1/inventory/stores")).status_code == 403
        finally:
            await user.aclose()


class TestGrantAdministration:
    async def test_an_administrator_may_grant_their_own_account_and_it_is_recorded(
        self, received, api_client,  # noqa: F811
    ):
        a, aid = store_id(PHYSICAL_STORE), KNOWN_USERS["ADMIN"]
        r = (await api_client.post(f"/api/v1/users/{aid}/store-access",
                                   json={"store_id": str(a), "reason": "I run the pilot count at A"})).json()["data"]
        assert r["created"] is True and r["grant"]["self_granted"] is True
        assert (r["grant"]["username"], r["grant"]["granted_by"], r["grant"]["granted_by_role"]) == (
            "testadmin", "testadmin", "ADMIN")
        assert (await api_client.get(f"/api/v1/inventory/stores/{a}/overview")).status_code == 200

    async def test_grants_refuse_source_identities_unknown_ids_and_missing_reasons(
        self, received, api_client,  # noqa: F811
    ):
        mid, a = KNOWN_USERS["MANAGER"], store_id(PHYSICAL_STORE)
        url = f"/api/v1/users/{mid}/store-access"
        r = await api_client.post(url, json={"store_id": str(store_id(SOURCE_ONLY_STORE)), "reason": "x"})
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "source_identity"
        assert (await api_client.post(url, json={"store_id": str(uuid.uuid4()), "reason": "x"})).status_code == 404
        for reason in ("", "   "):
            assert (await api_client.post(url, json={"store_id": str(a), "reason": reason})).status_code == 422
        assert (await api_client.post(f"/api/v1/users/{uuid.uuid4()}/store-access",
                                      json={"store_id": str(a), "reason": "x"})).status_code == 404

    async def test_a_repeat_or_concurrent_grant_leaves_exactly_one_active_row(
        self, received, api_client, db_session,  # noqa: F811
    ):
        mid, a = KNOWN_USERS["MANAGER"], store_id(PHYSICAL_STORE)
        url = f"/api/v1/users/{mid}/store-access"
        responses = await asyncio.gather(*(api_client.post(url, json={"store_id": str(a), "reason": "race"})
                                           for _ in range(4)))
        assert [r.status_code for r in responses] == [200] * 4
        assert sorted(r.json()["data"]["created"] for r in responses) == [False, False, False, True]
        assert len({r.json()["data"]["grant"]["id"] for r in responses}) == 1
        assert await active_grants(db_session, "MANAGER", a) == 1

    async def test_only_an_administrator_manages_grants(self, received, manager):  # noqa: F811
        r = await manager.post(f"/api/v1/users/{KNOWN_USERS['MANAGER']}/store-access",
                               json={"store_id": str(store_id(PHYSICAL_STORE)), "reason": "self-service"})
        assert r.status_code == 403

    async def test_the_database_refuses_an_incomplete_revocation(self, received, db_session):  # noqa: F811
        row = await grant(db_session, "MANAGER", store_id(PHYSICAL_STORE))
        with pytest.raises(IntegrityError):
            await db_session.execute(text("UPDATE user_store_access SET revoked_at = now() WHERE id = :id"),
                                     {"id": row.id})
        await db_session.rollback()


class TestStoreDirectoryScope:
    """The Store Directory page: ADMIN sees every store; a MANAGER only the physical stores they are granted."""

    async def test_a_manager_sees_only_granted_physical_stores_and_an_admin_sees_all(
        self, received, manager, api_client, db_session,  # noqa: F811
    ):
        a = store_id(PHYSICAL_STORE)
        assert (await manager.get("/api/v1/stores/directory")).json()["data"] == []
        await grant(db_session, "MANAGER", a)
        assert [s["id"] for s in (await manager.get("/api/v1/stores/directory")).json()["data"]] == [str(a)]
        everything = {s["id"] for s in (await api_client.get("/api/v1/stores")).json()["data"]}
        admin_view = {s["id"] for s in (await api_client.get("/api/v1/stores/directory")).json()["data"]}
        assert admin_view == everything and str(store_id(SOURCE_ONLY_STORE)) in admin_view

    async def test_the_processing_store_pickers_are_unchanged_for_managers(
        self, received, manager, api_client,  # noqa: F811
    ):
        # GET /stores feeds the pickers used while processing invoices; it is not grant-scoped.
        as_manager = {s["id"] for s in (await manager.get("/api/v1/stores")).json()["data"]}
        as_admin = {s["id"] for s in (await api_client.get("/api/v1/stores")).json()["data"]}
        assert as_manager == as_admin

    async def test_a_user_account_has_no_directory(self, received, app):  # noqa: F811
        user = await client_as(app, USER_USERNAME, USER_PASSWORD)
        try:
            assert (await user.get("/api/v1/stores/directory")).status_code == 403
        finally:
            await user.aclose()


class TestStoreIdentityIsAdministratorOnly:
    async def test_a_manager_cannot_add_a_store_or_change_an_identity_and_nothing_changes(
        self, received, manager, api_client,  # noqa: F811
    ):
        a = store_id(PHYSICAL_STORE)
        before = (await api_client.get(f"/api/v1/stores/{a}")).json()["data"]
        r = await manager.post("/api/v1/stores", json={"display_name": "Manager Market", "created_by": "m"})
        assert r.status_code == 403
        r = await manager.patch(f"/api/v1/stores/{a}", json={"display_name": "Renamed", "confirm": True,
                                                              "confirmed_by": "m"})
        assert r.status_code == 403
        assert (await api_client.get(f"/api/v1/stores/{a}")).json()["data"] == before
        names = {s["display_name"] for s in (await api_client.get("/api/v1/stores")).json()["data"]}
        assert "Manager Market" not in names

    async def test_an_administrator_still_can(self, received, api_client):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        r = await api_client.patch(f"/api/v1/stores/{a}", json={"customer_name": "Billing Name Inc"})
        assert r.status_code == 200 and r.json()["data"]["customer_name"] == "Billing Name Inc"


class TestGrantAllPhysicalStores:
    async def _physical(self, api_client):  # noqa: F811
        return {s["id"] for s in (await api_client.get("/api/v1/stores")).json()["data"] if s["kind"] == "physical"}

    async def test_an_administrator_account_gets_every_physical_store_once_and_a_repeat_writes_nothing(
        self, received, api_client, db_session,  # noqa: F811
    ):
        aid = KNOWN_USERS["ADMIN"]
        url = f"/api/v1/users/{aid}/store-access/grant-all-physical"
        physical = await self._physical(api_client)
        first = (await api_client.post(url, json={"reason": "admin oversight of every store"})).json()["data"]
        granted_before = {g["store_id"] for g in first["already_granted"]}
        assert granted_before == {str(received.store_id)}            # the fixture's existing grant, unchanged
        assert {g["store_id"] for g in first["created"]} | granted_before == physical
        assert all(g["grant_reason"] == "admin oversight of every store" and g["self_granted"]
                   for g in first["created"])
        assert str(store_id(SOURCE_ONLY_STORE)) not in {g["store_id"] for g in first["created"]}
        again = (await api_client.post(url, json={"reason": "again"})).json()["data"]
        assert again["created"] == [] and {g["store_id"] for g in again["already_granted"]} == physical
        total = (await db_session.execute(select(func.count()).select_from(UserStoreAccess).where(
            UserStoreAccess.user_id == aid, UserStoreAccess.revoked_at.is_(None)))).scalar()
        assert total == len(physical)
        listed = {s["id"] for s in (await api_client.get("/api/v1/inventory/stores")).json()["data"]}
        assert listed == physical

    async def test_it_is_refused_for_a_manager_account_without_a_reason_or_by_a_manager(
        self, received, api_client, manager, db_session,  # noqa: F811
    ):
        mid = KNOWN_USERS["MANAGER"]
        r = await api_client.post(f"/api/v1/users/{mid}/store-access/grant-all-physical", json={"reason": "x"})
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "account_not_administrator"
        aid = KNOWN_USERS["ADMIN"]
        assert (await api_client.post(f"/api/v1/users/{aid}/store-access/grant-all-physical",
                                      json={"reason": "  "})).status_code == 422
        assert (await manager.post(f"/api/v1/users/{aid}/store-access/grant-all-physical",
                                   json={"reason": "x"})).status_code == 403
        assert (await api_client.post(f"/api/v1/users/{uuid.uuid4()}/store-access/grant-all-physical",
                                      json={"reason": "x"})).status_code == 404
        assert (await db_session.execute(select(func.count()).select_from(UserStoreAccess).where(
            UserStoreAccess.user_id == mid))).scalar() == 0


class _RecordingLogger:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def info(self, event, **fields):
        self.events.append((event, fields))

    def __getattr__(self, _name):                      # warning/error etc.: accepted, not asserted
        return lambda *a, **k: None


class TestBulkGrant:
    """POST /users/{id}/store-access/bulk: all or nothing, one audit row and log entry per new grant."""

    def url(self, uid):
        return f"/api/v1/users/{uid}/store-access/bulk"

    async def _physical(self, api_client):  # noqa: F811
        return sorted(s["id"] for s in (await api_client.get("/api/v1/stores")).json()["data"] if s["kind"] == "physical")

    async def _rows(self, db_session, uid):
        db_session.expire_all()
        return (await db_session.execute(select(UserStoreAccess).where(UserStoreAccess.user_id == uid))).scalars().all()

    async def test_each_new_grant_is_its_own_audit_row_and_log_entry(
        self, received, api_client, db_session, monkeypatch,  # noqa: F811
    ):
        import app.api.v1.users as users_api
        log = _RecordingLogger()
        monkeypatch.setattr(users_api, "logger", log)
        mid, physical = KNOWN_USERS["MANAGER"], await self._physical(api_client)
        assert len(physical) >= 2
        r = await api_client.post(self.url(mid), json={"store_ids": physical, "reason": "covers these stores"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert sorted(g["store_id"] for g in data["created"]) == physical and data["already_granted"] == []
        rows = await self._rows(db_session, mid)
        assert sorted(str(g.store_id) for g in rows) == physical
        assert {(g.granted_by, g.granted_by_role, g.grant_reason, g.revoked_at) for g in rows} == {
            ("testadmin", "ADMIN", "covers these stores", None)}
        grants_logged = [f for e, f in log.events if e == "store_access_granted"]
        assert sorted(f["store_id"] for f in grants_logged) == physical
        assert all(f["actor_username"] == "testadmin" and f["actor_role"] == "ADMIN" and f["bulk"] for f in grants_logged)
        assert [e for e, _ in log.events].count("store_access_granted_bulk") == 1

    async def test_an_already_granted_store_is_returned_unchanged(self, received, api_client, db_session):  # noqa: F811
        mid, a, b = KNOWN_USERS["MANAGER"], store_id(PHYSICAL_STORE), received.store_id
        first = await grant(db_session, "MANAGER", a, reason="original reason")
        r = await api_client.post(self.url(mid), json={"store_ids": [str(a), str(b)], "reason": "bulk"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert [g["store_id"] for g in data["created"]] == [str(b)]
        assert [(g["id"], g["grant_reason"]) for g in data["already_granted"]] == [(str(first.id), "original reason")]
        assert await active_grants(db_session, "MANAGER", a) == 1 and await active_grants(db_session, "MANAGER", b) == 1

    async def test_any_invalid_store_refuses_the_whole_request_and_names_them_all(
        self, received, api_client, db_session,  # noqa: F811
    ):
        mid, a = KNOWN_USERS["MANAGER"], store_id(PHYSICAL_STORE)
        unknown, source = uuid.uuid4(), store_id(SOURCE_ONLY_STORE)
        r = await api_client.post(self.url(mid), json={"store_ids": [str(a), str(unknown), str(source)], "reason": "x"})
        assert r.status_code == 422, r.text
        detail = r.json()["error"]["detail"]
        assert detail["reason"] == "invalid_stores"
        assert detail["stores"] == {str(unknown): "unknown", str(source): "source_identity"}
        assert await self._rows(db_session, mid) == []                       # the valid store was not granted either

    async def test_duplicates_counts_and_reasons_are_refused_and_nothing_is_written(
        self, received, api_client, db_session,  # noqa: F811
    ):
        mid, a = KNOWN_USERS["MANAGER"], str(store_id(PHYSICAL_STORE))
        r = await api_client.post(self.url(mid), json={"store_ids": [a, a], "reason": "x"})
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "duplicate_store_ids"
        for body in ({"store_ids": [], "reason": "x"},
                     {"store_ids": [str(uuid.uuid4()) for _ in range(101)], "reason": "x"},
                     {"store_ids": [a], "reason": "   "}, {"store_ids": [a], "reason": ""}, {"store_ids": [a]}):
            assert (await api_client.post(self.url(mid), json=body)).status_code == 422, body
        assert await self._rows(db_session, mid) == []

    async def test_authorization_and_eligibility(self, received, api_client, manager, db_session):  # noqa: F811
        a = str(store_id(PHYSICAL_STORE))
        assert (await manager.post(self.url(KNOWN_USERS["MANAGER"]), json={"store_ids": [a], "reason": "x"})).status_code == 403
        r = await api_client.post(self.url(KNOWN_USERS["USER"]), json={"store_ids": [a], "reason": "x"})
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "account_not_eligible"
        assert (await api_client.post(self.url(uuid.uuid4()), json={"store_ids": [a], "reason": "x"})).status_code == 404
        assert await self._rows(db_session, KNOWN_USERS["MANAGER"]) == []
        assert await self._rows(db_session, KNOWN_USERS["USER"]) == []

    async def test_concurrent_overlapping_requests_leave_one_active_grant_per_store(
        self, received, api_client, db_session,  # noqa: F811
    ):
        mid, ids = KNOWN_USERS["MANAGER"], [str(store_id(PHYSICAL_STORE)), str(received.store_id)]
        responses = await asyncio.gather(*(api_client.post(self.url(mid), json={"store_ids": ids, "reason": "race"})
                                           for _ in range(4)))
        assert [r.status_code for r in responses] == [200] * 4
        assert sum(len(r.json()["data"]["created"]) for r in responses) == 2
        for sid in ids:
            assert await active_grants(db_session, "MANAGER", uuid.UUID(sid)) == 1

    async def test_grants_open_inventory_and_leave_invoice_processing_as_it_was(
        self, received, api_client, manager, db_session,  # noqa: F811
    ):
        invoices_before = (await manager.get("/api/v1/invoices")).json()["total"]
        assert (await manager.get("/api/v1/inventory/stores")).json()["data"] == []
        ids = [str(store_id(PHYSICAL_STORE)), str(received.store_id)]
        assert (await api_client.post(self.url(KNOWN_USERS["MANAGER"]),
                                      json={"store_ids": ids, "reason": "x"})).status_code == 200
        listed = sorted(s["id"] for s in (await manager.get("/api/v1/inventory/stores")).json()["data"])
        assert listed == sorted(ids)
        assert (await manager.get("/api/v1/invoices")).json()["total"] == invoices_before
