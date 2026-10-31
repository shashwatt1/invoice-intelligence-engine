"""
tests/integration/test_vendor_merge_db.py — merging two vendor rows that are one supplier.

The duplicate is kept as a tombstone, never deleted; everything that pointed at it moves to the vendor that stays; the dry run
writes nothing; the apply is for an active ADMIN, with a reason, from the plan that was reviewed. A merge never changes a Retailed
mark, and an invoice that later prints the old spelling resolves to the vendor that stays.
"""
# ruff: noqa: F811 — pytest fixtures are reused by importing them, as the other integration tests do

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.models.document import Document
from app.models.invoice import Invoice
from app.models.processing_log import ProcessingLog
from app.models.product_data_proposal import (
    ENTITY_VENDOR_ITEM,
    FIELD_ITEM_CODE,
    SOURCE_UPC_ENTRY,
    STATUS_APPROVED,
    STATUS_PENDING,
    ProductDataProposal,
)
from app.models.vendor import Vendor, VendorIdentityReview, VendorMerge
from app.models.vendor_item_crosswalk import (
    CROSSWALK_CONFIRMED,
    CROSSWALK_RETIRED,
    VendorItemCrosswalk,
)
from app.models.vendor_request import VendorRequest
from app.services import vendor_identity_service
from app.services import vendor_merge_service as merge
from tests.integration.conftest import (
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    PHYSICAL_STORE,
    USER_USERNAME,
    requires_db,
    store_id,
)
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse
from tests.integration.test_inventory_db import (  # noqa: F401 — fixture reuse
    ALE,
    LAGER,
    approve,
    approve_submission,
    ledger_counts,
    received,
    submit,
)
from tests.integration.test_store_access_db import client_as
from tests.integration.test_vendor_delivery_db import invoice_of, upload

pytestmark = requires_db

ADMIN = "testadmin"
NOW = datetime(2026, 10, 1, tzinfo=UTC)


# The helpers return plain id holders: a rollback expires ORM objects, and a test rolls back often.
async def vendor(db_session, name, *, tax_id=None, confirmed=False) -> SimpleNamespace:
    row = Vendor(name=name, tax_id=tax_id)
    db_session.add(row)
    await db_session.commit()
    vendor_id = row.id
    if confirmed:
        await vendor_identity_service.confirm(db_session, vendor_id, display_name=name, basis="test fixture",
                                              reviewer="test:reviewer")
        await db_session.commit()
    return SimpleNamespace(id=vendor_id, name=name)


async def crosswalk(db_session, owner, code, item_code, *, state=CROSSWALK_CONFIRMED) -> SimpleNamespace:
    retired = state == CROSSWALK_RETIRED
    row = VendorItemCrosswalk(
        vendor_id=owner.id, vendor_item_code=code, vendor_item_code_raw=code, item_code=item_code, state=state,
        confirmed_by="test:reviewer", confirmed_at=NOW, evidence={},
        retired_at=NOW if retired else None, retired_by="test" if retired else None, retire_reason="test" if retired else None)
    db_session.add(row)
    await db_session.commit()
    return SimpleNamespace(id=row.id)


async def proposal(db_session, owner, *, status=STATUS_PENDING) -> SimpleNamespace:
    row = ProductDataProposal(
        store_id=store_id(PHYSICAL_STORE), entity_type=ENTITY_VENDOR_ITEM, entity_key=str(uuid.uuid4().int)[:10],
        field=FIELD_ITEM_CODE, proposed_value="12345678901", source=SOURCE_UPC_ENTRY, proposed_by="test", status=status,
        vendor_id=owner.id)
    db_session.add(row)
    await db_session.commit()
    return SimpleNamespace(id=row.id)


async def plan_of(db_session, winner, loser, **options):
    db_session.expire_all()
    return await merge.plan(db_session, winner.id, loser.id, merge.MergeOptions(**options))


async def apply(db_session, winner, loser, *, plan=None, actor=ADMIN, basis="the same supplier", **options):
    plan = plan or await plan_of(db_session, winner, loser, **options)
    await db_session.rollback()
    try:
        result, row = await merge.apply(db_session, winner.id, loser.id, actor_username=actor, basis=basis,
                                        expect_plan=plan.fingerprint, options=merge.MergeOptions(**options))
        record = SimpleNamespace(loser_vendor_id=row.loser_vendor_id, winner_vendor_id=row.winner_vendor_id,
                                 merged_by=row.merged_by, basis=row.basis, plan_sha256=row.plan_sha256, details=row.details)
    except merge.VendorMergeRefusal:
        await db_session.rollback()
        raise
    await db_session.commit()
    return result, record


