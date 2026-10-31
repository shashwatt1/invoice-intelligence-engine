# ruff: noqa: F811 — pytest fixtures are re-imported and then named as parameters, as in the other integration suites
"""
tests/integration/test_export_race_db.py — an export in flight and a revalidation (the revalidate-only tool's apply).

A PDI, TXT or CSV request used to read the invoice, check the critical-check gate and deliver the file without any lock,
so a revalidation could commit a blocking state in between and the file still left. The export routes now share-lock the
invoice before reading it (and hold the lock to the end of the request); every writer takes the exclusive lock first.
Proven here with real, separate transactions in the isolated test database: an export that starts during an apply waits
and then sees the block; an apply waits for an export that has read the invoice but not yet decided; the historical
export is untouched. JSON and review copies deliver no file and take no lock.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.services import revalidation_tool as tool
from tests.integration.conftest import requires_db
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture
from tests.integration.test_completeness_db import structure_as  # noqa: F401 — fixture
from tests.integration.test_export_override_db import blocked, ledger_on  # noqa: F401 — fixtures
from tests.integration.test_revalidation_tool_concurrency_db import (
    apply_and_commit,
    reviewed,
    wait_until_something_waits_on_a_lock,
)
from tests.integration.test_revalidation_tool_db import (  # noqa: F401 — _clean_inventory and tool_session are fixtures
    _clean_inventory,
    exported_legacy,
    invoice_state,
    make_legacy,
    number_of,
    tool_session,
    world,
)

pytestmark = requires_db


class TestAnExportInFlightAndARevalidation:
    """The export routes share-lock the invoice while they produce a file, the apply takes the exclusive lock."""

    @pytest.mark.parametrize("fmt", ["pdi", "txt", "csv"])
    async def test_an_export_that_starts_during_an_apply_waits_and_then_sees_the_block(
            self, api_client, db_engine, db_session, blocked, tool_session, fmt):
        await make_legacy(db_session, blocked)                           # nothing blocks it yet: exportable
        number = await number_of(db_session, blocked)
        plan = await reviewed(tool_session, number)
        async with tool_session() as applying:
            _, wrote = await tool.apply(applying, number, expect_plan=plan.fingerprint, actor_username="testadmin")
            assert wrote                                                 # written, locked, NOT yet committed
            export = asyncio.create_task(api_client.get(f"/api/v1/invoices/{blocked}/export?format={fmt}"))
            assert await wait_until_something_waits_on_a_lock(db_engine), \
                f"the {fmt} export did not wait for the revalidation: it read the invoice before the block committed"
            assert not export.done()
            await applying.commit()
        response = await asyncio.wait_for(export, timeout=10)
        assert response.status_code == 422, response.text                # the block applies to it
        assert "critical checks" in response.json()["error"]["message"]
        assert (await db_session.execute(text("select count(*) from edi_exports"))).scalar_one() == 0

    async def test_an_apply_waits_for_an_export_that_is_in_flight_and_then_finds_the_plan_stale(
            self, api_client, db_engine, db_session, blocked, tool_session, monkeypatch):
        from app.api.v1 import exports as export_routes

        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        plan = await reviewed(tool_session, number)
        in_flight, release = asyncio.Event(), asyncio.Event()
        original = export_routes.invoice_commercial_resolution

        async def paused(*args, **kwargs):          # the invoice has been read; the critical-check gate has not run yet
            in_flight.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(export_routes, "invoice_commercial_resolution", paused)

        export = asyncio.create_task(api_client.get(f"/api/v1/invoices/{blocked}/export?format=pdi"))
        try:
            await asyncio.wait_for(in_flight.wait(), timeout=10)         # the export has read the invoice and is mid-request
            applying = asyncio.create_task(apply_and_commit(tool_session, number, plan.fingerprint))
            waited = await wait_until_something_waits_on_a_lock(db_engine)
            apply_finished_early = applying.done()
        finally:
            release.set()                                                # never leave a request parked on a failed test
        delivered = await asyncio.wait_for(export, timeout=10)
        outcome = await asyncio.wait_for(applying, timeout=10)
        assert waited and not apply_finished_early, "the apply did not wait for the export in flight"
        assert delivered.status_code == 200 and delivered.headers["x-edi-export-number"] == "1"
        assert "no longer matches" in outcome                            # an export landed meanwhile: re-run the dry run
        assert (await invoice_state(db_session, blocked))["blocking"] is None          # nothing was written
        fresh = await reviewed(tool_session, number)
        assert fresh.historical_edi_exports == 1
        assert await apply_and_commit(tool_session, number, fresh.fingerprint) == "wrote"
        assert (await api_client.get(f"/api/v1/invoices/{blocked}/export?format=pdi")).status_code == 422

    async def test_the_historical_export_is_untouched_by_all_of_it(self, api_client, db_session, blocked, tool_session):
        delivered = await exported_legacy(api_client, db_session, blocked)
        before = (await world(db_session, blocked))["edi_exports"]
        number = await number_of(db_session, blocked)
        plan = await reviewed(tool_session, number)
        assert await apply_and_commit(tool_session, number, plan.fingerprint) == "wrote"
        db_session.expire_all()
        assert (await world(db_session, blocked))["edi_exports"] == before
        listing = (await api_client.get(f"/api/v1/invoices/{blocked}/edi-exports")).json()["data"]
        original = await api_client.get(f"/api/v1/edi-exports/{listing[0]['id']}/file")
        assert original.status_code == 200 and original.text == before[0]["content"] == delivered
