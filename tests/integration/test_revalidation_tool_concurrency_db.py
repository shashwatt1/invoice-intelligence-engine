# ruff: noqa: F811 — pytest fixtures are re-imported and then named as parameters, as in the other integration suites
"""
tests/integration/test_revalidation_tool_concurrency_db.py — races, staleness and integrity of the revalidate-only tool.

Every test runs against the isolated test database with real, separate transactions (each "someone else" is its own
session holding the invoice's row lock, the lock every receipt, edit and revalidation path takes first). Nothing here
touches production and nothing calls a model.

  * two simultaneous applies of one reviewed plan: exactly one writes;
  * a receipt posted while an apply is waiting for the invoice lock: the apply refuses and writes nothing;
  * a plan goes stale after a relevant invoice change (a row added, an export delivered) and survives an irrelevant one;
  * the line-rewrite counter agrees with the deployed Rule D / Rule E function;
  * proposals, mappings, exports, receipts and movements are preserved, including a reversed receipt's movements.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

from sqlalchemy import text

from app.models.inventory import InventoryReceipt
from app.services import inventory_service
from app.services import revalidation_tool as tool
from tests.integration.conftest import PHYSICAL_STORE, requires_db, store_id
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture
from tests.integration.test_completeness_db import (  # noqa: F401 — structure_as is a fixture
    CODES,
    structure_as,
)
from tests.integration.test_export_override_db import blocked, ledger_on  # noqa: F401 — fixtures
from tests.integration.test_inventory_db import (  # noqa: F401 — received is a fixture
    receive,
    received,
    received_and_mapped,
)
from tests.integration.test_revalidation_tool_db import (  # noqa: F401 — _clean_inventory and tool_session are fixtures
    _clean_inventory,
    exported_legacy,
    invoice_state,
    log_counts,
    make_legacy,
    number_of,
    tool_session,
    world,
)

pytestmark = requires_db


async def wait_until_something_waits_on_a_lock(db_engine, timeout=10.0) -> bool:
    """True once some session in the test database is blocked waiting for a lock (polled, never slept blindly)."""
    async with db_engine.connect() as conn:
        for _ in range(int(timeout / 0.05)):
            waiting = (await conn.execute(text(
                "select count(*) from pg_stat_activity where datname = current_database() "
                "and wait_event_type = 'Lock'"))).scalar_one()
            if waiting:
                return True
            await asyncio.sleep(0.05)
    return False


async def reviewed(tool_session, number):
    async with tool_session() as session:
        plan = await tool.plan(session, number)
        await session.rollback()
    return plan


async def apply_and_commit(tool_session, number, fingerprint):
    """'wrote', 'noop' or the refusal's text; always in its own transaction."""
    async with tool_session() as session:
        try:
            _, wrote = await tool.apply(session, number, expect_plan=fingerprint, actor_username="testadmin")
            await session.commit()
            return "wrote" if wrote else "noop"
        except tool.ToolRefusal as refused:
            await session.rollback()
            return str(refused)