async def counts(db_session, model) -> int:
    return (await db_session.execute(select(func.count()).select_from(model))).scalar()


class TestTheDryRun:
    async def test_it_lists_what_would_move_and_writes_nothing(self, api_client, app, db_session):
        winner = await vendor(db_session, "T.J. Sheehan Distributing", confirmed=True)
        loser = await vendor(db_session, "TJ SHEEHAN")
        for _ in range(2):
            await upload(api_client, app, vendor_id=loser.id, delivery_date="2026-10-22", printed="TJ Sheehan")
        await proposal(db_session, loser)
        await crosswalk(db_session, loser, "A1", "11111111111")
        request = VendorRequest(requested_name="x", normalized_name="x", requested_by="a", status="APPROVED",
                                vendor_id=loser.id, decided_at=NOW)
        db_session.add(request)
        await db_session.commit()

        plan = await plan_of(db_session, winner, loser)
        assert plan.refusals == []
        assert (len(plan.invoice_ids), len(plan.proposal_ids), len(plan.crosswalk_move), len(plan.request_ids)) == (2, 1, 1, 1)
        assert len(plan.document_ids) == 2 and len(plan.fingerprint) == 64
        await db_session.rollback()

        db_session.expire_all()
        assert (await db_session.get(Vendor, loser.id)).merged_into_id is None
        assert await counts(db_session, VendorMerge) == 0
        owners = (await db_session.execute(select(Invoice.vendor_id))).scalars().all()
        assert owners == [loser.id, loser.id]

    async def test_the_database_refuses_a_write_in_it(self, db_session):
        winner, loser = await vendor(db_session, "Acme Foods"), await vendor(db_session, "ACME")
        await merge.plan(db_session, winner.id, loser.id)
        with pytest.raises(Exception, match="read-only"):
            await db_session.execute(text("UPDATE vendors SET name = 'x'"))
        await db_session.rollback()

    async def test_the_fingerprint_changes_when_anything_it_covers_changes(self, api_client, app, db_session):
        winner, loser = await vendor(db_session, "Acme Foods"), await vendor(db_session, "ACME")
        first = await plan_of(db_session, winner, loser)
        await upload(api_client, app, vendor_id=loser.id, delivery_date="2026-10-22")
        second = await plan_of(db_session, winner, loser)
        assert first.fingerprint != second.fingerprint
        assert (await plan_of(db_session, winner, loser, allow_different_names=True)).fingerprint != second.fingerprint


