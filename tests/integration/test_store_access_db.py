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
    acceptance,
    grant,
    ledger_counts,
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
            ("POST", f"{base}/opening-counts", count)]


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
    async def test_preview_post_and_reverse_cannot_reach_an_ungranted_store_and_write_nothing(
        self, received, manager, api_client, db_session,  # noqa: F811
    ):
        await grant(db_session, "MANAGER", store_id(PHYSICAL_STORE))     # granted A, the invoice is at B
        url = f"/api/v1/inventory/invoices/{received.invoice_id}/receipt"
        unknown = uuid.uuid4()

        assert_invoice_not_found(await manager.get(url), received.invoice_id)
        assert_invoice_not_found(await manager.get(f"/api/v1/inventory/invoices/{unknown}/receipt"), unknown)

        # Posting with the real line ids (read as the ADMIN who may see them) is refused the same way.
        receipt = await received_and_mapped(api_client, db_session, received)          # ADMIN receives it
        assert (await api_client.get(url)).json()["data"]["receipt"] is not None
        assert await ledger_counts(db_session) == (1, 2)
        # ADMIN reverses it so it can be received again, giving the manager a real post to attempt.
        reversal = await api_client.post(f"{url}/reverse", json={"receipt_id": receipt["id"], "reason": "probe"})
        assert reversal.status_code == 200
        preview = (await api_client.get(url)).json()["data"]
        body = {"received_on": "2026-10-10", "lines": acceptance(preview["lines"])}
        before = await ledger_counts(db_session)
        assert_invoice_not_found(await manager.post(url, json=body), received.invoice_id)
        assert await ledger_counts(db_session) == before                                # the refusal wrote nothing

        posted = (await api_client.post(url, json=body)).json()["data"]["receipt"]          # ADMIN posts again
        refused = await manager.post(f"{url}/reverse", json={"receipt_id": posted["id"], "reason": "not mine"})
        missing = await manager.post(f"{url}/reverse", json={"receipt_id": str(uuid.uuid4()), "reason": "x"})
        for r in (refused, missing):
            assert r.status_code == 404 and r.json()["error"]["message"] == "Receipt not found for this invoice."
        assert set(refused.json()["error"]["detail"]) == set(missing.json()["error"]["detail"]) == {
            "invoice_id", "receipt_id"}

        status = (await db_session.execute(select(InventoryReceipt.status).where(
            InventoryReceipt.id == uuid.UUID(posted["id"])))).scalar()
        assert status == "POSTED"
        assert await ledger_counts(db_session) == (before[0] + 1, before[1] + 2)          # only the ADMIN's post


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
