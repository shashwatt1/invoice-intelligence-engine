"""
tests/integration/test_document_lifecycle_actions.py — STOP / MOVE-TO-BIN
against real Postgres, under real authenticated sessions (P3).

Covers the P2 scenarios (both actions on an active document, idempotency,
STOP blocking completion and EDI, BIN preserving source/history and never
touching mappings/proposals, completed-invoice protection, the in-flight
OCR/LLM race) now driven by real login instead of a client-supplied
actor/role, plus P3's ownership boundaries: a USER only ever acts on
documents authenticated as their own uploader.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.exceptions import DocumentWithdrawnError
from app.models.invoice import Invoice
from app.models.processing_log import LogStatus, PipelineStage, ProcessingLog
from app.models.user import UserRole
from app.repositories.document_repository import DocumentRepository
from app.repositories.user_repository import UserRepository
from app.services.document_lifecycle import stop_document
from app.services.pipeline_service import InvoiceProcessingPipeline, PageUpload
from tests.integration.conftest import (
    MANAGER_PASSWORD,
    MANAGER_USERNAME,
    USER_PASSWORD,
    USER_USERNAME,
    requires_db,
    store_id,
    user_id,
)
from tests.integration.fakes import FakeExtraction, FakeStructuring
from tests.integration.test_api_db import (  # noqa: F401 — fixture reuse
    INVOICE_PDF,
    STORE,
    api_client,
    process_file,
)

pytestmark = requires_db


async def _client_as(app, username: str, password: str) -> AsyncClient:
    """A second, independently-authenticated client against the same
    app/db as `api_client` — for scenarios needing two identities at once
    (e.g. USER A's document vs USER B's session)."""
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return client


async def _as_manager(app) -> AsyncClient:
    return await _client_as(app, MANAGER_USERNAME, MANAGER_PASSWORD)


async def _as_user(app) -> AsyncClient:
    return await _client_as(app, USER_USERNAME, USER_PASSWORD)


async def _active_document(db_session, *, uploaded_by_user_id: uuid.UUID | None):
    """A document sitting in UPLOADED — active, no pipeline stage run yet — owned by the given user id."""
    document = await DocumentRepository(db_session).create(
        filename="invoice.pdf", mime_type="application/pdf", file_size_bytes=len(INVOICE_PDF),
        file_path="/uploads/invoice.pdf", file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        uploaded_by_user_id=uploaded_by_user_id,
    )
    await db_session.commit()
    return document


def _page(content: bytes = INVOICE_PDF, filename: str = "acme-invoice.pdf") -> PageUpload:
    return PageUpload(
        filename=filename, mime_type="application/pdf", file_size_bytes=len(content),
        file_path=f"/uploads/{filename}", file_hash=hashlib.sha256(content).hexdigest(), content=content,
    )


async def _stop(client, document_id) -> object:
    return await client.post(f"/api/v1/documents/{document_id}/stop")


async def _bin(client, document_id) -> object:
    return await client.post(f"/api/v1/documents/{document_id}/move-to-bin")


async def _log_count(db_session, document_id, stage=PipelineStage.LIFECYCLE) -> int:
    rows = (await db_session.execute(
        select(ProcessingLog).where(ProcessingLog.document_id == document_id, ProcessingLog.stage == stage)
    )).scalars().all()
    return len(rows)


class TestStopActiveDocument:
    """P2 #1: STOP active document — now under a real session."""

    async def test_stop_a_document_that_is_still_active(self, api_client, db_session):  # noqa: F811
        document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.ADMIN.value))
        response = await _stop(api_client, document.id)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["status"] == "STOPPED"
        assert data["is_terminal"] is True

    async def test_stop_records_one_lifecycle_audit_entry(self, api_client, db_session):  # noqa: F811
        document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.ADMIN.value))
        await _stop(api_client, document.id)
        assert await _log_count(db_session, document.id) == 1


