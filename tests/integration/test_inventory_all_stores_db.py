"""
tests/integration/test_inventory_all_stores_db.py — Inventory store access, opening state and zero start, against Postgres.

Stores: 86357232 ("LG - RCM", the `received` fixture's store) and 90000001 ("AF Fixture Store") are physical;
47708760 is a source identity. Twelve more physical stores and one inactive store are added where a test needs
the production-sized list of fourteen. The seeded ADMIN is granted nothing by default.

  * an ADMIN reaches every ACTIVE physical store without any grant (list, store routes, supply options,
    opening counts); a MANAGER only the stores granted; a USER nothing; a source identity, an unknown id and
    an inactive store (without an explicit grant) are never reachable and never listed;
  * the opening state is never invented: NOT_SET (stock unknown, no on-hand figure) is not a ZERO_START (an
    administrator's declaration) and neither is COUNTED (a person's physical count);
  * the store's catalogue (the products its own POS lists) is part of the inventory even before any movement: those
    products need an opening count, or — after a zero start — sit at an explicit 0, with no movement fabricated;
  * a zero start gives every product without its own count an explicit zero opening (they stop needing one), keeps
    the product count, makes available units 0 for a store with no products, and lets later receipts add up from
    zero; posted receipts count exactly once; a person's own count always wins;
  * a store with movements or posted receipts may have held stock, so it needs an explicit acknowledgement;
  * a zero start is administrator-only, confirmed, idempotent and audited, and writes one baseline and nothing
    else: no movement, no receipt, no change to a posted receipt or a balance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.inventory import (
    MOVEMENT_RECEIPT,
    InventoryMovement,
    InventoryReceipt,
    StoreInventoryBaseline,
)
from app.models.store import Store
from app.models.store_product_reference import StoreProductReference
from app.repositories.store_repository import StoreRepository
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
FIRST_RECEIPT = "2026-10-09"           # the arrival date of the `received` fixture's receipt


def zero_start_path(sid):
    return f"/api/v1/inventory/stores/{sid}/zero-start"


def confirmed(reason="new store, opened with no stock", **extra):
    return {"reason": reason, "confirm": True, **extra}


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


async def overview_of(client, sid):
    r = await client.get(f"/api/v1/inventory/stores/{sid}/overview")
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def items_of(client, sid, **params):
    r = await client.get(f"/api/v1/inventory/stores/{sid}/items", params={"page_size": 500, **params})
    assert r.status_code == 200, r.text
    return {i["item_code"]: i for i in r.json()["items"]}


async def ledger(db_session):
    movements = (await db_session.execute(
        select(InventoryMovement.id, InventoryMovement.item_code, InventoryMovement.quantity)
        .order_by(InventoryMovement.id))).all()
    receipts = (await db_session.execute(
        select(InventoryReceipt.id, InventoryReceipt.status).order_by(InventoryReceipt.id))).all()
    return movements, receipts


async def baseline_rows(db_session):
    return (await db_session.execute(select(func.count()).select_from(StoreInventoryBaseline))).scalar()


async def store_rows(db_session):
    return (await db_session.execute(select(func.count()).select_from(Store))).scalar()


async def add_stores(db_session, count=12, *, inactive=False):
    """More physical stores (a display name makes a store physical). Returns their ids."""
    ids = []
    for i in range(count):
        store = await StoreRepository(db_session).create(display_name=f"AF Extra {'Closed' if inactive else 'Store'} {i:02d}")
        if inactive:
            store.status = "inactive"
        ids.append(store.id)
    await db_session.commit()
    return ids


async def add_received_products(db_session, sid, count=20, *, day=date(2026, 9, 1), quantity=3):
    """`count` products that only have receipt movements at the store: they exist, and no opening is recorded."""
    for i in range(count):
        db_session.add(InventoryMovement(
            store_id=sid, item_code=f"88800000{i:03d}", description=f"TEST PRODUCT {i:02d}",
            movement_type=MOVEMENT_RECEIPT, quantity=Decimal(quantity), business_date=day,
            idempotency_key=f"test:{sid}:{i}", actor_username="test"))
    await db_session.commit()
    return [f"88800000{i:03d}" for i in range(count)]


async def add_catalogue(db_session, sid, count=20, *, start=0):
    """`count` products the store's POS lists (store_product_references); no stock event of any kind."""
    for i in range(start, start + count):
        db_session.add(StoreProductReference(
            store_id=sid, item_code=f"77700000{i:03d}", scan_code_raw=f"77700000{i:03d}", description=f"CATALOGUE ITEM {i:02d}",
            source_file="test export.xlsx", imported_at=datetime.now(UTC)))
    await db_session.commit()
    return [f"77700000{i:03d}" for i in range(start, start + count)]