class TestApplying:
    async def test_everything_moves_and_the_duplicate_stays_as_a_tombstone(self, api_client, app, db_session):
        winner = await vendor(db_session, "T.J. Sheehan Distributing", confirmed=True)
        loser = await vendor(db_session, "TJ SHEEHAN")
        accepted = await upload(api_client, app, vendor_id=loser.id, delivery_date="2026-10-22", printed="TJ Sheehan")
        invoice_id = (await invoice_of(api_client, accepted))["invoice_id"]
        pending = await proposal(db_session, loser)
        decided = await proposal(db_session, loser, status=STATUS_APPROVED)
        moved = await crosswalk(db_session, loser, "A1", "11111111111")
        redundant = await crosswalk(db_session, loser, "B2", "22222222222")
        await crosswalk(db_session, winner, "B2", "22222222222")
        retired_before = await crosswalk(db_session, loser, "C3", "33333333333", state=CROSSWALK_RETIRED)
        reviews_before = (await db_session.execute(select(func.count()).select_from(VendorIdentityReview).where(
            VendorIdentityReview.vendor_id == loser.id))).scalar()

        plan, record = await apply(db_session, winner, loser)

        db_session.expire_all()
        assert (await db_session.get(Vendor, loser.id)).merged_into_id == winner.id
        assert (await db_session.get(Vendor, winner.id)).merged_into_id is None
        assert (await db_session.get(Invoice, uuid.UUID(invoice_id))).vendor_id == winner.id
        assert {(await db_session.get(ProductDataProposal, p.id)).vendor_id for p in (pending, decided)} == {winner.id}
        assert (await db_session.get(ProductDataProposal, decided.id)).status == STATUS_APPROVED
        assert (await db_session.execute(select(Document.chosen_vendor_id))).scalar() == winner.id
        assert (await db_session.get(VendorItemCrosswalk, moved.id)).vendor_id == winner.id
        assert (await db_session.get(VendorItemCrosswalk, moved.id)).state == CROSSWALK_CONFIRMED
        gone = await db_session.get(VendorItemCrosswalk, redundant.id)
        assert (gone.state, gone.vendor_id, gone.retired_by) == (CROSSWALK_RETIRED, winner.id, ADMIN)
        assert "merged into" in gone.retire_reason
        assert (await db_session.get(VendorItemCrosswalk, retired_before.id)).vendor_id == winner.id
        assert (await db_session.get(VendorItemCrosswalk, retired_before.id)).state == CROSSWALK_RETIRED

        reviews_after = (await db_session.execute(select(func.count()).select_from(VendorIdentityReview).where(
            VendorIdentityReview.vendor_id == loser.id))).scalar()
        assert reviews_after == reviews_before, "the audit history stays where it was made"
        assert (record.loser_vendor_id, record.winner_vendor_id, record.merged_by, record.basis, record.plan_sha256) == (
            loser.id, winner.id, ADMIN, "the same supplier", plan.fingerprint)
        assert record.details["invoice_ids"] == [invoice_id]
        logged = (await db_session.execute(select(ProcessingLog.payload).where(
            ProcessingLog.payload["event"].astext == merge.AUDIT_EVENT))).scalars().all()
        assert [(p["invoice_id"], p["to_vendor_id"], p["by"]) for p in logged] == [(invoice_id, str(winner.id), ADMIN)]
        assert await counts(db_session, Vendor) == 2, "nothing was deleted"

    async def test_a_retailed_invoice_stays_retailed_when_its_vendor_is_merged(self, api_client, db_session, received):
        loser = SimpleNamespace(id=(await db_session.execute(select(Vendor.id).where(Vendor.name == "Acme Corp"))).scalar_one())
        winner = await vendor(db_session, "Acme Corp Distributing", confirmed=True)
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        await submit(api_client, received.invoice_id)
        before = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]["completion"]
        assert before["status"] == "retailed"

        plan, _ = await apply(db_session, winner, loser)
        assert plan.retailed_invoices == 1

        after = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        assert after["completion"]["status"] == "retailed", "the attestation does not cover the vendor"
        assert after["vendor"]["id"] == str(winner.id)

    async def test_an_invoice_with_a_posted_receipt_is_included_only_when_the_operator_says_so(self, api_client, db_session, received):
        loser = SimpleNamespace(id=(await db_session.execute(select(Vendor.id).where(Vendor.name == "Acme Corp"))).scalar_one())
        winner = await vendor(db_session, "Acme Corp Distributing", confirmed=True)
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        preview = await submit(api_client, received.invoice_id)
        assert (await approve_submission(api_client, received.invoice_id, preview)).status_code == 200
        ledger = await ledger_counts(db_session)

        refused = await plan_of(db_session, winner, loser)
        assert refused.posted_receipt_invoices == 1
        assert any("with_a_posted_receipt" in r for r in refused.refusals), refused.refusals
        with pytest.raises(merge.VendorMergeRefusal, match="posted_receipt"):
            await apply(db_session, winner, loser)

        await apply(db_session, winner, loser, allow_received_invoices=True)
        db_session.expire_all()
        assert (await db_session.get(Invoice, uuid.UUID(received.invoice_id))).vendor_id == winner.id
        assert await ledger_counts(db_session) == ledger, "the receipt and its movements are untouched"
        after = (await api_client.get(f"/api/v1/invoices/{received.invoice_id}")).json()["data"]
        assert after["completion"]["status"] == "retailed"

    async def test_a_second_merge_of_the_same_vendor_is_refused(self, db_session):
        winner, loser = await vendor(db_session, "Acme Foods"), await vendor(db_session, "ACME")
        await apply(db_session, winner, loser)
        again = await plan_of(db_session, winner, loser)
        assert any(r.startswith("loser_already_merged_into") for r in again.refusals)


