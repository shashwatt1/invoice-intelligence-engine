"""
tests/integration/test_approval_integrity_db.py — an approved case-mapping proposal has its mapping.

Approval writes the proposal's decision and the store's mapping in one transaction, and checks that the mapping
landed with the approved value and a link back to the proposal; if it did not, the whole approval fails and
nothing is committed. A mapping removed afterwards (by hand) leaves the proposal APPROVED on purpose — the audit
trail is never rewritten — and shows up as NOT_APPLIED instead of passing silently. Retries and duplicates change
nothing, other stores are never touched, and nothing here revives or repairs an old proposal.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, update

from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_data_proposal import STATUS_APPROVED, STATUS_PENDING, ProductDataProposal
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.services import proposal_service
from tests.integration.conftest import PHYSICAL_STORE, RCM_STORE, requires_db, store_id
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse

pytestmark = requires_db

CODE = "03600029145"
OTHER = "01200000129"


async def propose(db_session, store_code=PHYSICAL_STORE, units=12, code=CODE):
    p = await ProductDataProposalRepository(db_session).create(
        store_id=store_id(store_code), entity_type="case_mapping", entity_key=code, field="units_per_case",
        proposed_value=units, current_value=None, source="document_derived", proposed_by="testadmin",
        evidence={"invoice_description": "SYNTH COLA"})
    await db_session.commit()
    return SimpleNamespace(id=p.id, store_id=p.store_id)


async def fresh(db_session, proposal_id):
    """The proposal as the database holds it now (not a cached instance)."""
    return (await db_session.execute(select(ProductDataProposal).where(ProductDataProposal.id == proposal_id)
                                     .execution_options(populate_existing=True))).scalars().one()


async def approve_via_api(client, proposal):
    return await client.post(f"/api/v1/proposals/{proposal.id}/approve", json={"scope": "store"})


async def mapping_rows(db_session, store_code=PHYSICAL_STORE):
    return (await db_session.execute(select(ProductCaseMapping).where(
        ProductCaseMapping.store_id == store_id(store_code)))).scalars().all()


async def integrity(client, **params):
    r = await client.get("/api/v1/proposals/integrity", params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


class TestApprovalAndMappingAreOneThing:
    async def test_an_approval_leaves_the_mapping_linked_to_its_proposal(self, api_client, db_session):  # noqa: F811
        p = await propose(db_session)
        assert (await approve_via_api(api_client, p)).status_code == 200
        [row] = await mapping_rows(db_session)
        assert (row.item_code, row.units_per_case, row.approved_proposal_id) == (CODE, 12, p.id)
        assert await integrity(api_client) == []
        assert (await proposal_service.applied_states(db_session, [await fresh(db_session, p.id)])) \
            == {p.id: proposal_service.APPLIED}

    async def test_an_approval_whose_mapping_does_not_land_commits_nothing(self, db_session, monkeypatch):
        p = await propose(db_session)

        async def writes_nothing(self, **kwargs):                      # a defective write: returns, persists nothing
            return ProductCaseMapping(store_id=kwargs["store_id"], item_code=kwargs["item_code"],
                                      units_per_case=kwargs["units_per_case"], source="APPROVED")

        monkeypatch.setattr(ProductCaseMappingRepository, "upsert", writes_nothing)
        proposal = await fresh(db_session, p.id)
        with pytest.raises(proposal_service.ApprovalIntegrityError):
            await proposal_service.approve(db_session, proposal, reviewed_by="testadmin")
        await db_session.rollback()                                     # the caller never commits an exception
        assert (await fresh(db_session, p.id)).status == STATUS_PENDING
        assert await mapping_rows(db_session) == []

    async def test_a_retried_or_duplicate_approval_changes_nothing(self, api_client, db_session):  # noqa: F811
        p = await propose(db_session)
        assert (await approve_via_api(api_client, p)).status_code == 200
        before = [(m.id, m.units_per_case, m.approved_proposal_id) for m in await mapping_rows(db_session)]
        again = await approve_via_api(api_client, p)
        assert again.status_code == 422                                   # already decided: immutable
        assert [(m.id, m.units_per_case, m.approved_proposal_id) for m in await mapping_rows(db_session)] == before
        assert (await fresh(db_session, p.id)).status == STATUS_APPROVED

    async def test_two_proposals_for_one_product_end_with_the_later_approval_as_the_mapping(self, api_client, db_session):  # noqa: F811
        first, second = await propose(db_session, units=12), await propose(db_session, units=24)
        assert (await approve_via_api(api_client, first)).status_code == 200
        assert (await approve_via_api(api_client, second)).status_code == 200
        [row] = await mapping_rows(db_session)
        assert (row.units_per_case, row.approved_proposal_id) == (24, second.id)
        rows = [await fresh(db_session, i) for i in (first.id, second.id)]
        assert await proposal_service.applied_states(db_session, rows) == {
            first.id: proposal_service.SUPERSEDED, second.id: proposal_service.APPLIED}
        assert await integrity(api_client) == []                          # a superseded approval is history, not a gap


class TestARemovedMappingIsAVisibleGap:
    async def removed(self, api_client, db_session, **kw):  # noqa: F811
        p = await propose(db_session, **kw)
        assert (await approve_via_api(api_client, p)).status_code == 200
        await db_session.execute(delete(ProductCaseMapping).where(ProductCaseMapping.store_id == p.store_id))
        await db_session.commit()
        return p

    async def test_the_approved_proposal_with_no_mapping_is_reported_not_hidden_and_not_rewritten(self, api_client, db_session):  # noqa: F811
        p = await self.removed(api_client, db_session)
        [gap] = await integrity(api_client)
        assert (gap["proposal_id"], gap["item_code"], gap["approved_value"], gap["mapping_value"]) == (
            str(p.id), CODE, 12, None)
        kept = await fresh(db_session, p.id)
        assert (kept.status, kept.reviewed_by) == (STATUS_APPROVED, "testadmin")      # history untouched
        assert await mapping_rows(db_session) == []                                     # and nothing was repaired

    async def test_a_gap_whose_invoice_was_deleted_is_flagged_and_left_exactly_as_it_is(self, api_client, db_session):  # noqa: F811
        p = await propose(db_session)
        gone = uuid.uuid4()                                    # an invoice that no longer exists
        await db_session.execute(update(ProductDataProposal).where(ProductDataProposal.id == p.id).values(invoice_id=gone))
        await db_session.commit()
        assert (await approve_via_api(api_client, p)).status_code == 200
        await db_session.execute(delete(ProductCaseMapping).where(ProductCaseMapping.store_id == p.store_id))
        await db_session.commit()
        [gap] = await integrity(api_client)
        assert (gap["invoice_id"], gap["invoice_deleted"]) == (str(gone), True)
        kept = await fresh(db_session, p.id)
        assert (kept.status, kept.reviewed_by, kept.invoice_id) == (STATUS_APPROVED, "testadmin", gone)   # not revived, not removed
        assert await mapping_rows(db_session) == []                                                      # nothing was repaired

    async def test_a_mapping_with_a_different_value_is_a_gap_too(self, api_client, db_session):  # noqa: F811
        p = await propose(db_session)
        assert (await approve_via_api(api_client, p)).status_code == 200
        await ProductCaseMappingRepository(db_session).upsert(
            store_id=p.store_id, item_code=CODE, units_per_case=6, source="APPROVED")
        await db_session.commit()
        [gap] = await integrity(api_client)
        assert (gap["approved_value"], gap["mapping_value"]) == (12, 6)

    async def test_the_report_can_be_limited_to_one_store_and_other_stores_are_untouched(self, api_client, db_session):  # noqa: F811
        await self.removed(api_client, db_session, store_code=PHYSICAL_STORE)
        other = await propose(db_session, RCM_STORE, 24, OTHER)
        assert (await approve_via_api(api_client, other)).status_code == 200
        assert [g["item_code"] for g in await integrity(api_client)] == [CODE]
        assert [g["item_code"] for g in await integrity(api_client, store_id=str(store_id(PHYSICAL_STORE)))] == [CODE]
        assert await integrity(api_client, store_id=str(store_id(RCM_STORE))) == []
        assert [m.units_per_case for m in await mapping_rows(db_session, RCM_STORE)] == [24]

    async def test_reading_the_report_revives_and_changes_nothing(self, api_client, db_session):  # noqa: F811
        p = await self.removed(api_client, db_session)
        count = lambda: db_session.execute(select(func.count()).select_from(ProductDataProposal))  # noqa: E731
        before = (await count()).scalar()
        await integrity(api_client)
        await integrity(api_client)
        assert (await count()).scalar() == before
        assert await mapping_rows(db_session) == []
        assert (await fresh(db_session, p.id)).status == STATUS_APPROVED

    async def test_pending_and_rejected_proposals_are_never_gaps(self, api_client, db_session):  # noqa: F811
        await propose(db_session, units=12)
        rejected = await propose(db_session, units=18, code=OTHER)
        r = await api_client.post(f"/api/v1/proposals/{rejected.id}/reject", json={"note": "wrong"})
        assert r.status_code == 200
        assert await integrity(api_client) == []
