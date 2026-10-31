# ruff: noqa: F811 — pytest fixtures are re-imported and then named as parameters, as in the other integration suites
"""
tests/integration/test_revalidation_tool_db.py — the revalidate-only tool against real rows.

Existing invoices (stored before the completeness rules, so `blocking_checks` is NULL and nothing blocks their export)
are brought under the rules WITHOUT being edited. A dry run writes nothing (the database itself refuses a write in its
transaction); an apply changes only the invoice's status, confidence and blocking checks plus the document's lifecycle
status, and refuses an invoice with a POSTED receipt, a stale plan, a non-admin actor or an ambiguous number. Historical
EDI exports, proposals, mappings, lines and inventory are compared row for row before and after. Nothing calls a model.
"""

from __future__ import annotations

import argparse
import uuid
from contextlib import asynccontextmanager
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.inventory import InventoryReceipt
from app.models.processing_log import PipelineStage, ProcessingLog
from app.services import revalidation_tool as tool
from scripts import revalidate_invoices as cli
from tests.integration.conftest import PHYSICAL_STORE, requires_db, store_id
from tests.integration.fakes import extracted_invoice
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture
from tests.integration.test_completeness_db import (  # noqa: F401 — structure_as is a fixture
    CODES,
    line,
    structure_as,
    upload,
)
from tests.integration.test_export_override_db import blocked, ledger_on  # noqa: F401 — fixtures

pytestmark = requires_db


@pytest.fixture(autouse=True)
async def _clean_inventory(db_engine):
    yield
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE inventory_movements, inventory_receipts CASCADE"))


@pytest.fixture
def tool_session(db_engine):
    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def open_session():
        async with factory() as session:
            yield session
    open_session.factory = factory
    return open_session


async def number_of(db_session, invoice_id) -> str:
    return (await db_session.execute(text("select invoice_number from invoices where id = :i"),
                                     {"i": uuid.UUID(invoice_id)})).scalar_one()


async def make_legacy(db_session, invoice_id, *, status="REVIEW_REQUIRED", document="REVIEW_REQUIRED", composite="0.9500"):
    """An invoice as it was stored before the completeness rules: no blocking_checks, the old confidence."""
    await db_session.execute(text("update invoices set blocking_checks = null, status = :s, composite_confidence = :c "
                                  "where id = :i"), {"s": status, "c": composite, "i": uuid.UUID(invoice_id)})
    await db_session.execute(text("update documents set status = :d where id = (select document_id from invoices where id = :i)"),
                             {"d": document, "i": uuid.UUID(invoice_id)})
    await db_session.commit()


async def world(db_session, invoice_id) -> dict:
    """Everything the tool must not touch (and, for the invoice row, everything except the three fields it may set)."""
    i = uuid.UUID(invoice_id)

    async def rows(sql):
        return [r[0] for r in (await db_session.execute(text(sql), {"i": i})).all()]
    return {
        "invoice_other_columns": await rows("select to_jsonb(t) - 'status' - 'composite_confidence' - 'blocking_checks' - "
                                            "'updated_at' from invoices t where id = :i"),
        "items": await rows("select to_jsonb(t) from invoice_items t where invoice_id = :i order by sort_order"),
        "proposals": await rows("select to_jsonb(t) from product_data_proposals t order by id"),
        "mappings": await rows("select to_jsonb(t) from product_case_mappings t order by id"),
        "edi_exports": await rows("select to_jsonb(t) from edi_exports t where invoice_id = :i order by export_number"),
        "receipts": await rows("select to_jsonb(t) from inventory_receipts t where invoice_id = :i order by id"),
        "movements": await rows("select to_jsonb(t) from inventory_movements t where invoice_id = :i order by id"),
        "overrides": await rows("select to_jsonb(t) from export_overrides t order by id"),
    }


async def invoice_state(db_session, invoice_id) -> dict:
    row = (await db_session.execute(text(
        "select i.status, i.composite_confidence::text, i.blocking_checks, d.status from invoices i "
        "join documents d on d.id = i.document_id where i.id = :i"), {"i": uuid.UUID(invoice_id)})).one()
    return {"status": row[0], "composite": row[1], "blocking": row[2], "document": row[3]}


