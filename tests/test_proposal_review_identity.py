"""
tests/test_proposal_review_identity.py — Data Review decisions are attributable and governed.

  * The reviewer (and a reviser) is always the signed-in account; a name in
    the request body is ignored.
  * A store-level case-mapping value cannot be approved around the Product
    Master: an unapproved/disputed Product Master mapping for the same item,
    or an approved one at a different value, refuses the approval — singly or
    as part of a batch (all or nothing). Rejecting it stays possible.
  * Rows carry their source invoice's number and date, and the reason a row
    needs an individual decision.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.api.v1 import proposals as api
from app.models.product_data_proposal import (
    ENTITY_CASE_MAPPING,
    FIELD_UNITS_PER_CASE,
    STATUS_PENDING,
)
from app.models.user import User, UserRole
from app.services.product_master import commercial_resolution as cr

STORE = uuid.uuid4()
INVOICE = uuid.uuid4()
CONFLICT_CODE, CLEAN_CODE = "08769200057", "08769283102"


def proposal(code, value, pid=None):
    return SimpleNamespace(
        id=pid or uuid.uuid4(), store_id=STORE, entity_type=ENTITY_CASE_MAPPING, entity_key=code,
        field=FIELD_UNITS_PER_CASE, proposed_value=value, current_value=None, source="operator_entered",
        source_file=None, source_sheet=None, source_row=None, invoice_id=INVOICE,
        evidence={"invoice_description": "TWISTED TEA"}, reason=None, proposed_by="testuser",
        status=STATUS_PENDING, reviewed_by=None, reviewed_at=None, review_note=None,
        created_at=datetime.now(UTC),
    )


class _Session:
    def __init__(self):
        self.committed = 0

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        return None


@pytest.fixture
def world(app, monkeypatch):
    from app.core.dependencies import require_authenticated_user
    from app.database.session import get_db

    rows: dict[uuid.UUID, SimpleNamespace] = {}
    calls: list[tuple] = []
    session = _Session()
    manager = User(id=uuid.uuid4(), username="barj", password_hash="x", role=UserRole.MANAGER.value, is_active=True)

    async def fake_db():
        yield session

    class Proposals:
        def __init__(self, _db):
            pass

        async def get(self, pid):
            return rows.get(pid)

    class Nothing:
        def __init__(self, _db):
            pass

        async def get(self, *_a):
            return None

    class Invoices:
        def __init__(self, _db):
            pass

        async def get(self, iid):
            return SimpleNamespace(id=iid, invoice_number="101497", invoice_date=date(2026, 9, 10))

    class Stores:
        def __init__(self, _db):
            pass

        async def get(self, sid):
            return SimpleNamespace(id=sid, label="PB Wolf", identity_status="confirmed", display_name="PB Wolf",
                                   address_summary=None, source_codes=[], kind="physical", source_identity=None)

    async def resolve(_db, store_id, codes):
        # The Product Master view of these items: the Half & Half conflict is
        # unapproved; the other is not in the Product Master at all.
        lines = {}
        for c in codes:
            if c == CONFLICT_CODE:
                lines[c] = cr.CodeResolution(c, cr.PATH_REQUIRES_MAPPING, None,
                                             unapproved=(("REVIEW_REQUIRED", "CONFLICT"),))
            else:
                lines[c] = cr.CodeResolution(c, cr.PATH_REQUIRES_MAPPING, None)
        return cr.CommercialResolution(lines, enabled=True)

    async def approve(_db, p, *, reviewed_by, note=None):
        calls.append(("approve", p.id, reviewed_by))
        p.status, p.reviewed_by = "APPROVED", reviewed_by
        return SimpleNamespace(proposal=p, applied_to=f"product_case_mappings:{p.store_id}:{p.entity_key}")

    async def reject(_db, p, *, reviewed_by, note=None):
        calls.append(("reject", p.id, reviewed_by))
        p.status, p.reviewed_by = "REJECTED", reviewed_by

    async def decide_many(_db, ps, *, approve_them, reviewed_by, note=None):
        calls.append(("decide_many", [p.id for p in ps], approve_them, reviewed_by))
        for p in ps:
            p.status, p.reviewed_by = ("APPROVED" if approve_them else "REJECTED"), reviewed_by
        return [SimpleNamespace(proposal=p, applied_to=None) for p in ps]

    async def revise(_db, p, *, proposed_value, proposed_by, note=None):
        calls.append(("revise", p.id, proposed_by))
        new = proposal(p.entity_key, proposed_value)
        new.proposed_by = proposed_by
        rows[new.id] = new
        p.status = "REJECTED"
        return new

    monkeypatch.setattr(api, "ProductDataProposalRepository", Proposals)
    monkeypatch.setattr(api, "ProductCaseMappingRepository", Nothing)
    monkeypatch.setattr(api, "InvoiceRepository", Invoices)
    monkeypatch.setattr(api, "StoreRepository", Stores)
    monkeypatch.setattr(api, "resolve_store_codes", resolve)
    monkeypatch.setattr(api.proposal_service, "approve", approve)
    monkeypatch.setattr(api.proposal_service, "reject", reject)
    monkeypatch.setattr(api.proposal_service, "decide_many", decide_many)
    monkeypatch.setattr(api.proposal_service, "revise", revise)
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[require_authenticated_user] = lambda: manager

    def add(code, value):
        p = proposal(code, value)
        rows[p.id] = p
        return p

    yield SimpleNamespace(add=add, calls=calls, session=session)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_authenticated_user, None)


class TestTheReviewerIsTheSignedInAccount:
    async def test_approve_and_reject_record_the_session_account_not_the_typed_name(self, client, world):
        clean, other = world.add(CLEAN_CODE, 4), world.add(CLEAN_CODE, 6)
        r = await client.post(f"/api/v1/proposals/{clean.id}/approve", json={"reviewed_by": "someone-else"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["proposal"]["reviewed_by"] == "barj"
        assert (await client.post(f"/api/v1/proposals/{other.id}/reject", json={})).status_code == 200
        assert world.calls == [("approve", clean.id, "barj"), ("reject", other.id, "barj")]

    async def test_a_bulk_decision_and_a_revision_record_the_session_account(self, client, world):
        a, b = world.add(CLEAN_CODE, 4), world.add(CLEAN_CODE, 4)
        r = await client.post("/api/v1/proposals/bulk-approve",
                              json={"proposal_ids": [str(a.id)], "reviewed_by": "typed-name"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["reviewed_by"] == "barj"
        r = await client.post(f"/api/v1/proposals/{b.id}/revise", json={"proposed_value": 6, "proposed_by": "x"})
        assert r.status_code == 200, r.text
        assert world.calls == [("decide_many", [a.id], True, "barj"), ("revise", b.id, "barj")]


class TestTheProductMasterIsNotBypassed:
    async def test_a_store_value_for_a_disputed_item_cannot_be_approved_but_can_be_rejected(self, client, world):
        conflict = world.add(CONFLICT_CODE, 18)
        r = await client.post(f"/api/v1/proposals/{conflict.id}/approve", json={})
        assert r.status_code == 422
        assert r.json()["error"]["detail"]["reason"] == "product_master_governs"
        assert "REVIEW_REQUIRED (CONFLICT)" in r.json()["error"]["message"]
        assert world.calls == [] and world.session.committed == 0
        assert (await client.post(f"/api/v1/proposals/{conflict.id}/reject", json={"note": "C-18 is one unit"})
                ).status_code == 200

    async def test_one_disputed_row_refuses_the_whole_bulk_approval(self, client, world):
        clean, conflict = world.add(CLEAN_CODE, 4), world.add(CONFLICT_CODE, 18)
        r = await client.post("/api/v1/proposals/bulk-approve",
                              json={"proposal_ids": [str(clean.id), str(conflict.id)]})
        assert r.status_code == 422
        failures = r.json()["error"]["detail"]["failures"]
        assert list(failures) == [str(conflict.id)]
        assert world.calls == [] and clean.status == STATUS_PENDING

    async def test_queue_rows_name_their_source_invoice_and_why_a_row_needs_an_individual_decision(
            self, client, world, monkeypatch):
        conflict, clean = world.add(CONFLICT_CODE, 18), world.add(CLEAN_CODE, 4)

        class Listing:
            def __init__(self, _db):
                pass

            async def list(self, **kw):
                return [conflict, clean]
        monkeypatch.setattr(api, "ProductDataProposalRepository", Listing)
        r = await client.get("/api/v1/proposals", params={"invoice_id": str(INVOICE)})
        assert r.status_code == 200, r.text
        rows = {row["entity_key"]: row for row in r.json()["items"]}
        assert rows[CLEAN_CODE]["invoice_number"] == "101497" and rows[CLEAN_CODE]["invoice_date"] == "2026-09-10"
        assert rows[CLEAN_CODE]["product_master_block"] is None
        assert "decide it in Product Master Approvals first" in rows[CONFLICT_CODE]["product_master_block"]


class TestTheGuardRule:
    def line(self, path, units=None, unapproved=(), scope="Global (distributor evidence, held under Item Sales · 47708760)"):
        return cr.CodeResolution("c", path, units, scope_label=scope, unapproved=unapproved)

    def test_an_approved_product_master_value_that_differs_blocks_and_one_that_agrees_does_not(self):
        governed = self.line(cr.PATH_PRODUCT_MASTER_GLOBAL, 4)
        assert "already governs this item at 4/case; this value (18)" in cr.store_value_block(governed, 18)
        assert cr.store_value_block(governed, 4) is None

    def test_a_conflict_or_an_unapproved_mapping_blocks_and_nothing_in_the_master_does_not(self):
        assert cr.store_value_block(self.line(cr.PATH_CONFLICT), 4)
        assert cr.store_value_block(self.line(cr.PATH_LEGACY_FALLBACK, 6, (("PENDING", "UNKNOWN"),)), 6)
        assert cr.store_value_block(self.line(cr.PATH_REQUIRES_MAPPING), 4) is None
        assert cr.store_value_block(None, 4) is None

    def test_the_resolver_records_unapproved_mappings_without_changing_its_answer(self):
        r = cr.resolve_code("c", global_=[cr.ScopedMapping("m", "p", "REVIEW_REQUIRED", "CONFLICT", None)],
                            legacy_units=18, global_scope=cr.Scope("g", "Global"))
        assert (r.path, r.units_per_case, r.unapproved) == (cr.PATH_LEGACY_FALLBACK, 18,
                                                           (("REVIEW_REQUIRED", "CONFLICT"),))
