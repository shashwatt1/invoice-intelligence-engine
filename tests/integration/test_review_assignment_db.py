"""
PM Approvals work assignment — tests/integration/test_review_assignment_db.py

An administrator hands out the review queue in order ("the first N to one reviewer, the next N to another") so two
people never work the same candidate. A manager may not decide on a candidate assigned to someone else; an
administrator may. Assignment never changes a candidate or its review version.

The test database keeps rows between tests, so every test starts by taking back all undecided work and seeds its
own candidates; counts are asserted on what the test handed out, never on the whole queue.
"""

from __future__ import annotations

import random

from app.models.product_master import MasterCommercialMapping
from app.services.product_master import shared_decisions
from tests.integration.conftest import MANAGER_PASSWORD, MANAGER_USERNAME, requires_db, user_id
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse
from tests.integration.test_data_team_user_permissions import _client_as

pytestmark = requires_db

BASE = "/api/v1/product-master/commercial"


async def _seed(db_session, n: int) -> list[str]:
    """n open candidates ready for review; returns their mapping ids."""
    ids = []
    for _ in range(n):
        code = "9" + "".join(random.choice("0123456789") for _ in range(10))
        product, _ = await shared_decisions.find_or_create_product(db_session, code)
        mapping = MasterCommercialMapping(
            product_id=product.id, store_id=None, pdi_item_code=code,
            commercial_unit_basis="UNIT_IS_SELLING_UNIT", units_accounted_for=12,
            approval_state="REVIEW_REQUIRED", source_snapshot={}, evidence={}, source_system="test:assignment")
        db_session.add(mapping)
        await db_session.flush()
        ids.append(str(mapping.id))
    await db_session.commit()
    return ids


async def _fresh(api_client):  # noqa: F811
    r = await api_client.post(f"{BASE}/assignments/unassign", json={})
    assert r.status_code == 200, r.text


async def _assigned_ids(api_client, who: str) -> list[str]:  # noqa: F811
    r = await api_client.get(BASE, params={"assigned_to": who, "review_status": "UNDECIDED", "page_size": 500})
    assert r.status_code == 200, r.text
    return [row["id"] for row in r.json()["items"]]


class TestHandingOutTheQueueInOrder:
    async def test_each_person_gets_the_next_block_in_queue_order(self, api_client, db_session):  # noqa: F811
        await _seed(db_session, 7)
        await _fresh(api_client)
        manager, admin = str(user_id("MANAGER")), str(user_id("ADMIN"))
        r = await api_client.post(f"{BASE}/assignments/in-order",
                                  json={"plan": [{"user_id": manager, "count": 4}, {"user_id": admin, "count": 3}]})
        assert r.status_code == 200, r.text
        blocks = r.json()["data"]["blocks"]
        assert [(b["requested"], b["assigned"]) for b in blocks] == [(4, 4), (3, 3)]
        assert blocks[0]["last_upc"] <= blocks[1]["first_upc"]           # contiguous, in order
        mine = await _assigned_ids(api_client, manager)
        assert len(mine) == 4 and not set(mine) & set(await _assigned_ids(api_client, admin))
        rows = (await api_client.get(BASE, params={"assigned_to": manager, "page_size": 500})).json()["items"]
        assert {row["assigned_to"] for row in rows} == {MANAGER_USERNAME}

    async def test_running_it_again_only_hands_out_what_is_left(self, api_client, db_session):  # noqa: F811
        await _seed(db_session, 4)
        await _fresh(api_client)
        manager, admin = str(user_id("MANAGER")), str(user_id("ADMIN"))
        first = await api_client.post(f"{BASE}/assignments/in-order", json={"plan": [{"user_id": manager, "count": 2}]})
        second = await api_client.post(f"{BASE}/assignments/in-order", json={"plan": [{"user_id": admin, "count": 2}]})
        assert first.status_code == second.status_code == 200
        assert not set(await _assigned_ids(api_client, manager)) & set(await _assigned_ids(api_client, admin))

    async def test_the_summary_counts_each_persons_work(self, api_client, db_session):  # noqa: F811
        await _seed(db_session, 3)
        await _fresh(api_client)
        manager = str(user_id("MANAGER"))
        await api_client.post(f"{BASE}/assignments/in-order", json={"plan": [{"user_id": manager, "count": 3}]})
        people = {p["username"]: p for p in (await api_client.get(f"{BASE}/assignments")).json()["data"]["people"]}
        assert people[MANAGER_USERNAME]["awaiting"] == 3

    async def test_only_managers_and_admins_can_be_given_work(self, api_client, db_session):  # noqa: F811
        r = await api_client.post(f"{BASE}/assignments/in-order",
                                  json={"plan": [{"user_id": str(user_id("USER")), "count": 1}]})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["detail"]["reason"] == "not_a_reviewer"


