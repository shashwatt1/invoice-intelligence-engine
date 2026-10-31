# ruff: noqa: F811 — pytest fixtures are re-imported and then named as parameters, as in the other integration suites
"""
tests/integration/test_export_override_db.py — the admin export override, every export route, and the audit trail.

An extraction that misses a printed row is persisted REVIEW_REQUIRED with a failed critical check. Without an
override PDI, TXT and CSV all refuse it (JSON stays readable and says it is not export-ready; a labelled review copy
exists for TXT/CSV). An ADMIN's recorded, reasoned override lets that exact state be exported and changes nothing
about the invoice or its validation results. Nothing here calls a model or an OCR service.
"""

from __future__ import annotations

import csv
import io
import uuid

import pytest
from sqlalchemy import select, text

from app.core.config import get_settings
from app.models.edi_export import EdiExport
from app.models.export_override import ExportOverride
from app.models.invoice import Invoice
from app.models.processing_log import PipelineStage, ProcessingLog
from tests.integration.conftest import (
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    USER_PASSWORD,
    USER_USERNAME,
    requires_db,
)
from tests.integration.fakes import extracted_invoice
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse
from tests.integration.test_completeness_db import (  # noqa: F401 — fixture reuse
    CODES,
    line,
    structure_as,
    upload,
)
from tests.integration.test_export_api import confirm_mapping
from tests.integration.test_store_access_db import client_as

pytestmark = requires_db

REASON = "Vendor confirmed by phone that the third row was a credit that is not on this copy"


@pytest.fixture(autouse=True)
async def ledger_on(db_session, monkeypatch):
    # The ledger reads alembic_version, which the create_all test database lacks (see test_invoice_workflow_db).
    await db_session.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)"))
    await db_session.execute(text("INSERT INTO alembic_version VALUES ('0039') ON CONFLICT DO NOTHING"))
    await db_session.commit()
    monkeypatch.setenv("EDI_EXPORT_LEDGER", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    await db_session.rollback()
    await db_session.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await db_session.commit()


@pytest.fixture
async def blocked(structure_as, api_client, db_session):
    """An invoice that printed three rows but extracted two: REVIEW_REQUIRED, completeness failed, PDI-ready otherwise."""
    structure_as(extracted_invoice(subtotal=20.0, grand_total=20.0, line_items=[line(1), line(2)]))
    invoice_id, detail = await upload(api_client, f"blocked-{uuid.uuid4().hex[:8]}.pdf")
    for code in (CODES[0][:11], CODES[1][:11]):
        await confirm_mapping(api_client, db_session, invoice_id, item_code=code, units=12)
    detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
    assert "COMPLETENESS_MISSING_LINES" in detail["blocking_checks"]
    return invoice_id


@pytest.fixture
async def manager(app, api_client):
    client = await client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)
    yield client
    await client.aclose()


@pytest.fixture
async def plain_user(app, api_client):
    client = await client_as(app, USER_USERNAME, USER_PASSWORD)
    yield client
    await client.aclose()


def url(invoice_id, fmt, purpose=None):
    return f"/api/v1/invoices/{invoice_id}/export?format={fmt}" + (f"&purpose={purpose}" if purpose else "")


async def detail_of(client, invoice_id):
    return (await client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]


async def grant(client, invoice_id, reason=REASON):
    return await client.post(f"/api/v1/invoices/{invoice_id}/export-override", json={"reason": reason})


class TestWithoutAnOverrideEveryExportRouteRefuses:
    @pytest.mark.parametrize("fmt", ["pdi", "txt", "csv"])
    async def test_the_same_rule_blocks_pdi_txt_and_csv(self, api_client, blocked, fmt):
        response = await api_client.get(url(blocked, fmt))
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert "critical checks" in error["message"] and "COMPLETENESS_MISSING_LINES" in error["message"]
        if fmt != "pdi":
            assert "COMPLETENESS_MISSING_LINES" in error["detail"]["blocking_checks"]
            assert error["detail"]["review_copy"].endswith(f"format={fmt}&purpose=review")

    async def test_json_stays_readable_and_says_it_is_not_export_ready(self, api_client, blocked):
        response = await api_client.get(url(blocked, "json"))
        assert response.status_code == 200
        status = response.json()["export_status"]
        assert status["export_ready"] is False and "COMPLETENESS_MISSING_LINES" in status["blocking_checks"]
        assert status["overridden_by_admin"] is False

    async def test_a_complete_invoice_exports_in_every_format(self, structure_as, api_client, db_session):
        structure_as(extracted_invoice(subtotal=30.0, grand_total=30.0, line_items=[line(1), line(2), line(3)]))
        invoice_id, _ = await upload(api_client, f"whole-{uuid.uuid4().hex[:8]}.pdf")
        for code in CODES:
            await confirm_mapping(api_client, db_session, invoice_id, item_code=code[:11], units=12)
        for fmt in ("pdi", "txt", "csv", "json"):
            assert (await api_client.get(url(invoice_id, fmt))).status_code == 200, fmt


