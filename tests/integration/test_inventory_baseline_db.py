"""
tests/integration/test_inventory_baseline_db.py — store inventory baselines against real Postgres.

Receipts go through the real workflow (export + Retailed submits, the
inventory API approves), so pending, posted, late-approved and reversed
receipts are the real thing. The fixture invoice receives 36 units of
LAGER (3 cases x 12) and 12 of ALE (2 x 6) at store 86357232.

  * an existing store starts on its earliest posted receipt's arrival date
    and its historical receipts count once; a store with no receipts starts on
    the initialization date; a source identity is skipped;
  * a dry run writes nothing; a repeat run skips; a failure for one store
    leaves the others initialized;
  * product-level opening counts are never overwritten and take precedence;
  * pending receipts do not count; a receipt approved after initialization
    counts whether its arrival date is before or after the baseline date; a
    new product received after initialization counts; a reversal nets to zero;
  * only an active administrator initializes; a baseline is never edited or
    deleted.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import PermissionDeniedError, ValidationError
from app.models.inventory import (
    BASELINE_EARLIEST_RECEIPT,
    BASELINE_INITIALIZATION_DATE,
    ImmutableBaselineError,
    InventoryMovement,
    StoreInventoryBaseline,
)
from app.services import inventory_baseline as ib
from tests.integration.conftest import PHYSICAL_STORE, SOURCE_ONLY_STORE, requires_db, store_id
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.integration.test_inventory_db import (  # noqa: F401 — fixture reuse
    ALE,
    ALE_CODE,
    LAGER,
    LAGER_CODE,
    _clean_inventory,
    approve,
    approve_submission,
    grant,
    preview_of,
    receive,
    received,
    received_and_mapped,
    submit,
)

pytestmark = requires_db

INIT = date(2026, 10, 20)
REASON = "test: inventory go-live"


@pytest.fixture
async def posted(api_client, db_session, received):  # noqa: F811
    """The fixture invoice mapped, submitted and approved: a POSTED receipt arriving 9 Oct."""
    return await received_and_mapped(api_client, db_session, received)


@pytest.fixture
def factory(db_engine):
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


async def run(factory, *stores, apply=True, initialization_date=INIT, actor="testadmin", reason=REASON):
    return {r.store_id: r for r in await ib.run_initialization(
        factory, actor_username=actor, reason=reason, initialization_date=initialization_date,
        store_ids=list(stores) or None, apply=apply)}


async def items(client, sid):
    r = await client.get(f"/api/v1/inventory/stores/{sid}/items")
    assert r.status_code == 200, r.text
    return {i["item_code"]: i for i in r.json()["items"]}


async def baselines(db_session):
    return (await db_session.execute(select(func.count()).select_from(StoreInventoryBaseline))).scalar()


async def movement_count(db_session):
    return (await db_session.execute(select(func.count()).select_from(InventoryMovement))).scalar()


async def map_both(api_client, db_session, invoice_id):  # noqa: F811
    await approve(api_client, db_session, invoice_id, LAGER, 12)
    await approve(api_client, db_session, invoice_id, ALE, 6)


class TestInitialization:
    async def test_an_existing_store_starts_on_its_earliest_posted_receipt_and_history_counts_once(
        self, api_client, db_session, received, posted, factory,  # noqa: F811
    ):
        sid = received.store_id
        before = await items(api_client, sid)
        assert {c: (i["on_hand"], i["status"]) for c, i in before.items()} == {
            LAGER_CODE: (None, "OPENING_STOCK_REQUIRED"), ALE_CODE: (None, "OPENING_STOCK_REQUIRED")}
        moved = await movement_count(db_session)

        result = (await run(factory, sid))[sid]
        assert (result.outcome, result.basis, result.baseline_date) == (
            ib.INITIALIZED, BASELINE_EARLIEST_RECEIPT, date(2026, 10, 9))
        assert str(result.earliest_receipt_id) == posted["id"]

        after = await items(api_client, sid)
        assert {c: (float(i["on_hand"]), i["opening_source"], i["opening_date"]) for c, i in after.items()} == {
            LAGER_CODE: (36, "STORE_BASELINE", "2026-10-09"), ALE_CODE: (12, "STORE_BASELINE", "2026-10-09")}
        assert await movement_count(db_session) == moved               # nothing written to the ledger
        overview = (await api_client.get(f"/api/v1/inventory/stores/{sid}/overview")).json()["data"]
        assert overview["opening_required"] == 0 and overview["in_stock"] == 2
        assert overview["baseline"]["baseline_date"] == "2026-10-09"
        assert overview["baseline"]["basis"] == BASELINE_EARLIEST_RECEIPT
        assert overview["baseline"]["initialized_by"] == "testadmin"

    async def test_a_store_without_receipts_starts_on_the_initialization_date_and_a_source_identity_is_skipped(
        self, db_session, received, factory,  # noqa: F811
    ):
        a, code = store_id(PHYSICAL_STORE), store_id(SOURCE_ONLY_STORE)
        results = await run(factory, a, code)
        assert (results[a].outcome, results[a].basis, results[a].baseline_date) == (
            ib.INITIALIZED, BASELINE_INITIALIZATION_DATE, INIT)
        assert results[a].earliest_receipt_id is None
        assert results[code].outcome == ib.SKIPPED and "source identity" in results[code].detail
        assert await baselines(db_session) == 1

    async def test_a_dry_run_writes_nothing_and_a_repeat_run_skips_every_initialized_store(
        self, db_session, received, posted, factory,  # noqa: F811
    ):
        sid, a = received.store_id, store_id(PHYSICAL_STORE)
        dry = await run(factory, sid, a, apply=False)
        assert [dry[s].outcome for s in (sid, a)] == [ib.INITIALIZED, ib.INITIALIZED]     # would initialize
        assert await baselines(db_session) == 0
        await run(factory, sid, a)
        again = await run(factory, sid, a, initialization_date=date(2026, 10, 25))
        assert [again[s].outcome for s in (sid, a)] == [ib.SKIPPED, ib.SKIPPED]
        assert again[a].baseline_date == INIT                                  # the first run's, unchanged
        assert await baselines(db_session) == 2

    async def test_one_failed_store_does_not_block_the_others(
        self, db_session, received, posted, factory,  # noqa: F811
    ):
        sid, a = received.store_id, store_id(PHYSICAL_STORE)
        results = await run(factory, sid, a, initialization_date=date(2026, 10, 1))   # receipt is dated 9 Oct
        assert results[sid].outcome == ib.FAILED and "after the initialization date" in results[sid].detail
        assert results[a].outcome == ib.INITIALIZED
        rows = (await db_session.execute(select(StoreInventoryBaseline.store_id))).scalars().all()
        assert rows == [a]

    async def test_every_physical_store_by_default_and_the_script_reports_the_totals(
        self, received, factory, monkeypatch, capsys,  # noqa: F811
    ):
        spec = importlib.util.spec_from_file_location(
            "init_baselines", Path(__file__).resolve().parents[2] / "scripts/initialize_inventory_baselines.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        monkeypatch.setattr(script, "get_session_factory", lambda: factory)
        args = ["--actor", "testadmin", "--reason", REASON, "--initialization-date", "2026-10-20"]
        assert await script.main(args) == 0
        dry = capsys.readouterr().out
        assert dry.startswith("DRY RUN") and "WOULD INITIALIZE" in dry and "Failed: 0" in dry
        assert await script.main([*args, "--apply"]) == 0
        applied = capsys.readouterr().out
        assert applied.startswith("APPLIED") and "SKIPPED" in applied        # the source identity
        assert await script.main([*args, "--apply"]) == 0
        repeat = capsys.readouterr().out
        assert "Initialized: 0" in repeat and "INITIALIZED " not in repeat
        with pytest.raises(SystemExit):
            script.parse_args(["--actor", "testadmin", "--reason", REASON])  # no date: refused


class TestOpeningCountsAndReceipts:
    async def test_existing_product_opening_counts_are_never_overwritten_and_take_precedence(
        self, api_client, db_session, received, posted, factory,  # noqa: F811
    ):
        sid = received.store_id
        r = await api_client.post(f"/api/v1/inventory/stores/{sid}/opening-counts", json={
            "count_date": "2026-10-10", "reason": "shelf count", "entries": [{"item_code": LAGER_CODE, "quantity": 5}]})
        assert r.status_code == 201, r.text
        count_before = (await db_session.execute(select(InventoryMovement.quantity, InventoryMovement.business_date)
                        .where(InventoryMovement.movement_type == "OPENING_COUNT"))).all()
        await run(factory, sid)
        after = await items(api_client, sid)
        assert (float(after[LAGER_CODE]["on_hand"]), after[LAGER_CODE]["opening_source"]) == (5, "OPENING_COUNT")
        assert (float(after[ALE_CODE]["on_hand"]), after[ALE_CODE]["opening_source"]) == (12, "STORE_BASELINE")
        assert (await db_session.execute(select(InventoryMovement.quantity, InventoryMovement.business_date)
                .where(InventoryMovement.movement_type == "OPENING_COUNT"))).all() == count_before
        # A second count for LAGER is still refused; ALE may still get its own count, which then wins.
        twice = await api_client.post(f"/api/v1/inventory/stores/{sid}/opening-counts", json={
            "count_date": "2026-10-21", "reason": "again", "entries": [{"item_code": LAGER_CODE, "quantity": 9}]})
        assert twice.status_code == 422
        ale = await api_client.post(f"/api/v1/inventory/stores/{sid}/opening-counts", json={
            "count_date": "2026-10-21", "reason": "shelf count", "entries": [{"item_code": ALE_CODE, "quantity": 3}]})
        assert ale.status_code == 201, ale.text
        final = await items(api_client, sid)
        assert (float(final[ALE_CODE]["on_hand"]), final[ALE_CODE]["opening_source"]) == (3, "OPENING_COUNT")

    async def test_pending_receipts_do_not_count_and_late_approved_receipts_count_once(
        self, api_client, db_session, received, factory,  # noqa: F811
    ):
        sid = received.store_id
        result = (await run(factory, sid))[sid]                     # no receipts yet: starts on 20 Oct
        assert (result.basis, result.baseline_date) == (BASELINE_INITIALIZATION_DATE, INIT)

        await map_both(api_client, db_session, received.invoice_id)
        preview = await submit(api_client, received.invoice_id)    # SUBMITTED: pending, no stock effect
        assert await items(api_client, sid) == {}
        assert await movement_count(db_session) == 0

        # Approved after initialization, but the goods arrived on 15 Oct — before the baseline date.
        r = await approve_submission(api_client, received.invoice_id, preview, received_on="2026-10-15")
        assert r.status_code == 200, r.text
        new_products = await items(api_client, sid)                 # both products are new since initialization
        assert {c: float(i["on_hand"]) for c, i in new_products.items()} == {LAGER_CODE: 36, ALE_CODE: 12}

        # Reversing it nets to zero; nothing is lost or counted twice.
        receipt_id = (await preview_of(api_client, received.invoice_id))["receipt"]["id"]
        rev = await api_client.post(f"/api/v1/inventory/invoices/{received.invoice_id}/receipt/reverse",
                                    json={"receipt_id": receipt_id, "reason": "entered against the wrong store"})
        assert rev.status_code == 200, rev.text
        reversed_ = await items(api_client, sid)
        assert {c: (float(i["on_hand"]), i["status"]) for c, i in reversed_.items()} == {
            LAGER_CODE: (0, "OUT_OF_STOCK"), ALE_CODE: (0, "OUT_OF_STOCK")}

    async def test_a_receipt_arriving_after_the_baseline_date_counts(
        self, api_client, db_session, received, factory,  # noqa: F811
    ):
        sid = received.store_id
        await run(factory, sid)
        await map_both(api_client, db_session, received.invoice_id)
        preview = await submit(api_client, received.invoice_id)
        r = await approve_submission(api_client, received.invoice_id, preview, received_on="2026-10-22")
        assert r.status_code == 200, r.text
        assert {c: float(i["on_hand"]) for c, i in (await items(api_client, sid)).items()} == {
            LAGER_CODE: 36, ALE_CODE: 12}


class TestSafeguards:
    async def test_only_an_active_administrator_initializes_and_a_reason_is_required(
        self, db_session, received, factory,  # noqa: F811
    ):
        with pytest.raises(PermissionDeniedError):
            await run(factory, actor="testmanager")
        with pytest.raises(ValidationError):
            await run(factory, reason="   ")
        assert await baselines(db_session) == 0

    async def test_a_baseline_is_never_edited_or_deleted(self, db_session, received, factory):  # noqa: F811
        a = store_id(PHYSICAL_STORE)
        await run(factory, a)
        row = (await db_session.execute(select(StoreInventoryBaseline))).scalars().one()
        row.baseline_date = date(2026, 1, 1)
        with pytest.raises(ImmutableBaselineError):
            await db_session.flush()
        await db_session.rollback()
        row = (await db_session.execute(select(StoreInventoryBaseline))).scalars().one()
        await db_session.delete(row)
        with pytest.raises(ImmutableBaselineError):
            await db_session.flush()
        await db_session.rollback()
        assert await baselines(db_session) == 1