async def log_counts(db_session, invoice_id) -> dict:
    rows = (await db_session.execute(text(
        "select l.stage, count(*) from processing_logs l join invoices i on i.document_id = l.document_id "
        "where i.id = :i group by l.stage"), {"i": uuid.UUID(invoice_id)})).all()
    return dict(rows)


async def post_receipt(db_session, invoice_id):
    db_session.add(InventoryReceipt(
        invoice_id=uuid.UUID(invoice_id), store_id=store_id(PHYSICAL_STORE), status="POSTED", received_on=date(2026, 10, 9),
        confirmed_by="testadmin", confirmed_role="ADMIN", line_count=0, discrepancy_count=0, resolution_snapshot={}))
    await db_session.commit()


async def exported_legacy(api_client, db_session, blocked):
    """A legacy invoice (nothing blocks it) that was already exported to PDI once."""
    await make_legacy(db_session, blocked)
    exported = await api_client.get(f"/api/v1/invoices/{blocked}/export?format=pdi")
    assert exported.status_code == 200, exported.text
    return exported.text


class TestTheDryRun:
    async def test_it_shows_the_exact_before_and_after_and_writes_nothing(self, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked)
        before_world, before_state = await world(db_session, blocked), await invoice_state(db_session, blocked)
        before_logs = await log_counts(db_session, blocked)
        async with tool_session() as session:
            plan = await tool.plan(session, await number_of(db_session, blocked))
            await session.rollback()

        assert (plan.before.status, plan.before.composite_confidence, plan.before.blocking_checks,
                plan.before.document_status) == ("REVIEW_REQUIRED", "0.9500", None, "REVIEW_REQUIRED")
        assert plan.after.blocking_checks == ["COMPLETENESS_MISSING_LINES"]
        assert plan.after.status == "REVIEW_REQUIRED" and float(plan.after.composite_confidence) <= 0.60
        assert plan.completeness == "INCOMPLETE" and plan.missing_codes == [CODES[2][:11]]
        assert (plan.historical_edi_exports, plan.posted_receipts, plan.rows_that_would_be_rewritten) == (0, 0, 0)
        assert plan.changes and plan.applicable and len(plan.fingerprint) == 64
        assert await world(db_session, blocked) == before_world
        assert await invoice_state(db_session, blocked) == before_state
        assert await log_counts(db_session, blocked) == before_logs

    async def test_the_database_itself_refuses_a_write_inside_the_dry_run(self, db_session, blocked, tool_session):
        number = await number_of(db_session, blocked)
        async with tool_session() as session:
            await tool.plan(session, number)
            with pytest.raises(DBAPIError, match="read-only transaction"):
                await session.execute(text("update invoices set status = 'VALIDATED'"))
            await session.rollback()
        assert (await invoice_state(db_session, blocked))["status"] == "REVIEW_REQUIRED"

    async def test_it_reports_historical_exports_and_receipts_around_the_invoice(self, api_client, db_session, blocked, tool_session):
        await exported_legacy(api_client, db_session, blocked)
        await post_receipt(db_session, blocked)
        async with tool_session() as session:
            plan = await tool.plan(session, await number_of(db_session, blocked))
        assert (plan.historical_edi_exports, plan.posted_receipts) == (1, 1)
        assert tool.REFUSE_POSTED_RECEIPT in plan.refusals and not plan.applicable

    async def test_a_validated_invoice_that_would_fail_is_shown_leaving_validated(self, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked, status="VALIDATED", document="COMPLETED", composite="0.9572")
        async with tool_session() as session:
            plan = await tool.plan(session, await number_of(db_session, blocked))
        assert (plan.before.status, plan.before.document_status) == ("VALIDATED", "COMPLETED")
        assert (plan.after.status, plan.after.document_status) == ("REVIEW_REQUIRED", "REVIEW_REQUIRED")
        assert any("leave VALIDATED" in w for w in plan.warnings)