class TestReviewCopiesAreLabelledAndSeparate:
    async def test_txt_review_copy(self, api_client, blocked):
        response = await api_client.get(url(blocked, "txt", "review"))
        assert response.status_code == 200
        assert response.text.splitlines()[0] == "REVIEW COPY - NOT EXPORT-READY - DO NOT IMPORT"
        assert "COMPLETENESS_MISSING_LINES" in response.text
        assert response.headers["x-export-purpose"] == "review-only" and response.headers["x-export-ready"] == "false"
        assert "REVIEW-ONLY_not-for-import" in response.headers["content-disposition"]

    async def test_csv_review_copy_cannot_pass_for_an_import_file(self, api_client, blocked):
        response = await api_client.get(url(blocked, "csv", "review"))
        assert response.status_code == 200
        assert response.text.startswith("# REVIEW COPY - NOT EXPORT-READY - DO NOT IMPORT")
        assert next(csv.reader(io.StringIO(response.text)))[0].startswith("#")
        assert "REVIEW-ONLY_not-for-import" in response.headers["content-disposition"]

    async def test_there_is_no_review_copy_of_the_pdi_file(self, api_client, blocked):
        response = await api_client.get(url(blocked, "pdi", "review"))
        assert response.status_code == 422
        assert (await api_client.get(url(blocked, "json", "review"))).status_code == 422

    async def test_a_review_copy_writes_nothing(self, api_client, blocked, db_session):
        await api_client.get(url(blocked, "txt", "review"))
        await api_client.get(url(blocked, "csv", "review"))
        assert (await db_session.execute(select(EdiExport))).scalars().all() == []
        assert (await db_session.execute(select(ExportOverride))).scalars().all() == []

    async def test_a_manager_can_download_a_review_copy(self, manager, blocked):
        assert (await manager.get(url(blocked, "txt", "review"))).status_code == 200


class TestWhoCanGrantAnOverride:
    async def test_a_manager_cannot(self, manager, blocked):
        response = await grant(manager, blocked)
        assert response.status_code == 403
        assert response.json()["error"]["detail"]["required_role"] == "ADMIN"

    async def test_a_user_cannot(self, plain_user, blocked):
        assert (await grant(plain_user, blocked)).status_code == 403

    async def test_nobody_signed_out_can(self, app, blocked):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as anonymous:
            assert (await grant(anonymous, blocked)).status_code == 401

    async def test_a_refused_grant_changes_nothing(self, manager, api_client, blocked, db_session):
        await grant(manager, blocked)
        assert (await db_session.execute(select(ExportOverride))).scalars().all() == []
        assert (await api_client.get(url(blocked, "pdi"))).status_code == 422

    async def test_a_manager_can_read_the_history_but_a_user_cannot(self, manager, plain_user, api_client, blocked):
        await grant(api_client, blocked)
        listed = await manager.get(f"/api/v1/invoices/{blocked}/export-overrides")
        assert listed.status_code == 200 and len(listed.json()["data"]) == 1
        assert (await plain_user.get(f"/api/v1/invoices/{blocked}/export-overrides")).status_code == 403

    async def test_only_an_admin_can_revoke(self, manager, api_client, blocked):
        granted = (await grant(api_client, blocked)).json()["data"]
        refused = await manager.post(f"/api/v1/export-overrides/{granted['id']}/revoke", json={"reason": REASON})
        assert refused.status_code == 403
        assert (await detail_of(api_client, blocked))["export_override"]["state"] == "ACTIVE"