class TestAfterTheMerge:
    async def _merged(self, db_session):
        winner = await vendor(db_session, "Acme Corp Distributing", confirmed=True)
        loser = await vendor(db_session, "Acme Corp", tax_id="GB123")
        await apply(db_session, winner, loser)
        return winner, loser

    async def test_an_invoice_that_prints_the_old_spelling_resolves_to_the_vendor_that_stays(self, api_client, app, db_session):
        winner, loser = await self._merged(db_session)
        accepted = await upload(api_client, app)                  # ADMIN, no choice: the vendor is what the document prints
        assert accepted.status_code == 202, accepted.text
        detail = await invoice_of(api_client, accepted)
        assert detail["vendor"]["id"] == str(winner.id)
        assert await counts(db_session, Vendor) == 2, "the duplicate was not created again"

    async def test_the_picker_and_the_vendor_master_no_longer_list_it(self, api_client, db_session):
        winner, loser = await self._merged(db_session)
        options = (await api_client.get("/api/v1/vendors/options")).json()["data"]
        assert [o["id"] for o in options] == [str(winner.id)]
        listed = (await api_client.get("/api/v1/vendors")).json()
        assert [v["id"] for v in listed["items"]] == [str(winner.id)] and listed["total"] == 1

    async def test_a_picker_loaded_before_the_merge_still_works(self, app, api_client, db_session):
        winner, loser = await self._merged(db_session)
        accepted = await upload(api_client, app, vendor_id=loser.id, delivery_date="2026-10-22")
        assert accepted.status_code == 202, accepted.text
        assert (await invoice_of(api_client, accepted))["vendor"]["id"] == str(winner.id)

    async def test_choosing_the_merged_vendor_on_an_invoice_chooses_the_one_that_stays(self, api_client, app, db_session):
        winner, loser = await self._merged(db_session)
        detail = await invoice_of(api_client, await upload(api_client, app))
        chosen = await api_client.patch(f"/api/v1/invoices/{detail['invoice_id']}/vendor", json={"vendor_id": str(loser.id)})
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["data"]["vendor_id"] == str(winner.id)

    async def test_a_merged_vendor_cannot_be_confirmed_or_added_again_by_name(self, api_client, db_session):
        winner, loser = await self._merged(db_session)
        with pytest.raises(Exception, match="merged"):
            await vendor_identity_service.confirm(db_session, loser.id, display_name="Acme Corp", basis="x", reviewer="r")
        await db_session.rollback()
        manager = await client_as(api_client._transport.app, MANAGER_USERNAME, MANAGER_PASSWORD)
        again = await manager.post("/api/v1/vendors", json={"name": "ACME CORP DISTRIBUTING"})
        assert again.status_code == 422 and again.json()["error"]["detail"]["vendor_id"] == str(winner.id)
        await manager.aclose()