class TestApplyingTheReviewedPlan:
    async def apply(self, tool_session, number, plan_sha, actor="testadmin"):
        async with tool_session() as session:
            result = await tool.apply(session, number, expect_plan=plan_sha, actor_username=actor)
            await session.commit()
        return result

    async def reviewed(self, tool_session, number):
        async with tool_session() as session:
            plan = await tool.plan(session, number)
            await session.rollback()
        return plan

    async def test_it_sets_only_status_confidence_blocking_checks_and_the_document_status(self, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked, status="VALIDATED", document="COMPLETED", composite="0.9572")
        number = await number_of(db_session, blocked)
        before_world, logs_before = await world(db_session, blocked), await log_counts(db_session, blocked)
        plan = await self.reviewed(tool_session, number)

        _, wrote = await self.apply(tool_session, number, plan.fingerprint)
        assert wrote is True
        assert await invoice_state(db_session, blocked) == {
            "status": "REVIEW_REQUIRED", "composite": plan.after.composite_confidence,
            "blocking": ["COMPLETENESS_MISSING_LINES"], "document": "REVIEW_REQUIRED"}
        db_session.expire_all()
        assert await world(db_session, blocked) == before_world        # lines, proposals, mappings, exports, inventory
        logs_after = await log_counts(db_session, blocked)
        assert logs_after["VALIDATION"] == logs_before["VALIDATION"] + 1
        assert logs_after["LIFECYCLE"] == logs_before.get("LIFECYCLE", 0) + 1

    async def test_the_new_report_is_what_the_invoice_screen_reads_and_export_is_now_blocked(self, api_client, db_session, blocked, tool_session):
        await exported_legacy(api_client, db_session, blocked)         # legacy: exportable, and exported once
        number = await number_of(db_session, blocked)
        plan = await self.reviewed(tool_session, number)
        await self.apply(tool_session, number, plan.fingerprint)

        detail = (await api_client.get(f"/api/v1/invoices/{blocked}")).json()["data"]
        assert detail["blocking_checks"] == ["COMPLETENESS_MISSING_LINES"]
        assert detail["validation_report"]["completeness"]["status"] == "INCOMPLETE"
        assert detail["pdi_export_allowed"] is False
        for fmt in ("pdi", "txt", "csv"):
            assert (await api_client.get(f"/api/v1/invoices/{blocked}/export?format={fmt}")).status_code == 422, fmt
        assert (await api_client.get(f"/api/v1/invoices/{blocked}/export?format=txt&purpose=review")).status_code == 200

    async def test_a_historical_export_stays_exactly_as_it_was_delivered(self, api_client, db_session, blocked, tool_session):
        delivered = await exported_legacy(api_client, db_session, blocked)
        before = (await world(db_session, blocked))["edi_exports"]
        number = await number_of(db_session, blocked)
        plan = await self.reviewed(tool_session, number)
        await self.apply(tool_session, number, plan.fingerprint)
        db_session.expire_all()
        assert (await world(db_session, blocked))["edi_exports"] == before and len(before) == 1
        assert before[0]["content"] == delivered.replace("\r\n", "\r\n")      # the stored bytes are the delivered ones
        listing = (await api_client.get(f"/api/v1/invoices/{blocked}/edi-exports")).json()["data"]
        assert len(listing) == 1
        original = await api_client.get(f"/api/v1/edi-exports/{listing[0]['id']}/file")
        assert original.status_code == 200 and original.text == before[0]["content"]

    async def test_the_audit_entry_names_who_and_the_before_and_after(self, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        plan = await self.reviewed(tool_session, number)
        await self.apply(tool_session, number, plan.fingerprint)
        logs = (await db_session.execute(select(ProcessingLog).where(ProcessingLog.stage == PipelineStage.LIFECYCLE))).scalars().all()
        entry = next(log.payload for log in logs if (log.payload or {}).get("event") == tool.AUDIT_EVENT)
        assert entry["by"] == "testadmin" and entry["plan_sha256"] == plan.fingerprint
        assert entry["before"]["blocking_checks"] is None and entry["after"]["blocking_checks"] == ["COMPLETENESS_MISSING_LINES"]
        assert entry["rewrote_stored_lines"] is False and entry["invoice_number"] == number
        validation = list((await db_session.execute(select(ProcessingLog).where(
            ProcessingLog.stage == PipelineStage.VALIDATION).order_by(ProcessingLog.created_at))).scalars().all())[-1]
        assert validation.payload["revalidation_tool"] == {"by": "testadmin", "plan_sha256": plan.fingerprint}
        assert validation.payload["completeness"]["status"] == "INCOMPLETE"

    async def test_a_second_apply_finds_nothing_to_change_and_writes_nothing(self, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        first = await self.reviewed(tool_session, number)
        await self.apply(tool_session, number, first.fingerprint)
        logs = await log_counts(db_session, blocked)
        again = await self.reviewed(tool_session, number)
        assert not again.changes
        plan, wrote = await self.apply(tool_session, number, again.fingerprint)
        assert wrote is False and await log_counts(db_session, blocked) == logs


class TestWhatItRefuses:
    async def refuse(self, tool_session, number, plan_sha, actor="testadmin"):
        async with tool_session() as session:
            with pytest.raises(tool.ToolRefusal) as refused:
                await tool.apply(session, number, expect_plan=plan_sha, actor_username=actor)
            await session.rollback()
        return refused.value

    async def reviewed(self, tool_session, number):
        async with tool_session() as session:
            plan = await tool.plan(session, number)
            await session.rollback()
        return plan

    async def untouched(self, db_session, blocked, state, snapshot, logs):
        db_session.expire_all()
        assert await invoice_state(db_session, blocked) == state
        assert await world(db_session, blocked) == snapshot
        assert await log_counts(db_session, blocked) == logs

    async def test_an_invoice_with_a_posted_receipt_is_refused_and_nothing_changes(self, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked, status="VALIDATED", document="COMPLETED", composite="0.9572")
        await post_receipt(db_session, blocked)
        number = await number_of(db_session, blocked)
        state, snapshot, logs = await invoice_state(db_session, blocked), await world(db_session, blocked), await log_counts(db_session, blocked)
        plan = await self.reviewed(tool_session, number)
        assert plan.refusals == [tool.REFUSE_POSTED_RECEIPT]
        refused = await self.refuse(tool_session, number, plan.fingerprint)
        assert refused.reasons == [tool.REFUSE_POSTED_RECEIPT]
        await self.untouched(db_session, blocked, state, snapshot, logs)

    async def test_a_line_the_deployed_revalidation_would_rewrite_is_refused(self, db_session, blocked, tool_session, monkeypatch):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        state, snapshot, logs = await invoice_state(db_session, blocked), await world(db_session, blocked), await log_counts(db_session, blocked)
        monkeypatch.setattr(tool, "rows_the_deployed_revalidation_would_rewrite", lambda invoice: 2)
        plan = await self.reviewed(tool_session, number)
        assert plan.refusals == [tool.REFUSE_WOULD_REWRITE] and plan.rows_that_would_be_rewritten == 2
        refused = await self.refuse(tool_session, number, plan.fingerprint)
        assert refused.reasons == [tool.REFUSE_WOULD_REWRITE]
        await self.untouched(db_session, blocked, state, snapshot, logs)

    async def test_an_invoice_in_the_recycle_bin_is_refused(self, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked, document="BINNED")
        number = await number_of(db_session, blocked)
        plan = await self.reviewed(tool_session, number)
        assert plan.refusals == [tool.REFUSE_INACTIVE]
        assert (await self.refuse(tool_session, number, plan.fingerprint)).reasons == [tool.REFUSE_INACTIVE]
        assert (await invoice_state(db_session, blocked))["document"] == "BINNED"

    @pytest.mark.parametrize("actor", ["testmanager", "testuser", "nobody-by-that-name", None, ""])
    async def test_only_an_active_admin_can_apply(self, db_session, blocked, tool_session, actor):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        state, snapshot, logs = await invoice_state(db_session, blocked), await world(db_session, blocked), await log_counts(db_session, blocked)
        plan = await self.reviewed(tool_session, number)
        await self.refuse(tool_session, number, plan.fingerprint, actor=actor)
        await self.untouched(db_session, blocked, state, snapshot, logs)

    async def test_an_inactive_admin_cannot_apply(self, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        plan = await self.reviewed(tool_session, number)
        await db_session.execute(text("update users set is_active = false where username = 'testadmin'"))
        await db_session.commit()
        try:
            await self.refuse(tool_session, number, plan.fingerprint)
        finally:
            await db_session.execute(text("update users set is_active = true where username = 'testadmin'"))
            await db_session.commit()

    @pytest.mark.parametrize("sha", [None, "", "0" * 64, "not-a-fingerprint"])
    async def test_a_missing_or_wrong_plan_fingerprint_is_refused(self, db_session, blocked, tool_session, sha):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        state, snapshot, logs = await invoice_state(db_session, blocked), await world(db_session, blocked), await log_counts(db_session, blocked)
        await self.refuse(tool_session, number, sha)
        await self.untouched(db_session, blocked, state, snapshot, logs)

    async def test_a_plan_goes_stale_when_the_invoice_changes_after_the_dry_run(self, api_client, db_session, blocked, tool_session):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        plan = await self.reviewed(tool_session, number)
        await exported_legacy(api_client, db_session, blocked)          # an export lands after the dry run
        refused = await self.refuse(tool_session, number, plan.fingerprint)
        assert "no longer matches" in str(refused)
        assert (await invoice_state(db_session, blocked))["blocking"] is None

    async def test_an_unknown_invoice_number_is_refused(self, tool_session):
        async with tool_session() as session:
            with pytest.raises(tool.ToolRefusal) as refused:
                await tool.plan(session, "no-such-invoice")
        assert refused.value.reasons == [tool.REFUSE_NOT_FOUND]

    async def test_a_number_shared_by_two_invoices_is_refused_rather_than_guessed(self, structure_as, api_client, tool_session):
        for tag in ("one", "two"):
            structure_as(extracted_invoice(subtotal=30.0, grand_total=30.0, line_items=[line(1), line(2), line(3)]))
            await upload(api_client, f"twin-{tag}-{uuid.uuid4().hex[:6]}.pdf")
        async with tool_session() as session:
            with pytest.raises(tool.ToolRefusal) as refused:
                await tool.plan(session, "INV-001")
        assert refused.value.reasons == [tool.REFUSE_AMBIGUOUS]


class TestTheCommandLine:
    def args(self, **kw):
        return argparse.Namespace(**{"invoice_number": [], "apply": False, "actor": None, "expect_plan": [],
                                     "expect_database": None, "json": False, **kw})

    async def expect_database(self, tool_session):
        async with tool_session() as session:
            target = await cli.actual_target(session)
            await session.rollback()
        return target.expect_flag

    async def test_the_default_is_a_dry_run_that_prints_the_comparison_and_changes_nothing(
            self, db_session, blocked, tool_session, monkeypatch, capsys):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        monkeypatch.setattr(cli, "get_session_factory", lambda: tool_session.factory)
        state, snapshot = await invoice_state(db_session, blocked), await world(db_session, blocked)
        code = await cli.run(self.args(invoice_number=[number]))
        out = capsys.readouterr().out
        assert code == 0 and "DRY RUN (read-only)" in out
        assert "TARGET DATABASE: host=" in out and "(to apply here: --expect-database " in out
        assert "REVIEW_REQUIRED" in out and "COMPLETENESS_MISSING_LINES" in out and "plan sha256" in out
        assert "historical EDI exports: 0 | POSTED receipts: 0" in out
        assert "WRITTEN" not in out
        assert await invoice_state(db_session, blocked) == state and await world(db_session, blocked) == snapshot

    async def test_apply_with_the_printed_fingerprint_writes_and_says_so(
            self, db_session, blocked, tool_session, monkeypatch, capsys):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        monkeypatch.setattr(cli, "get_session_factory", lambda: tool_session.factory)
        async with tool_session() as session:
            sha = (await tool.plan(session, number)).fingerprint
        code = await cli.run(self.args(invoice_number=[number], apply=True, actor="testadmin", expect_plan=[f"{number}={sha}"],
                                       expect_database=await self.expect_database(tool_session)))
        assert code == 0 and ">>> WRITTEN" in capsys.readouterr().out
        assert (await invoice_state(db_session, blocked))["blocking"] == ["COMPLETENESS_MISSING_LINES"]

    async def test_a_posted_receipt_invoice_is_reported_refused_with_exit_code_2_in_a_dry_run_too(
            self, db_session, blocked, tool_session, monkeypatch, capsys):
        await make_legacy(db_session, blocked, status="VALIDATED", document="COMPLETED")
        await post_receipt(db_session, blocked)
        number = await number_of(db_session, blocked)
        monkeypatch.setattr(cli, "get_session_factory", lambda: tool_session.factory)
        code = await cli.run(self.args(invoice_number=[number]))
        out = capsys.readouterr().out
        assert code == 2 and "REFUSED (would not be applied): posted_inventory_receipt" in out
        assert "POSTED receipts: 1" in out

    async def test_apply_never_writes_the_refused_invoice_but_still_handles_the_others(
            self, db_session, blocked, tool_session, monkeypatch, capsys):
        await make_legacy(db_session, blocked)
        await post_receipt(db_session, blocked)
        number = await number_of(db_session, blocked)
        monkeypatch.setattr(cli, "get_session_factory", lambda: tool_session.factory)
        async with tool_session() as session:
            sha = (await tool.plan(session, number)).fingerprint
        state = await invoice_state(db_session, blocked)
        code = await cli.run(self.args(invoice_number=[number, "does-not-exist"], apply=True, actor="testadmin",
                                       expect_plan=[f"{number}={sha}", "does-not-exist=00"],
                                       expect_database=await self.expect_database(tool_session)))
        out = capsys.readouterr().out
        assert code == 2 and out.count("REFUSED") >= 2 and "WRITTEN" not in out
        assert await invoice_state(db_session, blocked) == state

    @pytest.mark.parametrize("wrong", ["not-this-host/invoice_test", "localhost/not_this_database", "no-slash", ""])
    async def test_apply_against_a_database_other_than_the_expected_one_is_refused_before_anything_is_read_or_written(
            self, db_session, blocked, tool_session, monkeypatch, capsys, wrong):
        await make_legacy(db_session, blocked)
        number = await number_of(db_session, blocked)
        monkeypatch.setattr(cli, "get_session_factory", lambda: tool_session.factory)
        async with tool_session() as session:
            sha = (await tool.plan(session, number)).fingerprint
        state, snapshot, logs = await invoice_state(db_session, blocked), await world(db_session, blocked), await log_counts(db_session, blocked)

        # the tool must not even look at an invoice: make any attempt to plan or apply fail the test
        async def forbidden(*a, **k):
            raise AssertionError("the tool read or wrote an invoice although the database was not the expected one")
        monkeypatch.setattr(tool, "apply", forbidden)
        monkeypatch.setattr(tool, "plan", forbidden)
        code = await cli.run(self.args(invoice_number=[number], apply=True, actor="testadmin", expect_plan=[f"{number}={sha}"],
                                       expect_database=wrong))
        out = capsys.readouterr().out
        assert code == 2 and "REFUSED:" in out and "Nothing was read or written." in out and "WRITTEN" not in out
        assert await invoice_state(db_session, blocked) == state and await world(db_session, blocked) == snapshot
        assert await log_counts(db_session, blocked) == logs

    async def test_the_right_host_with_the_wrong_database_name_says_which_part_differs(
            self, db_session, blocked, tool_session, monkeypatch, capsys):
        monkeypatch.setattr(cli, "get_session_factory", lambda: tool_session.factory)
        host = (await self.expect_database(tool_session)).split("/")[0]
        code = await cli.run(self.args(invoice_number=["x"], apply=True, actor="testadmin", expect_plan=["x=00"],
                                       expect_database=f"{host}/production"))
        out = capsys.readouterr().out
        assert code == 2 and "database 'invoice_test'" in out and "host" not in out.split("differs in:")[1]

    async def test_a_dry_run_that_names_the_wrong_database_is_refused_too(self, db_session, blocked, tool_session, monkeypatch, capsys):
        monkeypatch.setattr(cli, "get_session_factory", lambda: tool_session.factory)
        number = await number_of(db_session, blocked)
        code = await cli.run(self.args(invoice_number=[number], expect_database="somewhere-else/prod"))
        assert code == 2 and "REFUSED:" in capsys.readouterr().out