class TestNoTwoPeopleWorkTheSameCandidate:
    async def test_a_manager_cannot_decide_on_someone_elses_candidate(self, app, api_client, db_session):  # noqa: F811
        [candidate] = await _seed(db_session, 1)
        await api_client.post(f"{BASE}/assignments/reassign",
                              json={"mapping_ids": [candidate], "user_id": str(user_id("ADMIN"))})
        manager = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            version = (await manager.get(f"{BASE}/{candidate}")).json()["data"]["candidate"]["review_version"]
            r = await manager.post(f"{BASE}/{candidate}/approve", json={"note": "ok", "expected_review_version": version})
            assert r.status_code == 409, r.text
            assert r.json()["error"]["detail"]["reason"] == "assigned_elsewhere"
            # bulk approve is refused the same way, and nothing is approved
            r = await manager.post(f"{BASE}/bulk-approve", json={
                "items": [{"mapping_id": candidate, "expected_review_version": version}], "note": "ok"})
            assert r.status_code == 409, r.text
        finally:
            await manager.aclose()
        detail = (await api_client.get(f"{BASE}/{candidate}")).json()["data"]["candidate"]
        assert detail["approval_state"] == "REVIEW_REQUIRED" and detail["assigned_to"] is not None

    async def test_the_assigned_manager_decides_and_an_admin_always_may(self, app, api_client, db_session):  # noqa: F811
        mine, admins = await _seed(db_session, 2)
        await api_client.post(f"{BASE}/assignments/reassign",
                              json={"mapping_ids": [mine], "user_id": str(user_id("MANAGER"))})
        await api_client.post(f"{BASE}/assignments/reassign",
                              json={"mapping_ids": [admins], "user_id": str(user_id("MANAGER"))})
        manager = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            assert mine in await _assigned_ids(manager, "me")
            version = (await manager.get(f"{BASE}/{mine}")).json()["data"]["candidate"]["review_version"]
            r = await manager.post(f"{BASE}/{mine}/approve", json={"note": "12 per case", "expected_review_version": version})
            assert r.status_code == 200, r.text
        finally:
            await manager.aclose()
        # the admin may decide on a candidate assigned to the manager
        version = (await api_client.get(f"{BASE}/{admins}")).json()["data"]["candidate"]["review_version"]
        r = await api_client.post(f"{BASE}/{admins}/approve", json={"note": "12 per case", "expected_review_version": version})
        assert r.status_code == 200, r.text

    async def test_assigning_does_not_change_the_candidate_or_its_version(self, api_client, db_session):  # noqa: F811
        [candidate] = await _seed(db_session, 1)
        before = (await api_client.get(f"{BASE}/{candidate}")).json()["data"]["candidate"]
        await api_client.post(f"{BASE}/assignments/reassign",
                              json={"mapping_ids": [candidate], "user_id": str(user_id("MANAGER"))})
        after = (await api_client.get(f"{BASE}/{candidate}")).json()["data"]["candidate"]
        assert after["review_version"] == before["review_version"]
        assert after["approval_state"] == before["approval_state"] == "REVIEW_REQUIRED"
        assert (before["assigned_to"], after["assigned_to"]) == (None, MANAGER_USERNAME)


class TestOnlyAnAdministratorAssigns:
    async def test_a_manager_cannot_hand_out_work(self, app, api_client, db_session):  # noqa: F811
        manager = await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
        try:
            for path, body in (("in-order", {"plan": [{"user_id": str(user_id("MANAGER")), "count": 1}]}),
                               ("unassign", {}),
                               ("reassign", {"mapping_ids": [str(user_id("MANAGER"))], "user_id": None})):
                r = await manager.post(f"{BASE}/assignments/{path}", json=body)
                assert r.status_code == 403, (path, r.text)
            assert (await manager.get(f"{BASE}/assignments")).status_code == 200       # but may see the split
        finally:
            await manager.aclose()