class TestStopIdempotency:
    """P2 #2 / P3 #33: STOP idempotency, preserved."""

    async def test_stopping_twice_is_a_no_op_the_second_time(self, api_client, db_session):  # noqa: F811
        document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.ADMIN.value))
        first = await _stop(api_client, document.id)
        second = await _stop(api_client, document.id)
        assert first.status_code == second.status_code == 200
        assert second.json()["data"]["status"] == "STOPPED"
        assert await _log_count(db_session, document.id) == 1


class TestStopPreventsCompletionAndEdi:
    """P2 #3/#4: STOP prevents successful completion and EDI."""

    async def test_stopping_before_the_pipeline_runs_blocks_every_later_stage(self, db_session):
        document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.ADMIN.value))
        pipeline = InvoiceProcessingPipeline(structuring_service=FakeStructuring())
        admin = await UserRepository(db_session).get(user_id(UserRole.ADMIN.value))

        await stop_document(db_session, document, admin)
        await db_session.commit()

        with pytest.raises(DocumentWithdrawnError):
            await pipeline.run_stages_pages(
                db_session, document, [_page()], store_id=store_id(STORE)
            )

        await db_session.refresh(document)
        assert document.status == "STOPPED"

        invoice = (await db_session.execute(
            select(Invoice).where(Invoice.document_id == document.id)
        )).scalar_one_or_none()
        assert invoice is None

        logs = (await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == document.id)
        )).scalars().all()
        assert not any(log.status == LogStatus.FAILURE for log in logs)


class TestMoveToBinActiveDocument:
    """P2 #5: MOVE TO BIN active document."""

    async def test_bin_a_document_that_is_still_active(self, api_client, db_session):  # noqa: F811
        document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.ADMIN.value))
        response = await _bin(api_client, document.id)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["status"] == "BINNED"
        assert data["is_terminal"] is True


class TestMoveToBinIdempotency:
    """P2 #6 / P3 #33: MOVE TO BIN idempotency, preserved."""

    async def test_binning_twice_is_a_no_op_the_second_time(self, api_client, db_session):  # noqa: F811
        document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.ADMIN.value))
        first = await _bin(api_client, document.id)
        second = await _bin(api_client, document.id)
        assert first.status_code == second.status_code == 200
        assert second.json()["data"]["status"] == "BINNED"
        assert await _log_count(db_session, document.id) == 1


class TestMoveToBinPreservesSourceAndHistory:
    """P2 #7: MOVE TO BIN preserves source/history."""

    async def test_binning_a_completed_document_keeps_the_file_path_and_every_prior_log(
        self, api_client, db_session  # noqa: F811
    ):
        accepted = await process_file(api_client)
        document_id = accepted["document_id"]
        before = await DocumentRepository(db_session).get(document_id)
        file_path_before = before.file_path
        logs_before = await _log_count(db_session, document_id, stage=PipelineStage.UPLOAD)
        total_before = len((await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == document_id)
        )).scalars().all())

        response = await _bin(api_client, document_id)
        assert response.status_code == 200

        await db_session.refresh(before)
        after = before
        assert after is not None
        assert after.file_path == file_path_before
        assert after.status == "BINNED"
        assert await _log_count(db_session, document_id, stage=PipelineStage.UPLOAD) == logs_before
        total_after = len((await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == document_id)
        )).scalars().all())
        assert total_after == total_before + 1

        invoice = (await db_session.execute(
            select(Invoice).where(Invoice.document_id == document_id)
        )).scalar_one_or_none()
        assert invoice is not None