class TestAReasonIsMandatory:
    @pytest.mark.parametrize("reason", ["", "   ", "short", "          a          "])
    async def test_no_real_reason_no_override(self, api_client, blocked, db_session, reason):
        response = await grant(api_client, blocked, reason)
        assert response.status_code == 422
        assert (await db_session.execute(select(ExportOverride))).scalars().all() == []

    async def test_the_reason_is_required_by_the_schema(self, api_client, blocked):
        response = await api_client.post(f"/api/v1/invoices/{blocked}/export-override", json={})
        assert response.status_code == 422

    async def test_revoking_needs_a_reason_too(self, api_client, blocked):
        granted = (await grant(api_client, blocked)).json()["data"]
        response = await api_client.post(f"/api/v1/export-overrides/{granted['id']}/revoke", json={"reason": "no"})
        assert response.status_code == 422
        assert (await detail_of(api_client, blocked))["export_override"]["state"] == "ACTIVE"

    async def test_nothing_failing_means_nothing_to_override(self, structure_as, api_client):
        structure_as(extracted_invoice(subtotal=30.0, grand_total=30.0, line_items=[line(1), line(2), line(3)]))
        invoice_id, _ = await upload(api_client, f"clean-{uuid.uuid4().hex[:8]}.pdf")
        response = await grant(api_client, invoice_id)
        assert response.status_code == 422 and "nothing to override" in response.json()["error"]["message"]

    async def test_an_unknown_invoice_is_404(self, api_client):
        assert (await grant(api_client, uuid.uuid4())).status_code == 404


class TestUnderAnOverrideTheExportsRun:
    async def test_every_format_exports_and_pdi_is_ledgered_against_the_override(self, api_client, blocked, db_session):
        override = (await grant(api_client, blocked)).json()["data"]
        assert override["state"] == "ACTIVE" and override["reason"] == REASON
        assert override["blocked_checks"] == ["COMPLETENESS_MISSING_LINES"]

        pdi = await api_client.get(url(blocked, "pdi"))
        assert pdi.status_code == 200 and pdi.text.splitlines()[0].startswith("AMOUNT ")
        assert pdi.headers["x-export-override-id"] == override["id"]
        assert "admin-override" not in pdi.headers["content-disposition"]      # the PDI file's name is untouched
        ledger = (await db_session.execute(select(EdiExport).where(EdiExport.invoice_id == uuid.UUID(blocked)))).scalars().all()
        assert [str(r.export_override_id) for r in ledger] == [override["id"]]

        txt = await api_client.get(url(blocked, "txt"))
        assert txt.status_code == 200 and txt.text.splitlines()[0].startswith("EXPORTED UNDER AN ADMINISTRATOR OVERRIDE")
        assert "admin-override" in txt.headers["content-disposition"]
        csv_response = await api_client.get(url(blocked, "csv"))
        assert csv_response.status_code == 200 and csv_response.headers["x-export-override-id"] == override["id"]
        assert csv_response.text.startswith("Description,Quantity")           # accounting files keep their layout

        status = (await api_client.get(url(blocked, "json"))).json()["export_status"]
        assert status["export_ready"] is True and status["overridden_by_admin"] is True
        assert status["blocking_checks"] == ["COMPLETENESS_MISSING_LINES"]

    async def test_a_manager_can_download_what_an_admin_released(self, manager, api_client, blocked):
        await grant(api_client, blocked)
        assert (await manager.get(url(blocked, "pdi"))).status_code == 200

    async def test_the_override_lifts_only_the_critical_check_block(self, structure_as, api_client):
        # Same incomplete invoice, but nobody has confirmed units per case: PDI keeps its own rule.
        structure_as(extracted_invoice(subtotal=20.0, grand_total=20.0, line_items=[line(1), line(2)]))
        invoice_id, _ = await upload(api_client, f"unmapped-{uuid.uuid4().hex[:8]}.pdf")
        assert (await grant(api_client, invoice_id)).status_code == 200
        pdi = await api_client.get(url(invoice_id, "pdi"))
        assert pdi.status_code == 422
        assert "critical checks" not in pdi.json()["error"]["message"]          # refused for the mapping, not the checks
        assert pdi.json()["error"]["detail"]["unmapped_item_codes"]
        assert (await api_client.get(url(invoice_id, "txt"))).status_code == 200   # TXT has no mapping requirement

    async def test_the_detail_shows_the_override_and_the_original_failure(self, api_client, blocked):
        before = await detail_of(api_client, blocked)
        await grant(api_client, blocked)
        after = await detail_of(api_client, blocked)
        assert (after["pdi_export_allowed"], after["pdi_export_overridden"]) == (True, True)
        assert after["export_override"]["state"] == "ACTIVE" and after["export_override"]["granted_by"]
        assert before["pdi_export_allowed"] is False and before["export_override"] is None