class TestFindingDuplicates:
    async def test_the_listing_groups_vendors_that_look_like_one_supplier_and_writes_nothing(self, db_engine, db_session, capsys):
        import importlib.util
        from pathlib import Path

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        confirmed = await vendor(db_session, "T.J. Sheehan Distributing", confirmed=True)
        twin = await vendor(db_session, "TJ SHEEHAN")
        await vendor(db_session, "Zenith Traders")
        gone = await vendor(db_session, "Acme Foods")
        await apply(db_session, await vendor(db_session, "Acme Foods Inc"), gone)          # already merged: not listed again

        spec = importlib.util.spec_from_file_location(
            "merge_vendors_script", Path(__file__).resolve().parents[2] / "scripts/merge_vendors.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
        assert await script.list_duplicates(factory) == 0

        out = capsys.readouterr().out
        assert "4 vendors; 1 group(s)" in out                     # the merged-away vendor is not counted
        assert str(confirmed.id) in out and str(twin.id) in out and str(gone.id) not in out
        assert "Zenith" not in out


class TestRefusals:
    async def _refused(self, db_session, winner, loser, expect, **options):
        refusals = (await plan_of(db_session, winner, loser, **options)).refusals
        assert any(expect in r for r in refusals), refusals
        with pytest.raises(merge.VendorMergeRefusal):
            await apply(db_session, winner, loser, **options)
        db_session.expire_all()
        assert (await db_session.get(Vendor, loser.id)).merged_into_id is None, "nothing was written"
        assert await counts(db_session, VendorMerge) == 0

    async def test_the_same_vendor_and_an_unknown_one(self, db_session):
        row = await vendor(db_session, "Acme Foods")
        assert "same_vendor" in (await merge.plan(db_session, row.id, row.id)).refusals
        await db_session.rollback()
        assert "winner_not_found" in (await merge.plan(db_session, uuid.uuid4(), row.id)).refusals
        await db_session.rollback()
        assert "loser_not_found" in (await merge.plan(db_session, row.id, uuid.uuid4())).refusals
        await db_session.rollback()

    async def test_a_confirmed_vendor_is_never_merged_into_an_unconfirmed_one(self, db_session):
        confirmed, unconfirmed = await vendor(db_session, "Acme Foods", confirmed=True), await vendor(db_session, "ACME")
        await self._refused(db_session, unconfirmed, confirmed, "loser_is_confirmed_but_winner_is_not")

    async def test_two_confirmed_vendors_need_an_explicit_acknowledgement(self, db_session):
        a, b = await vendor(db_session, "Acme Foods", confirmed=True), await vendor(db_session, "ACME Foods Co", confirmed=True)
        await self._refused(db_session, a, b, "both_vendors_are_confirmed")
        await apply(db_session, a, b, allow_both_confirmed=True)
        db_session.expire_all()
        assert (await db_session.get(Vendor, b.id)).merged_into_id == a.id

    async def test_different_tax_ids_and_names_with_nothing_in_common_need_acknowledgement(self, db_session):
        a, b = await vendor(db_session, "Acme Foods", tax_id="11-1"), await vendor(db_session, "ACME", tax_id="22-2")
        await self._refused(db_session, a, b, "different_tax_ids")
        c, d = await vendor(db_session, "Zenith Traders"), await vendor(db_session, "Quartz Supply Holdings")
        await self._refused(db_session, c, d, "no_name_in_common")

    async def test_a_crosswalk_code_that_maps_to_different_items_is_a_conflict_a_person_must_decide(self, db_session):
        winner, loser = await vendor(db_session, "Acme Foods"), await vendor(db_session, "ACME")
        await crosswalk(db_session, winner, "X9", "11111111111")
        await crosswalk(db_session, loser, "X9", "22222222222")
        await self._refused(db_session, winner, loser, "crosswalk_conflict")

    async def test_a_merged_winner_points_at_the_vendor_to_use_instead(self, db_session):
        a, b, c = await vendor(db_session, "Acme Foods"), await vendor(db_session, "ACME"), await vendor(db_session, "Acme Foods Inc")
        await apply(db_session, a, b)
        refusals = (await plan_of(db_session, b, c)).refusals
        assert any(r.startswith("winner_was_merged_into") and str(a.id) in r for r in refusals)

    async def test_the_apply_needs_an_admin_a_basis_and_the_reviewed_plan(self, db_session):
        winner, loser = await vendor(db_session, "Acme Foods"), await vendor(db_session, "ACME")
        plan = await plan_of(db_session, winner, loser)
        await db_session.rollback()
        for kwargs in ({"actor": MANAGER_USERNAME}, {"actor": USER_USERNAME}, {"actor": "nobody"}, {"actor": None},
                       {"basis": "  "}, {"basis": None}):
            with pytest.raises(merge.VendorMergeRefusal):
                await apply(db_session, winner, loser, plan=plan, **kwargs)
        stale = plan.__class__(**{**plan.__dict__, "fingerprint": "0" * 64})
        with pytest.raises(merge.VendorMergeRefusal, match="no longer match"):
            await apply(db_session, winner, loser, plan=stale)
        db_session.expire_all()
        assert (await db_session.get(Vendor, loser.id)).merged_into_id is None
        assert await counts(db_session, VendorMerge) == 0

    async def test_an_invoice_that_arrived_after_the_review_makes_the_plan_stale(self, api_client, app, db_session):
        winner, loser = await vendor(db_session, "Acme Foods"), await vendor(db_session, "ACME")
        reviewed = await plan_of(db_session, winner, loser)
        await db_session.rollback()
        await upload(api_client, app, vendor_id=loser.id, delivery_date="2026-10-22")
        with pytest.raises(merge.VendorMergeRefusal, match="no longer match"):
            await apply(db_session, winner, loser, plan=reviewed)
        db_session.expire_all()
        assert (await db_session.get(Vendor, loser.id)).merged_into_id is None