class TestMoveToBinDoesNotTouchMappingsOrProposals:
    """P2 #8: MOVE TO BIN causes no mapping/proposal changes."""

    async def test_binning_leaves_case_mapping_and_proposal_tables_exactly_as_they_were(
        self, api_client, db_session  # noqa: F811
    ):
        from sqlalchemy import text as sql

        accepted = await process_file(api_client)
        mappings_before = (await db_session.execute(sql("SELECT count(*) FROM product_case_mappings"))).scalar_one()
        proposals_before = (await db_session.execute(sql("SELECT count(*) FROM product_data_proposals"))).scalar_one()

        response = await _bin(api_client, accepted["document_id"])
        assert response.status_code == 200

        mappings_after = (await db_session.execute(sql("SELECT count(*) FROM product_case_mappings"))).scalar_one()
        proposals_after = (await db_session.execute(sql("SELECT count(*) FROM product_data_proposals"))).scalar_one()
        assert mappings_after == mappings_before
        assert proposals_after == proposals_before


class TestOwnership:
    """P3 #18-24: USER-role document ownership, backed by real sessions."""

    async def test_user_sees_own_document_in_history(self, app, api_client, db_session):  # noqa: F811
        user_client = await _as_user(app)
        try:
            accepted = await process_file(user_client)
            listing = await user_client.get("/api/v1/invoices")
            assert listing.status_code == 200
            ids = [row["document_id"] for row in listing.json()["items"]]
            assert accepted["document_id"] in ids
        finally:
            await user_client.aclose()

    async def test_user_cannot_access_another_users_document(self, app, api_client, db_session):  # noqa: F811
        alice = await _as_user(app)
        try:
            accepted = await process_file(alice)
            status = (await alice.get(accepted["status_url"])).json()["data"]
        finally:
            await alice.aclose()

        # A second USER account, distinct from the seeded one, uploads nothing.
        await UserRepository(db_session).create(
            username="bob",
            password_hash=(await UserRepository(db_session).get(user_id(UserRole.USER.value))).password_hash,
            role=UserRole.USER.value,
        )
        await db_session.commit()
        bob = await _client_as(app, "bob", USER_PASSWORD)
        try:
            response = await bob.get(f"/api/v1/documents/{accepted['document_id']}")
            assert response.status_code == 403
            response = await bob.get(f"/api/v1/invoices/{status['invoice_id']}")
            assert response.status_code == 403
        finally:
            await bob.aclose()

    async def test_user_can_stop_own_document(self, app, api_client, db_session):  # noqa: F811
        user_client = await _as_user(app)
        try:
            document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.USER.value))
            response = await _stop(user_client, document.id)
            assert response.status_code == 200
        finally:
            await user_client.aclose()

    async def test_user_cannot_stop_another_users_document(self, app, api_client, db_session):  # noqa: F811
        document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.ADMIN.value))
        user_client = await _as_user(app)
        try:
            response = await _stop(user_client, document.id)
            assert response.status_code == 403
        finally:
            await user_client.aclose()

    async def test_user_can_move_to_bin_own_document(self, app, api_client, db_session):  # noqa: F811
        user_client = await _as_user(app)
        try:
            document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.USER.value))
            response = await _bin(user_client, document.id)
            assert response.status_code == 200
        finally:
            await user_client.aclose()

    async def test_user_cannot_move_to_bin_another_users_document(self, app, api_client, db_session):  # noqa: F811
        document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.ADMIN.value))
        user_client = await _as_user(app)
        try:
            response = await _bin(user_client, document.id)
            assert response.status_code == 403
        finally:
            await user_client.aclose()

    async def test_admin_can_manage_a_historical_null_owner_document(self, api_client, db_session):  # noqa: F811
        document = await _active_document(db_session, uploaded_by_user_id=None)
        response = await _stop(api_client, document.id)
        assert response.status_code == 200