class TestAnOverrideNeverChangesWhatTheChecksSaid:
    async def test_status_checks_confidence_and_report_are_untouched(self, api_client, blocked, db_session):
        before = await detail_of(api_client, blocked)
        validation_logs = len((await db_session.execute(select(ProcessingLog).where(
            ProcessingLog.stage == PipelineStage.VALIDATION))).scalars().all())
        await grant(api_client, blocked)
        for fmt in ("pdi", "txt", "csv"):
            await api_client.get(url(blocked, fmt))
        after = await detail_of(api_client, blocked)

        assert after["status"] == before["status"] == "REVIEW_REQUIRED"
        assert after["blocking_checks"] == before["blocking_checks"] == ["COMPLETENESS_MISSING_LINES"]
        assert after["composite_confidence"] == before["composite_confidence"]
        assert after["validation_report"] == before["validation_report"]
        assert after["validation_report"]["completeness"]["status"] == "INCOMPLETE"
        assert after["validation_report"]["confidence"]["composite"] <= 0.60
        db_session.expire_all()
        assert validation_logs == len((await db_session.execute(select(ProcessingLog).where(
            ProcessingLog.stage == PipelineStage.VALIDATION))).scalars().all())
        row = (await db_session.execute(select(Invoice).where(Invoice.id == uuid.UUID(blocked)))).scalar_one()
        assert row.status == "REVIEW_REQUIRED" and row.blocking_checks == ["COMPLETENESS_MISSING_LINES"]

    async def test_the_failed_results_are_preserved_on_the_override_itself(self, api_client, blocked, db_session):
        original = (await detail_of(api_client, blocked))["validation_report"]
        granted = (await grant(api_client, blocked)).json()["data"]
        snapshot = granted["validation_snapshot"]
        assert snapshot["invoice_status"] == "REVIEW_REQUIRED"
        assert snapshot["blocking_checks"] == ["COMPLETENESS_MISSING_LINES"]
        report = snapshot["validation_report"]
        assert report["completeness"] == original["completeness"]
        assert report["completeness"]["status"] == "INCOMPLETE"
        assert report["confidence"]["composite"] == original["confidence"]["composite"]
        assert any(c["name"] == "COMPLETENESS_MISSING_LINES" and c["status"] == "FAILED" for c in report["checks"])

    async def test_an_incomplete_invoice_cannot_be_marked_retailed_even_with_an_override(self, api_client, blocked):
        await grant(api_client, blocked)
        assert (await api_client.get(url(blocked, "pdi"))).status_code == 200   # a delivered, ledgered file exists
        marked = await api_client.post(f"/api/v1/invoices/{blocked}/retailed")
        assert marked.status_code == 422
        state = (await detail_of(api_client, blocked))
        assert state["status"] == "REVIEW_REQUIRED"

    async def test_a_revalidation_recomputes_the_blocks_regardless_of_the_override(self, api_client, blocked):
        await grant(api_client, blocked)
        corrected = await api_client.patch(f"/api/v1/invoices/{blocked}/totals",
                                           json={"subtotal": 20.0, "grand_total": 20.0, "corrected_by": "m"})
        assert corrected.status_code == 200, corrected.text
        detail = await detail_of(api_client, blocked)
        assert "COMPLETENESS_MISSING_LINES" in detail["blocking_checks"]      # still failing: the override did not hide it
        assert detail["status"] == "REVIEW_REQUIRED"


