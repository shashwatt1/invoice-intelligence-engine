"""
tests/integration/test_inventory_all_stores_db.py — the Inventory page's store list and opening state, against Postgres.

Stores: 86357232 ("LG - RCM", the `received` fixture's store) and 90000001 ("AF Fixture Store") are physical;
47708760 is a source identity. The seeded ADMIN is granted nothing by default.

  * every physical store an account may see is listed — with no invoices, receipts or movements — and a source
    identity never is;
  * a MANAGER sees only granted stores; an ADMIN also sees other active physical stores, but as locked entries
    without figures, and cannot open them; a USER sees nothing;
  * the opening state is never invented: NOT_SET (stock unknown, no on-hand figure) is not a ZERO_START (an
    administrator's declaration) and neither is COUNTED (a person's physical count);
  * "start at zero" is administrator-only, idempotent, audited, and writes one baseline and nothing else —
    no movement, no receipt, no change to a posted receipt or a balance;
  * an opening count cannot be entered twice for one product.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models.inventory import InventoryMovement, InventoryReceipt, StoreInventoryBaseline
from tests.integration.conftest import (
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
    ALE_CODE,
    LAGER_CODE,
    _clean_inventory,
    grant,
    received,
    received_and_mapped,
    switch_off,
    switch_on,
)
from tests.integration.test_store_access_db import client_as

pytestmark = requires_db

OVERVIEW = "/api/v1/inventory/overview"


def zero_start_path(sid):
    return f"/api/v1/inventory/stores/{sid}/zero-start"


@pytest.fixture(autouse=True)
async def inventory_on(api_client, db_session, monkeypatch):  # noqa: F811
    """Inventory (and the ledger it reads) on for these tests only."""
    await switch_on(db_session, monkeypatch)
    yield
    await switch_off(db_session, monkeypatch)


@pytest.fixture
async def manager(app, api_client):  # noqa: F811 — api_client wires the app to the test database
    client = await client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
    yield client
    await client.aclose()


@pytest.fixture
async def plain_user(app, api_client):  # noqa: F811
    client = await client_as(app, USER_USERNAME, USER_PASSWORD)
    yield client
    await client.aclose()


async def listing(client):
    r = await client.get(OVERVIEW)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    return data, {e["store"]["id"]: e for e in data["stores"]}


async def ledger(db_session):
    movements = (await db_session.execute(
        select(InventoryMovement.id, InventoryMovement.item_code, InventoryMovement.quantity)
        .order_by(InventoryMovement.id))).all()
    receipts = (await db_session.execute(
        select(InventoryReceipt.id, InventoryReceipt.status).order_by(InventoryReceipt.id))).all()
    return movements, receipts


async def baseline_rows(db_session):
    return (await db_session.execute(select(func.count()).select_from(StoreInventoryBaseline))).scalar()


class TestEveryPhysicalStoreIsListed:
    async def test_stores_without_invoices_receipts_or_movements_are_listed_and_a_source_identity_is_not(
        self, api_client, db_session,  # noqa: F811
    ):
        a, b = store_id(PHYSICAL_STORE), store_id("86357232")
        await grant(db_session, "ADMIN", a)
        await grant(db_session, "ADMIN", b)
        data, by_id = await listing(api_client)
        assert set(by_id) == {str(a), str(b)}
        assert str(store_id(SOURCE_ONLY_STORE)) not in by_id
        assert (data["total_physical"], data["not_granted"]) == (2, 0)
        for entry in by_id.values():
            assert entry["access_granted"] is True
            o = entry["overview"]
            assert (o["tracked_products"], o["pending_approvals"], o["recent_receipts"]) == (0, 0, [])
        assert (await ledger(db_session)) == ([], [])

    async def test_the_list_and_the_single_store_overview_agree(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "ADMIN", a)
        _, by_id = await listing(api_client)
        single = (await api_client.get(f"/api/v1/inventory/stores/{a}/overview")).json()["data"]
        assert by_id[str(a)]["overview"] == single

    async def test_a_store_with_a_posted_receipt_keeps_its_balances_in_the_list(
        self, api_client, db_session, received,  # noqa: F811
    ):
        await received_and_mapped(api_client, db_session, received)
        sid = received.store_id
        before = (await api_client.get(f"/api/v1/inventory/stores/{sid}/items")).json()["items"]
        await grant(db_session, "ADMIN", store_id(PHYSICAL_STORE))
        _, by_id = await listing(api_client)
        o = by_id[str(sid)]["overview"]
        assert o["tracked_products"] == 2 and len(o["recent_receipts"]) == 1
        assert by_id[str(store_id(PHYSICAL_STORE))]["overview"]["tracked_products"] == 0
        assert (await api_client.get(f"/api/v1/inventory/stores/{sid}/items")).json()["items"] == before


class TestWhoSeesWhich:
    async def test_a_manager_sees_only_granted_stores(self, manager, api_client, db_session):  # noqa: F811
        a, b = store_id(PHYSICAL_STORE), store_id("86357232")
        await grant(db_session, "MANAGER", a)
        data, by_id = await listing(manager)
        assert set(by_id) == {str(a)}
        assert (data["total_physical"], data["not_granted"]) == (1, 0)
        assert str(b) not in (await manager.get(OVERVIEW)).text

    async def test_a_manager_with_no_grant_sees_an_empty_list(self, manager):
        data, by_id = await listing(manager)
        assert by_id == {} and data["total_physical"] == 0

    async def test_an_admin_sees_ungranted_active_stores_as_locked_entries_without_figures_and_cannot_open_them(
        self, api_client, db_session,  # noqa: F811
    ):
        a, b = store_id(PHYSICAL_STORE), store_id("86357232")
        await grant(db_session, "ADMIN", a)
        data, by_id = await listing(api_client)
        assert (data["total_physical"], data["not_granted"]) == (2, 1)
        assert by_id[str(a)]["access_granted"] is True and by_id[str(a)]["overview"] is not None
        assert by_id[str(b)]["access_granted"] is False and by_id[str(b)]["overview"] is None
        assert (await api_client.get(f"/api/v1/inventory/stores/{b}/overview")).status_code == 404
        assert (await api_client.post(zero_start_path(b), json={"reason": "x"})).status_code == 404

    async def test_a_plain_user_has_no_inventory_list(self, plain_user):
        assert (await plain_user.get(OVERVIEW)).status_code == 403


class TestOpeningStateIsNeverInvented:
    async def test_an_untouched_store_is_not_set_with_no_on_hand_figure(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "ADMIN", a)
        _, by_id = await listing(api_client)
        o = by_id[str(a)]["overview"]
        assert o["opening_state"] == "NOT_SET"
        assert o["on_hand_units"] is None                     # unknown, not zero
        assert (o["opening_counted_products"], o["opening_last_counted_by"], o["baseline"]) == (0, None, None)

    async def test_a_zero_start_is_a_declaration_not_a_physical_count(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "ADMIN", a)
        r = await api_client.post(zero_start_path(a), json={"reason": "new store, opened empty"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["created"] is True
        _, by_id = await listing(api_client)
        o = by_id[str(a)]["overview"]
        assert o["opening_state"] == "ZERO_START"
        assert o["opening_counted_products"] == 0 and o["opening_last_counted_by"] is None
        assert o["baseline"]["reason"] == "new store, opened empty" and o["baseline"]["initialized_by"] == "testadmin"

    async def test_entering_physical_counts_makes_the_store_counted_and_records_who_and_when(
        self, api_client, db_session,  # noqa: F811
    ):
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "ADMIN", a)
        body = {"count_date": "2026-10-05", "reason": "full shelf count",
                "entries": [{"item_code": LAGER_CODE, "quantity": 5, "description": "LAGER"}]}
        r = await api_client.post(f"/api/v1/inventory/stores/{a}/opening-counts", json=body)
        assert r.status_code == 201, r.text
        _, by_id = await listing(api_client)
        o = by_id[str(a)]["overview"]
        assert o["opening_state"] == "COUNTED" and o["opening_counted_products"] == 1
        assert (o["opening_last_counted_on"], o["opening_last_counted_by"]) == ("2026-10-05", "testadmin")
        assert float(o["on_hand_units"]) == 5

    async def test_a_zero_that_a_person_counted_is_counted_and_an_unset_store_is_not(
        self, api_client, db_session,  # noqa: F811
    ):
        a, b = store_id(PHYSICAL_STORE), store_id("86357232")
        await grant(db_session, "ADMIN", a)
        await grant(db_session, "ADMIN", b)
        body = {"count_date": "2026-10-05", "reason": "shelf empty",
                "entries": [{"item_code": LAGER_CODE, "quantity": 0, "description": "LAGER"}]}
        assert (await api_client.post(f"/api/v1/inventory/stores/{a}/opening-counts", json=body)).status_code == 201
        _, by_id = await listing(api_client)
        counted, unset = by_id[str(a)]["overview"], by_id[str(b)]["overview"]
        assert counted["opening_state"] == "COUNTED" and float(counted["on_hand_units"]) == 0
        assert unset["opening_state"] == "NOT_SET" and unset["on_hand_units"] is None


class TestStartAtZeroIsSafe:
    async def test_repeating_it_writes_nothing_more_and_keeps_the_first_reason(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "ADMIN", a)
        first = await api_client.post(zero_start_path(a), json={"reason": "first reason"})
        second = await api_client.post(zero_start_path(a), json={"reason": "a different reason"})
        assert (first.json()["data"]["created"], second.json()["data"]["created"]) == (True, False)
        assert second.json()["data"]["baseline"]["reason"] == "first reason"
        assert await baseline_rows(db_session) == 1

    async def test_it_writes_no_movement_and_no_receipt(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "ADMIN", a)
        assert (await api_client.post(zero_start_path(a), json={"reason": "go-live"})).status_code == 200
        assert (await ledger(db_session)) == ([], [])

    async def test_posted_receipts_movements_and_balances_are_unchanged(
        self, api_client, db_session, received,  # noqa: F811
    ):
        await received_and_mapped(api_client, db_session, received)
        sid = received.store_id
        before_ledger = await ledger(db_session)
        before_items = (await api_client.get(f"/api/v1/inventory/stores/{sid}/items")).json()["items"]
        assert before_ledger[0] and before_ledger[1]
        r = await api_client.post(zero_start_path(sid), json={"reason": "go-live", "initialization_date": "2026-10-09"})
        assert r.status_code == 200, r.text
        assert await ledger(db_session) == before_ledger                    # same rows, same ids, same quantities
        after_items = (await api_client.get(f"/api/v1/inventory/stores/{sid}/items")).json()["items"]
        assert {i["item_code"]: i["received"] for i in after_items} == {i["item_code"]: i["received"] for i in before_items}

    async def test_a_product_with_its_own_count_keeps_it(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "ADMIN", a)
        body = {"count_date": "2026-10-05", "reason": "shelf count",
                "entries": [{"item_code": LAGER_CODE, "quantity": 7, "description": "LAGER"}]}
        assert (await api_client.post(f"/api/v1/inventory/stores/{a}/opening-counts", json=body)).status_code == 201
        before = await ledger(db_session)
        r = await api_client.post(zero_start_path(a), json={"reason": "go-live"})
        assert r.status_code == 200, r.text
        assert await ledger(db_session) == before
        items = {i["item_code"]: i for i in (await api_client.get(f"/api/v1/inventory/stores/{a}/items")).json()["items"]}
        assert float(items[LAGER_CODE]["on_hand"]) == 7 and items[LAGER_CODE]["opening_source"] == "OPENING_COUNT"
        _, by_id = await listing(api_client)
        assert by_id[str(a)]["overview"]["opening_state"] == "COUNTED"      # a person's count outranks the declaration

    async def test_a_second_count_for_one_product_is_refused_and_changes_nothing(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "ADMIN", a)
        path = f"/api/v1/inventory/stores/{a}/opening-counts"
        body = {"count_date": "2026-10-05", "reason": "shelf count",
                "entries": [{"item_code": LAGER_CODE, "quantity": 5, "description": "LAGER"}]}
        assert (await api_client.post(path, json=body)).status_code == 201
        before = await ledger(db_session)
        again = await api_client.post(path, json={**body, "entries": [{**body["entries"][0], "quantity": 99}]})
        assert again.status_code >= 400
        assert await ledger(db_session) == before
        _, by_id = await listing(api_client)
        assert float(by_id[str(a)]["overview"]["on_hand_units"]) == 5

    async def test_a_manager_cannot_start_a_store_at_zero(self, manager, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "MANAGER", a)
        assert (await manager.post(zero_start_path(a), json={"reason": "x"})).status_code == 403
        assert await baseline_rows(db_session) == 0

    async def test_it_needs_a_reason_and_refuses_a_future_date(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "ADMIN", a)
        assert (await api_client.post(zero_start_path(a), json={"reason": "   "})).status_code == 422
        assert (await api_client.post(zero_start_path(a), json={})).status_code == 422
        future = (date.today() + timedelta(days=3)).isoformat()
        r = await api_client.post(zero_start_path(a), json={"reason": "x", "initialization_date": future})
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "future_date"
        assert await baseline_rows(db_session) == 0

    async def test_a_source_identity_cannot_be_started(self, api_client):  # noqa: F811
        r = await api_client.post(zero_start_path(store_id(SOURCE_ONLY_STORE)), json={"reason": "x"})
        assert r.status_code == 404