# ---------------------------------------------------------------------------------------------------------
class TestAdministratorsSeeEveryActivePhysicalStore:
    async def test_all_fourteen_physical_stores_are_listed_and_usable_with_no_grants_at_all(
        self, api_client, db_session,  # noqa: F811
    ):
        await add_stores(db_session, 12)
        closed = (await add_stores(db_session, 1, inactive=True))[0]
        before = await store_rows(db_session)
        data, by_id = await listing(api_client)
        assert len(by_id) == data["total_physical"] == 14
        assert data["not_granted"] == 0
        assert all(e["access_granted"] and e["overview"] is not None for e in by_id.values())
        assert str(store_id(SOURCE_ONLY_STORE)) not in by_id          # the unresolved source identity is never a store
        assert str(closed) not in by_id                                # nor is an inactive store
        assert len(set(by_id)) == len(data["stores"])                  # no duplicates
        assert await store_rows(db_session) == before                  # listing created or changed no store

    async def test_every_store_route_opens_for_an_administrator_without_a_grant(self, api_client, db_session):  # noqa: F811
        ids = await add_stores(db_session, 3)
        for sid in ids + [store_id(PHYSICAL_STORE)]:
            base = f"/api/v1/inventory/stores/{sid}"
            for path in (f"{base}/overview", f"{base}/items", f"{base}/invoices", f"{base}/approvals"):
                assert (await api_client.get(path)).status_code == 200, path
        supply = (await api_client.get("/api/v1/inventory/supply/options")).json()["data"]["stores"]
        assert {s["id"] for s in supply} >= {str(i) for i in ids}
        assert (await api_client.get(f"/api/v1/inventory/supply?store_id={ids[0]}")).status_code == 200

    async def test_an_administrator_can_record_opening_counts_at_a_store_without_a_grant(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        body = {"count_date": "2026-10-05", "reason": "full shelf count",
                "entries": [{"item_code": LAGER_CODE, "quantity": 5, "description": "LAGER"}]}
        r = await api_client.post(f"/api/v1/inventory/stores/{a}/opening-counts", json=body)
        assert r.status_code == 201, r.text
        assert (await overview_of(api_client, a))["opening_last_counted_by"] == "testadmin"

    async def test_a_source_identity_an_unknown_id_and_an_inactive_store_are_the_same_404(self, api_client, db_session):  # noqa: F811
        closed = (await add_stores(db_session, 1, inactive=True))[0]
        for sid in (store_id(SOURCE_ONLY_STORE), uuid.uuid4(), closed):
            for path in (f"/api/v1/inventory/stores/{sid}/overview", f"/api/v1/inventory/stores/{sid}/items"):
                r = await api_client.get(path)
                assert r.status_code == 404 and r.json()["error"]["message"] == "Store not found.", path
            assert (await api_client.post(zero_start_path(sid), json=confirmed())).status_code == 404

    async def test_an_explicit_grant_still_reaches_an_inactive_store(self, api_client, db_session):  # noqa: F811
        closed = (await add_stores(db_session, 1, inactive=True))[0]
        await grant(db_session, "ADMIN", closed)
        _, by_id = await listing(api_client)
        assert str(closed) in by_id
        assert (await api_client.get(f"/api/v1/inventory/stores/{closed}/overview")).status_code == 200

    async def test_the_list_and_the_single_store_overview_agree(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        _, by_id = await listing(api_client)
        assert by_id[str(a)]["overview"] == await overview_of(api_client, a)

    async def test_a_store_with_a_posted_receipt_keeps_its_balances_in_the_list(
        self, api_client, db_session, received,  # noqa: F811
    ):
        await received_and_mapped(api_client, db_session, received)
        sid = received.store_id
        before = await items_of(api_client, sid)
        _, by_id = await listing(api_client)
        o = by_id[str(sid)]["overview"]
        assert o["tracked_products"] == 2 and len(o["recent_receipts"]) == 1
        assert by_id[str(store_id(PHYSICAL_STORE))]["overview"]["tracked_products"] == 0
        assert await items_of(api_client, sid) == before


class TestManagersStayGrantRestricted:
    async def test_a_manager_sees_and_opens_only_granted_stores(self, manager, api_client, db_session):  # noqa: F811
        a, b = store_id(PHYSICAL_STORE), store_id("86357232")
        await grant(db_session, "MANAGER", a)
        data, by_id = await listing(manager)
        assert set(by_id) == {str(a)} and data["total_physical"] == 1
        assert str(b) not in (await manager.get(OVERVIEW)).text
        assert (await manager.get(f"/api/v1/inventory/stores/{a}/overview")).status_code == 200
        for path in ("overview", "items", "approvals", "invoices"):
            r = await manager.get(f"/api/v1/inventory/stores/{b}/{path}")
            assert r.status_code == 404 and r.json()["error"]["message"] == "Store not found."
        supply = (await manager.get("/api/v1/inventory/supply/options")).json()["data"]["stores"]
        assert [s["id"] for s in supply] == [str(a)]
        assert (await manager.get("/api/v1/inventory/stores")).json()["data"][0]["id"] == str(a)

    async def test_a_manager_without_grants_sees_nothing_and_cannot_start_a_store_at_zero(self, manager, db_session):  # noqa: F811
        data, by_id = await listing(manager)
        assert by_id == {} and data["total_physical"] == 0
        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "MANAGER", a)
        assert (await manager.post(zero_start_path(a), json=confirmed())).status_code == 403
        assert (await manager.get(f"{zero_start_path(a)}/preview")).status_code == 403
        assert await baseline_rows(db_session) == 0

    async def test_a_revoked_manager_loses_the_store_on_the_next_request(self, manager, api_client, db_session):  # noqa: F811
        from app.repositories.user_repository import UserRepository
        from app.services import store_access
        from tests.integration.conftest import KNOWN_USERS

        a = store_id(PHYSICAL_STORE)
        await grant(db_session, "MANAGER", a)
        assert (await manager.get(f"/api/v1/inventory/stores/{a}/overview")).status_code == 200
        users = UserRepository(db_session)
        await store_access.revoke_store_access(db_session, user_id=KNOWN_USERS["MANAGER"], store_id=a,
                                               actor=await users.get(KNOWN_USERS["ADMIN"]), reason="test: revoked")
        await db_session.commit()
        assert (await manager.get(f"/api/v1/inventory/stores/{a}/overview")).status_code == 404

    async def test_a_plain_user_has_no_inventory_at_all(self, plain_user):
        a = store_id(PHYSICAL_STORE)
        assert (await plain_user.get(OVERVIEW)).status_code == 403
        assert (await plain_user.get(f"/api/v1/inventory/stores/{a}/overview")).status_code == 403
        assert (await plain_user.post(zero_start_path(a), json=confirmed())).status_code == 403


class TestOpeningStateIsNeverInvented:
    async def test_an_untouched_store_is_not_set_with_no_on_hand_figure(self, api_client):  # noqa: F811
        o = await overview_of(api_client, store_id(PHYSICAL_STORE))
        assert o["opening_state"] == "NOT_SET"
        assert o["on_hand_units"] is None                     # unknown, not zero
        assert (o["opening_counted_products"], o["opening_last_counted_by"], o["baseline"]) == (0, None, None)

    async def test_entering_physical_counts_makes_the_store_counted_and_records_who_and_when(self, api_client):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        body = {"count_date": "2026-10-05", "reason": "full shelf count",
                "entries": [{"item_code": LAGER_CODE, "quantity": 5, "description": "LAGER"}]}
        assert (await api_client.post(f"/api/v1/inventory/stores/{a}/opening-counts", json=body)).status_code == 201
        o = await overview_of(api_client, a)
        assert o["opening_state"] == "COUNTED" and o["opening_counted_products"] == 1
        assert (o["opening_last_counted_on"], o["opening_last_counted_by"]) == ("2026-10-05", "testadmin")
        assert Decimal(o["on_hand_units"]) == 5

    async def test_a_counted_zero_is_counted_and_an_unset_store_is_not(self, api_client):  # noqa: F811
        a, b = store_id(PHYSICAL_STORE), store_id("86357232")
        body = {"count_date": "2026-10-05", "reason": "shelf empty",
                "entries": [{"item_code": LAGER_CODE, "quantity": 0, "description": "LAGER"}]}
        assert (await api_client.post(f"/api/v1/inventory/stores/{a}/opening-counts", json=body)).status_code == 201
        counted, unset = await overview_of(api_client, a), await overview_of(api_client, b)
        assert counted["opening_state"] == "COUNTED" and Decimal(counted["on_hand_units"]) == 0
        assert unset["opening_state"] == "NOT_SET" and unset["on_hand_units"] is None

    async def test_a_second_count_for_one_product_is_refused_and_changes_nothing(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        path = f"/api/v1/inventory/stores/{a}/opening-counts"
        body = {"count_date": "2026-10-05", "reason": "shelf count",
                "entries": [{"item_code": LAGER_CODE, "quantity": 5, "description": "LAGER"}]}
        assert (await api_client.post(path, json=body)).status_code == 201
        before = await ledger(db_session)
        again = await api_client.post(path, json={**body, "entries": [{**body["entries"][0], "quantity": 99}]})
        assert again.status_code >= 400
        assert await ledger(db_session) == before
        assert Decimal((await overview_of(api_client, a))["on_hand_units"]) == 5


class TestZeroStartInitializesEveryProduct:
    async def test_twenty_products_with_no_opening_all_start_at_an_explicit_zero_and_the_count_is_kept(
        self, api_client, db_session,  # noqa: F811
    ):
        sid = store_id(PHYSICAL_STORE)
        codes = await add_received_products(db_session, sid, 20, quantity=3)
        before = await overview_of(api_client, sid)
        assert (before["tracked_products"], before["opening_required"], before["opening_state"]) == (20, 20, "NOT_SET")
        assert before["on_hand_units"] is None                                   # unknown, not zero
        assert len(await items_of(api_client, sid, status="OPENING_STOCK_REQUIRED")) == 20
        ledger_before = await ledger(db_session)

        preview = (await api_client.get(f"{zero_start_path(sid)}/preview")).json()["data"]
        assert (preview["products"], preview["products_to_initialize"], preview["counted_products"]) == (20, 20, 0)
        assert (preview["opening_required_after"], Decimal(preview["available_units_after"])) == (0, 60)
        assert preview["has_history"] is True and preview["already_started"] is False
        assert await baseline_rows(db_session) == 0                              # a preview writes nothing

        r = await api_client.post(zero_start_path(sid), json=confirmed(acknowledge_prior_stock=True))
        assert r.status_code == 200, r.text
        assert (r.json()["data"]["created"], r.json()["data"]["products_initialized"]) == (True, 20)

        after = await overview_of(api_client, sid)
        assert after["tracked_products"] == 20                                   # the catalogue is never reduced
        assert (after["opening_required"], after["in_stock"], after["out_of_stock"]) == (0, 20, 0)
        assert after["opening_state"] == "ZERO_START" and Decimal(after["on_hand_units"]) == 60
        assert len(await items_of(api_client, sid, status="OPENING_STOCK_REQUIRED")) == 0
        rows = await items_of(api_client, sid)
        assert set(rows) == set(codes)
        assert all(Decimal(r["opening_quantity"]) == 0 and r["opening_source"] == "STORE_BASELINE"
                   and Decimal(r["on_hand"]) == 3 for r in rows.values())
        assert await ledger(db_session) == ledger_before                         # nothing fabricated or changed

    async def test_the_state_survives_a_refresh_because_it_is_derived_from_stored_rows(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await add_received_products(db_session, sid, 5)
        assert (await api_client.post(zero_start_path(sid), json=confirmed(acknowledge_prior_stock=True))).status_code == 200
        first = await overview_of(api_client, sid)
        _, by_id = await listing(api_client)
        assert by_id[str(sid)]["overview"] == first == await overview_of(api_client, sid)
        assert (first["opening_state"], first["opening_required"]) == ("ZERO_START", 0)

    async def test_a_store_without_products_shows_zero_available_not_unknown_once_started(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        preview = (await api_client.get(f"{zero_start_path(sid)}/preview")).json()["data"]
        assert preview["has_history"] is False and preview["basis"] == "INITIALIZATION_DATE"
        assert preview["effective_date"] == datetime.now(UTC).date().isoformat()      # the API works in UTC
        r = await api_client.post(zero_start_path(sid), json=confirmed())            # no acknowledgement needed
        assert r.status_code == 200, r.text
        o = await overview_of(api_client, sid)
        assert (o["tracked_products"], o["opening_required"], o["opening_state"]) == (0, 0, "ZERO_START")
        assert Decimal(o["on_hand_units"]) == 0
        assert o["baseline"]["basis"] == "INITIALIZATION_DATE" and o["baseline"]["reason"] == "new store, opened with no stock"

    async def test_receipts_after_a_zero_start_add_up_from_zero(self, api_client, db_session, received):  # noqa: F811
        sid = received.store_id
        assert (await api_client.post(zero_start_path(sid), json=confirmed())).status_code == 200      # nothing received yet
        assert Decimal((await overview_of(api_client, sid))["on_hand_units"]) == 0
        await received_and_mapped(api_client, db_session, received)
        rows = await items_of(api_client, sid)
        assert {c: (Decimal(r["on_hand"]), r["status"]) for c, r in rows.items()} == {
            LAGER_CODE: (36, "IN_STOCK"), ALE_CODE: (12, "IN_STOCK")}
        assert all(r["opening_source"] == "STORE_BASELINE" and Decimal(r["opening_quantity"]) == 0 for r in rows.values())
        o = await overview_of(api_client, sid)
        assert (o["opening_required"], Decimal(o["on_hand_units"])) == (0, 48)


class TestStoresWithHistory:
    async def test_a_store_with_receipts_needs_an_acknowledgement_and_a_confirmation(
        self, api_client, db_session, received,  # noqa: F811
    ):
        await received_and_mapped(api_client, db_session, received)
        sid = received.store_id
        no_confirm = await api_client.post(zero_start_path(sid), json={"reason": "go-live"})
        assert no_confirm.status_code == 422 and no_confirm.json()["error"]["detail"]["reason"] == "confirmation_required"
        no_ack = await api_client.post(zero_start_path(sid), json=confirmed("go-live"))
        detail = no_ack.json()["error"]["detail"]
        assert no_ack.status_code == 422 and detail["reason"] == "prior_stock_acknowledgement_required"
        assert (detail["posted_receipts"], detail["products"], detail["first_receipt_date"]) == (1, 2, FIRST_RECEIPT)
        assert await baseline_rows(db_session) == 0
        o = await overview_of(api_client, sid)
        assert (o["opening_state"], o["opening_required"], o["on_hand_units"]) == ("NOT_SET", 2, None)

    async def test_historical_receipts_count_exactly_once_from_the_first_receipt_date_and_nothing_is_rewritten(
        self, api_client, db_session, received,  # noqa: F811
    ):
        await received_and_mapped(api_client, db_session, received)
        sid = received.store_id
        ledger_before = await ledger(db_session)
        preview = (await api_client.get(f"{zero_start_path(sid)}/preview")).json()["data"]
        assert (preview["effective_date"], preview["basis"], preview["posted_receipts"]) == (
            FIRST_RECEIPT, "EARLIEST_POSTED_RECEIPT", 1)
        assert Decimal(preview["available_units_after"]) == 48

        r = await api_client.post(zero_start_path(sid), json=confirmed("go-live", acknowledge_prior_stock=True))
        assert r.status_code == 200, r.text
        baseline = r.json()["data"]["baseline"]
        assert baseline["baseline_date"] == FIRST_RECEIPT and baseline["basis"] == "EARLIEST_POSTED_RECEIPT"
        assert baseline["reason"].startswith("Prior stock acknowledged (1 posted receipt(s), 2 product(s)")
        assert baseline["reason"].endswith("go-live")
        rows = await items_of(api_client, sid)
        assert {c: Decimal(r["on_hand"]) for c, r in rows.items()} == {LAGER_CODE: 36, ALE_CODE: 12}   # once, not twice
        assert await ledger(db_session) == ledger_before
        assert (await overview_of(api_client, sid))["opening_required"] == 0

    async def test_a_start_date_before_the_first_receipt_is_refused_not_reordered(self, api_client, db_session, received):  # noqa: F811
        await received_and_mapped(api_client, db_session, received)
        sid = received.store_id
        early = (date(2026, 10, 9) - timedelta(days=30)).isoformat()
        r = await api_client.post(zero_start_path(sid), json=confirmed(acknowledge_prior_stock=True, initialization_date=early))
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "after_first_receipt"
        assert await baseline_rows(db_session) == 0

    async def test_an_existing_physical_count_takes_precedence_over_the_zero_start(self, api_client, db_session, received):  # noqa: F811
        await received_and_mapped(api_client, db_session, received)
        sid = received.store_id
        body = {"count_date": "2026-10-01", "reason": "full shelf count",
                "entries": [{"item_code": "999000000014", "quantity": 10, "description": "NORTHWIND LAGER 12PK"}]}
        assert (await api_client.post(f"/api/v1/inventory/stores/{sid}/opening-counts", json=body)).status_code == 201
        preview = (await api_client.get(f"{zero_start_path(sid)}/preview")).json()["data"]
        assert (preview["counted_products"], preview["products_to_initialize"]) == (1, 1)
        assert (await api_client.post(zero_start_path(sid), json=confirmed(acknowledge_prior_stock=True))).status_code == 200
        rows = await items_of(api_client, sid)
        assert (rows[LAGER_CODE]["opening_source"], Decimal(rows[LAGER_CODE]["on_hand"])) == ("OPENING_COUNT", 46)
        assert (rows[ALE_CODE]["opening_source"], Decimal(rows[ALE_CODE]["on_hand"])) == ("STORE_BASELINE", 12)
        assert (await overview_of(api_client, sid))["opening_state"] == "COUNTED"   # a person's count outranks the declaration


class TestZeroStartIsSafe:
    async def test_repeating_it_writes_nothing_more_keeps_the_first_reason_and_needs_no_confirmation(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        first = await api_client.post(zero_start_path(a), json=confirmed("first reason"))
        second = await api_client.post(zero_start_path(a), json={"reason": "a different reason"})
        assert (first.json()["data"]["created"], second.status_code, second.json()["data"]["created"]) == (True, 200, False)
        assert second.json()["data"]["baseline"]["reason"] == "first reason"
        assert second.json()["data"]["products_initialized"] == 0
        assert await baseline_rows(db_session) == 1
        assert (await api_client.get(f"{zero_start_path(a)}/preview")).json()["data"]["already_started"] is True

    async def test_it_writes_no_movement_and_no_receipt(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        assert (await api_client.post(zero_start_path(a), json=confirmed("go-live"))).status_code == 200
        assert (await ledger(db_session)) == ([], [])

    async def test_it_needs_a_reason_and_refuses_a_future_date(self, api_client, db_session):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        assert (await api_client.post(zero_start_path(a), json=confirmed("   "))).status_code == 422
        assert (await api_client.post(zero_start_path(a), json={"confirm": True})).status_code == 422
        future = (date.today() + timedelta(days=3)).isoformat()
        r = await api_client.post(zero_start_path(a), json=confirmed("x", initialization_date=future))
        assert r.status_code == 422 and r.json()["error"]["detail"]["reason"] == "future_date"
        assert (await api_client.post(zero_start_path(a), json={"reason": "x"})).json()["error"]["detail"]["reason"] \
            == "confirmation_required"
        assert await baseline_rows(db_session) == 0


class TestCatalogueProductsWithoutMovements:
    async def test_twenty_catalogue_products_are_visible_and_pending_before_a_zero_start(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        codes = await add_catalogue(db_session, sid, 20)
        o = await overview_of(api_client, sid)
        assert (o["tracked_products"], o["opening_required"], o["opening_state"]) == (20, 20, "NOT_SET")
        assert o["on_hand_units"] is None                                       # unknown, not zero
        rows = await items_of(api_client, sid)
        assert set(rows) == set(codes)
        assert all(r["status"] == "OPENING_STOCK_REQUIRED" and r["on_hand"] is None and r["last_movement"] is None
                   for r in rows.values())
        assert rows[codes[0]]["description"] == "CATALOGUE ITEM 00"
        assert (await ledger(db_session)) == ([], [])                           # showing them wrote nothing

    async def test_a_zero_start_gives_all_twenty_an_explicit_zero_and_fabricates_no_movement(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        codes = await add_catalogue(db_session, sid, 20)
        preview = (await api_client.get(f"{zero_start_path(sid)}/preview")).json()["data"]
        assert (preview["products"], preview["catalogue_only_products"], preview["products_to_initialize"]) == (20, 20, 20)
        assert (preview["has_history"], preview["posted_receipts"]) == (False, 0)      # a catalogue is not stock history
        assert (preview["opening_required_after"], Decimal(preview["available_units_after"])) == (0, 0)

        r = await api_client.post(zero_start_path(sid), json=confirmed())              # no acknowledgement is needed
        assert r.status_code == 200, r.text
        assert r.json()["data"]["products_initialized"] == 20

        o = await overview_of(api_client, sid)
        assert (o["tracked_products"], o["opening_required"], o["out_of_stock"], o["in_stock"]) == (20, 0, 20, 0)
        assert o["opening_state"] == "ZERO_START" and Decimal(o["on_hand_units"]) == 0
        rows = await items_of(api_client, sid)
        assert set(rows) == set(codes)
        assert all(Decimal(r["opening_quantity"]) == 0 and Decimal(r["on_hand"]) == 0 and r["status"] == "OUT_OF_STOCK"
                   and r["opening_source"] == "STORE_BASELINE" for r in rows.values())
        assert len(await items_of(api_client, sid, status="OPENING_STOCK_REQUIRED")) == 0
        assert (await ledger(db_session)) == ([], [])                                  # still no movement or receipt

    async def test_the_preview_and_the_final_baseline_use_the_same_population(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        await add_catalogue(db_session, sid, 8, start=0)
        moved = await add_received_products(db_session, sid, 5)          # 88800000xxx: movements only
        await add_catalogue(db_session, sid, 3, start=100)
        # A catalogue entry that also has movements is one product, not two.
        db_session.add(StoreProductReference(store_id=sid, item_code=moved[0], scan_code_raw=moved[0], description="DUPLICATE",
                                             source_file="test export.xlsx", imported_at=datetime.now(UTC)))
        await db_session.commit()
        preview = (await api_client.get(f"{zero_start_path(sid)}/preview")).json()["data"]
        assert (preview["products"], preview["catalogue_only_products"], preview["products_to_initialize"]) == (16, 11, 16)
        r = await api_client.post(zero_start_path(sid), json=confirmed(acknowledge_prior_stock=True))
        assert r.status_code == 200 and r.json()["data"]["products_initialized"] == preview["products_to_initialize"]
        after = await overview_of(api_client, sid)
        assert after["tracked_products"] == preview["products"] == len(await items_of(api_client, sid))
        assert Decimal(after["on_hand_units"]) == Decimal(preview["available_units_after"]) == 15      # 5 moved x 3

    async def test_a_receipt_for_a_catalogue_product_counts_exactly_once_from_zero(self, api_client, db_session, received):  # noqa: F811
        sid = received.store_id
        await add_catalogue(db_session, sid, 18)
        # The receipt's own two products are listed by the catalogue too: still two balances, not four.
        for code, name in ((LAGER_CODE, "LAGER"), (ALE_CODE, "ALE")):
            db_session.add(StoreProductReference(store_id=sid, item_code=code, scan_code_raw=code, description=name,
                                                 source_file="test export.xlsx", imported_at=datetime.now(UTC)))
        await db_session.commit()
        assert (await api_client.post(zero_start_path(sid), json=confirmed())).status_code == 200
        assert Decimal((await overview_of(api_client, sid))["on_hand_units"]) == 0
        await received_and_mapped(api_client, db_session, received)
        rows = await items_of(api_client, sid)
        assert len(rows) == 20
        assert (Decimal(rows[LAGER_CODE]["on_hand"]), rows[LAGER_CODE]["status"]) == (36, "IN_STOCK")
        assert (Decimal(rows[ALE_CODE]["on_hand"]), rows[ALE_CODE]["status"]) == (12, "IN_STOCK")
        others = [r for c, r in rows.items() if c not in (LAGER_CODE, ALE_CODE)]
        assert len(others) == 18 and all(Decimal(r["on_hand"]) == 0 and r["status"] == "OUT_OF_STOCK" for r in others)
        o = await overview_of(api_client, sid)
        assert (o["tracked_products"], o["opening_required"], o["in_stock"], o["out_of_stock"]) == (20, 0, 2, 18)
        assert Decimal(o["on_hand_units"]) == 48

    async def test_a_physical_count_of_a_catalogue_product_wins_and_is_not_duplicated(self, api_client, db_session):  # noqa: F811
        sid = store_id(PHYSICAL_STORE)
        codes = await add_catalogue(db_session, sid, 3)
        body = {"count_date": "2026-10-05", "reason": "shelf count",
                "entries": [{"item_code": codes[0], "quantity": 7, "description": "CATALOGUE ITEM 00"}]}
        assert (await api_client.post(f"/api/v1/inventory/stores/{sid}/opening-counts", json=body)).status_code == 201
        # A counted product is stock activity, so the rest being at zero is an acknowledged statement.
        assert (await api_client.post(zero_start_path(sid), json=confirmed())).status_code == 422
        assert (await api_client.post(zero_start_path(sid), json=confirmed(acknowledge_prior_stock=True))).status_code == 200
        rows = await items_of(api_client, sid)
        assert len(rows) == 3
        assert (rows[codes[0]]["opening_source"], Decimal(rows[codes[0]]["on_hand"])) == ("OPENING_COUNT", 7)
        assert all(rows[c]["opening_source"] == "STORE_BASELINE" and Decimal(rows[c]["on_hand"]) == 0 for c in codes[1:])

    async def test_one_stores_catalogue_never_appears_in_another(self, api_client, db_session):  # noqa: F811
        a, b = store_id(PHYSICAL_STORE), store_id("86357232")
        await add_catalogue(db_session, a, 4)
        assert (await overview_of(api_client, a))["tracked_products"] == 4
        assert (await overview_of(api_client, b))["tracked_products"] == 0