class TestAnOverrideStopsApplyingWhenTheInvoiceChanges:
    async def test_a_correction_makes_it_stale_and_blocks_again(self, api_client, blocked):
        await grant(api_client, blocked)
        assert (await api_client.get(url(blocked, "txt"))).status_code == 200
        changed = await api_client.patch(f"/api/v1/invoices/{blocked}/totals",
                                         json={"subtotal": 25.0, "grand_total": 25.0, "corrected_by": "m"})
        assert changed.status_code == 200, changed.text
        detail = await detail_of(api_client, blocked)
        assert detail["export_override"]["state"] == "STALE"
        assert detail["pdi_export_allowed"] is False and detail["pdi_export_overridden"] is False
        for fmt in ("pdi", "txt", "csv"):
            assert (await api_client.get(url(blocked, fmt))).status_code == 422, fmt

    async def test_a_new_override_can_then_be_granted_for_the_new_state(self, api_client, blocked):
        await grant(api_client, blocked)
        await api_client.patch(f"/api/v1/invoices/{blocked}/totals",
                               json={"subtotal": 25.0, "grand_total": 25.0, "corrected_by": "m"})
        again = await grant(api_client, blocked, "Reviewed again after the total was corrected by hand")
        assert again.status_code == 200
        assert (await api_client.get(url(blocked, "txt"))).status_code == 200

    async def test_fixing_the_invoice_lifts_the_block_without_any_override(self, api_client, blocked):
        await grant(api_client, blocked)
        added = await api_client.post(f"/api/v1/invoices/{blocked}/items", json={
            "description": "ITEM 3", "product_code": CODES[2], "pack_size": "20OZ PL 1/12", "quantity": "1",
            "unit_price": "10.00", "line_total": "10.00", "added_by": "x", "note": "missed by the extraction"})
        assert added.status_code in (200, 201), added.text
        totals = await api_client.patch(f"/api/v1/invoices/{blocked}/totals",     # the printed totals for three rows
                                        json={"subtotal": 30.0, "grand_total": 30.0, "corrected_by": "m"})
        assert totals.status_code == 200, totals.text
        detail = await detail_of(api_client, blocked)
        assert detail["blocking_checks"] == [] and detail["pdi_export_overridden"] is False
        assert detail["export_override"]["state"] == "STALE"


class TestRevokingAndHistory:
    async def test_revoking_blocks_the_exports_again_and_keeps_the_record(self, api_client, blocked, db_session):
        granted = (await grant(api_client, blocked)).json()["data"]
        revoked = await api_client.post(f"/api/v1/export-overrides/{granted['id']}/revoke",
                                        json={"reason": "Granted on the wrong invoice by mistake"})
        assert revoked.status_code == 200
        record = revoked.json()["data"]
        assert record["state"] == "REVOKED" and record["revoke_reason"] == "Granted on the wrong invoice by mistake"
        assert record["reason"] == REASON and record["validation_snapshot"] == granted["validation_snapshot"]
        for fmt in ("pdi", "txt", "csv"):
            assert (await api_client.get(url(blocked, fmt))).status_code == 422, fmt
        assert len((await db_session.execute(select(ExportOverride))).scalars().all()) == 1

    async def test_a_revoked_override_cannot_be_revoked_again(self, api_client, blocked):
        granted = (await grant(api_client, blocked)).json()["data"]
        path = f"/api/v1/export-overrides/{granted['id']}/revoke"
        assert (await api_client.post(path, json={"reason": REASON})).status_code == 200
        assert (await api_client.post(path, json={"reason": REASON})).status_code == 422

    async def test_a_second_grant_while_one_is_active_is_refused(self, api_client, blocked):
        assert (await grant(api_client, blocked)).status_code == 200
        assert (await grant(api_client, blocked)).status_code == 422

    async def test_the_history_lists_every_override_newest_first(self, api_client, blocked):
        first = (await grant(api_client, blocked)).json()["data"]
        await api_client.post(f"/api/v1/export-overrides/{first['id']}/revoke", json={"reason": REASON})
        second = (await grant(api_client, blocked, "Granted again after a second review of the photos")).json()["data"]
        listed = (await api_client.get(f"/api/v1/invoices/{blocked}/export-overrides")).json()["data"]
        assert [r["id"] for r in listed] == [second["id"], first["id"]]
        assert [r["state"] for r in listed] == ["ACTIVE", "REVOKED"]