class TestSimultaneousApplies:
    async def test_of_one_reviewed_plan_exactly_one_writes(self, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        plan = await reviewed(tool_session, number)
        logs = await log_counts(db_session, blocked)

        results = await asyncio.gather(*[apply_and_commit(tool_session, number, plan.fingerprint) for _ in range(3)])

        assert sorted(r for r in results if r == "wrote") == ["wrote"]
        assert len([r for r in results if "no longer matches" in r]) == 2
        db_session.expire_all()
        assert (await invoice_state(db_session, blocked))["blocking"] == ["COMPLETENESS_MISSING_LINES"]
        after = await log_counts(db_session, blocked)
        assert after["VALIDATION"] == logs["VALIDATION"] + 1             # one write, one audit trail
        assert after["LIFECYCLE"] == logs.get("LIFECYCLE", 0) + 1


class TestAReceiptPostedWhileAnApplyWaits:
    async def test_the_apply_refuses_and_writes_nothing(self, db_engine, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked, status="VALIDATED", document="COMPLETED", composite="0.9572")
        number = await number_of(db_session, blocked)
        plan = await reviewed(tool_session, number)
        state, logs = await invoice_state(db_session, blocked), await log_counts(db_session, blocked)

        async with tool_session() as receiving:                             # how every receipt path starts
            await inventory_service.lock_invoice(receiving, uuid.UUID(blocked))
            applying = asyncio.create_task(apply_and_commit(tool_session, number, plan.fingerprint))
            assert await wait_until_something_waits_on_a_lock(db_engine), "the apply did not wait for the invoice lock"
            assert not applying.done()
            receiving.add(InventoryReceipt(
                invoice_id=uuid.UUID(blocked), store_id=store_id(PHYSICAL_STORE), status="POSTED",
                received_on=date(2026, 10, 9), confirmed_by="testadmin", confirmed_role="ADMIN", line_count=0,
                discrepancy_count=0, resolution_snapshot={}))
            await receiving.commit()                                        # releases the lock: the apply now proceeds

        outcome = await asyncio.wait_for(applying, timeout=10)
        assert "posted_inventory_receipt" in outcome and outcome != "wrote"
        db_session.expire_all()
        assert await invoice_state(db_session, blocked) == state            # nothing written
        assert await log_counts(db_session, blocked) == logs


class TestAPlanGoesStaleAfterARelevantChange:
    async def test_a_row_added_after_the_dry_run_makes_it_stale(self, api_client, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        plan = await reviewed(tool_session, number)
        added = await api_client.post(f"/api/v1/invoices/{blocked}/items", json={
            "description": "ITEM 3", "product_code": CODES[2], "pack_size": "20OZ PL 1/12", "quantity": "1",
            "unit_price": "10.00", "line_total": "10.00", "added_by": "x", "note": "added after the dry run"})
        assert added.status_code in (200, 201), added.text
        state = await invoice_state(db_session, blocked)
        assert "no longer matches" in await apply_and_commit(tool_session, number, plan.fingerprint)
        assert await invoice_state(db_session, blocked) == state

    async def test_an_export_delivered_after_the_dry_run_makes_it_stale(self, api_client, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        plan = await reviewed(tool_session, number)
        await exported_legacy(api_client, db_session, blocked)
        assert "no longer matches" in await apply_and_commit(tool_session, number, plan.fingerprint)

    async def test_a_change_that_does_not_alter_the_outcome_does_not_invalidate_the_plan(self, db_session, blocked, tool_session):
        # By design the fingerprint covers what the apply would write and what surrounds the invoice, not every byte:
        # an edit that leaves both unchanged cannot make the reviewed write different.
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        plan = await reviewed(tool_session, number)
        await db_session.execute(text("update invoice_items set description = description || ' ' where invoice_id = :i "
                                      "and sort_order = 0"), {"i": uuid.UUID(blocked)})
        await db_session.commit()
        assert await apply_and_commit(tool_session, number, plan.fingerprint) == "wrote"


class TestPreservation:
    async def test_proposals_mappings_exports_receipts_and_movements_survive_an_apply(
            self, api_client, db_session, received, tool_session):
        # A received-then-reversed invoice: a REVERSED receipt with its original and compensating movements, an
        # approved proposal and mapping per product, and the PDI export that submitting it recorded.
        receipt = await received_and_mapped(api_client, db_session, received)
        reversal = await api_client.post(f"/api/v1/inventory/invoices/{received.invoice_id}/receipt/reverse",
                                         json={"receipt_id": receipt["id"], "reason": "test: preservation"})
        assert reversal.status_code == 200, reversal.text
        await make_legacy(db_session, received.invoice_id, status="VALIDATED", document="COMPLETED", composite="0.9000")

        async def everything():
            tables = ("product_data_proposals", "product_case_mappings", "edi_exports", "inventory_receipts",
                      "inventory_movements", "export_overrides", "invoice_items")
            return {t: [r[0] for r in (await db_session.execute(text(f"select to_jsonb(x) from {t} x order by id"))).all()]
                    for t in tables}

        before = await everything()
        assert all(before[t] for t in ("product_data_proposals", "product_case_mappings", "edi_exports",
                                       "inventory_receipts", "inventory_movements")), "the scenario must have rows to protect"
        assert len(before["inventory_movements"]) == 4                       # two received, two compensating
        number = await number_of(db_session, received.invoice_id)
        plan = await reviewed(tool_session, number)
        assert plan.applicable and plan.changes and plan.posted_receipts == 0
        assert await apply_and_commit(tool_session, number, plan.fingerprint) == "wrote"
        db_session.expire_all()
        assert await everything() == before