class TestUploadAttribution:
    """P3 #25-27: uploader identity comes from the session, never the client."""

    async def test_uploader_is_the_authenticated_identity(self, app, api_client, db_session):  # noqa: F811
        user_client = await _as_user(app)
        try:
            accepted = await process_file(user_client)
        finally:
            await user_client.aclose()
        document = await DocumentRepository(db_session).get(accepted["document_id"])
        assert document.uploaded_by_user_id == user_id(UserRole.USER.value)

    async def test_client_cannot_override_the_uploader(self, api_client, db_session):  # noqa: F811
        # No client-facing field accepts an uploader id at all anymore —
        # proven structurally: the multipart form only carries file/store_id.
        response = await api_client.post(
            "/api/v1/invoices/process",
            files={"file": ("x.pdf", INVOICE_PDF, "application/pdf")},
            data={"store_id": str(store_id(STORE)), "uploaded_by_user_id": str(uuid.uuid4())},
        )
        assert response.status_code == 202
        document = await DocumentRepository(db_session).get(response.json()["data"]["document_id"])
        assert document.uploaded_by_user_id == user_id(UserRole.ADMIN.value)  # api_client's own session, not the spoofed field

    async def test_multi_photo_upload_attributes_every_photo_to_the_uploader(self, app, api_client, db_session):  # noqa: F811
        user_client = await _as_user(app)
        try:
            response = await user_client.post(
                "/api/v1/invoices/process",
                files=[("files", ("p1.pdf", INVOICE_PDF, "application/pdf")),
                      ("files", ("p2.pdf", INVOICE_PDF + b" ", "application/pdf"))],
                data={"store_id": str(store_id(STORE))},
            )
            assert response.status_code == 202
        finally:
            await user_client.aclose()
        document = await DocumentRepository(db_session).get(response.json()["data"]["document_id"])
        assert document.uploaded_by_user_id == user_id(UserRole.USER.value)


class TestCompletedInvoiceProtection:
    """P2 #10 counterpart: completed invoice protection, under real auth."""

    async def test_stop_is_refused_on_a_completed_document(self, api_client, db_session):  # noqa: F811
        accepted = await process_file(api_client)
        response = await _stop(api_client, accepted["document_id"])
        assert response.status_code == 422

    async def test_bin_on_a_completed_document_never_mutates_the_invoice(
        self, api_client, db_session  # noqa: F811
    ):
        accepted = await process_file(api_client)
        invoice_before = (await db_session.execute(
            select(Invoice).where(Invoice.document_id == accepted["document_id"])
        )).scalar_one()
        status_before, total_before = invoice_before.status, invoice_before.grand_total

        response = await _bin(api_client, accepted["document_id"])
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "BINNED"

        invoice_after = (await db_session.execute(
            select(Invoice).where(Invoice.document_id == accepted["document_id"])
        )).scalar_one()
        assert invoice_after.status == status_before
        assert invoice_after.grand_total == total_before


class TestInFlightStageCannotResurrectAStoppedDocument:
    """P2 #11: in-flight OCR/LLM completion cannot resurrect a stopped document."""

    async def test_ocr_that_finishes_after_a_concurrent_stop_cannot_advance_the_document(
        self, db_session
    ):
        document = await _active_document(db_session, uploaded_by_user_id=user_id(UserRole.ADMIN.value))
        admin = await UserRepository(db_session).get(user_id(UserRole.ADMIN.value))

        class StopMidFlightExtraction:
            async def extract_text(self, file_content, mime_type, filename=""):
                result = await FakeExtraction().extract_text(file_content, mime_type, filename)
                await stop_document(db_session, document, admin)
                await db_session.commit()
                return result

        pipeline = InvoiceProcessingPipeline(
            extraction_service=StopMidFlightExtraction(), structuring_service=FakeStructuring(),
        )

        with pytest.raises(DocumentWithdrawnError):
            await pipeline.run_stages_pages(
                db_session, document, [_page()], store_id=store_id(STORE)
            )

        await db_session.refresh(document)
        assert document.status == "STOPPED"

        invoice = (await db_session.execute(
            select(Invoice).where(Invoice.document_id == document.id)
        )).scalar_one_or_none()
        assert invoice is None, "OCR finishing late must never let persistence proceed"

        logs = (await db_session.execute(
            select(ProcessingLog).where(ProcessingLog.document_id == document.id)
        )).scalars().all()
        assert not any(log.status == LogStatus.FAILURE for log in logs)