class TestEveryDecisionIsInTheAuditTrail:
    async def test_grant_exports_and_revocation_are_logged_with_who_and_why(self, api_client, blocked, db_session):
        granted = (await grant(api_client, blocked)).json()["data"]
        await api_client.get(url(blocked, "pdi"))
        await api_client.get(url(blocked, "txt"))
        await api_client.get(url(blocked, "csv"))
        await api_client.post(f"/api/v1/export-overrides/{granted['id']}/revoke", json={"reason": "Withdrawn after the vendor sent a corrected copy"})
        logs = (await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.stage == PipelineStage.LIFECYCLE).order_by(ProcessingLog.created_at))).scalars().all()
        events = [(log.payload or {}).get("event") for log in logs]
        assert events.count("export_override_granted") == 1 and events.count("export_override_revoked") == 1
        exported = [log.payload for log in logs if (log.payload or {}).get("event") == "exported_under_override"]
        assert sorted(p["format"] for p in exported) == ["csv", "pdi", "txt"]
        assert all(p["override_id"] == granted["id"] and p["blocked_checks"] == ["COMPLETENESS_MISSING_LINES"] for p in exported)
        grant_log = next(log.payload for log in logs if (log.payload or {}).get("event") == "export_override_granted")
        assert grant_log["reason"] == REASON and grant_log["by"] == "testadmin"
        pdi = next(p for p in exported if p["format"] == "pdi")
        assert pdi["export_number"] == 1 and len(pdi["sha256"]) == 64

    async def test_each_override_export_names_invoice_format_override_user_time_bytes_and_snapshot(
            self, api_client, blocked, db_session):
        import hashlib
        import json
        from datetime import datetime

        granted = (await grant(api_client, blocked)).json()["data"]
        delivered = {}
        for fmt in ("pdi", "txt", "csv"):
            response = await api_client.get(url(blocked, fmt))
            assert response.status_code == 200
            delivered[fmt] = response.content
        row = (await db_session.execute(select(ExportOverride))).scalar_one()
        digest = hashlib.sha256(json.dumps(row.validation_snapshot, sort_keys=True, separators=(",", ":"),
                                           default=str).encode()).hexdigest()
        logs = (await db_session.execute(select(ProcessingLog).where(ProcessingLog.stage == PipelineStage.LIFECYCLE))).scalars().all()
        exported = {log.payload["format"]: log.payload for log in logs
                    if (log.payload or {}).get("event") == "exported_under_override"}
        assert set(exported) == {"pdi", "txt", "csv"}
        invoice = (await db_session.execute(select(Invoice).where(Invoice.id == uuid.UUID(blocked)))).scalar_one()
        for fmt, entry in exported.items():
            assert entry["invoice_id"] == blocked and entry["invoice_number"] == invoice.invoice_number
            assert entry["override_id"] == granted["id"]
            assert (entry["by"], entry["by_role"]) == ("testadmin", "ADMIN") and entry["by_user_id"]
            assert datetime.fromisoformat(entry["exported_at"]).tzinfo is not None
            assert entry["sha256"] == hashlib.sha256(delivered[fmt]).hexdigest()      # the bytes that actually left
            assert entry["byte_size"] == len(delivered[fmt])
            assert entry["validation_snapshot_ref"] == {
                "override_id": granted["id"], "state_sha256": row.state_sha256, "snapshot_sha256": digest}
        assert exported["pdi"]["export_number"] == 1 and exported["txt"]["export_number"] is None

    async def test_review_downloads_are_separate_and_leave_no_override_export_entry(self, api_client, blocked, db_session):
        await grant(api_client, blocked)
        before = len((await db_session.execute(select(ProcessingLog))).scalars().all())
        for fmt in ("txt", "csv"):
            assert (await api_client.get(url(blocked, fmt, "review"))).status_code == 200
        db_session.expire_all()
        logs = (await db_session.execute(select(ProcessingLog))).scalars().all()
        assert len(logs) == before
        assert not [log for log in logs if (log.payload or {}).get("event") == "exported_under_override"]

    async def test_a_blocked_export_logs_nothing_as_exported(self, api_client, blocked, db_session):
        for fmt in ("pdi", "txt", "csv"):
            assert (await api_client.get(url(blocked, fmt))).status_code == 422
        logs = (await db_session.execute(select(ProcessingLog))).scalars().all()
        assert not [log for log in logs if (log.payload or {}).get("event") == "exported_under_override"]

    async def test_the_override_row_records_the_admin_and_survives_the_invoice(self, api_client, blocked, db_session):
        await grant(api_client, blocked)
        row = (await db_session.execute(select(ExportOverride))).scalar_one()
        assert (row.granted_by, row.granted_by_role, row.reason) == ("testadmin", "ADMIN", REASON)
        assert str(row.invoice_id) == blocked and row.state_sha256 and row.document_id is not None
