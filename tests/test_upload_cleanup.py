"""
tests/test_upload_cleanup.py — LOCAL OFFLINE TESTS for what happens to a
stored file when intake refuses the upload (app/api/v1/invoices.py).

The process route stores the file BEFORE it can check for a duplicate —
the hash it checks is the stored file's. So every refused intake leaves a
freshly written object behind unless the route removes it. The first pilot
upload proved the cost: two duplicate retries left two orphaned objects in
the Supabase bucket, each a full copy of an invoice already stored.

The safety property these tests pin down is narrow and load-bearing:
cleanup removes ONLY what this request wrote. handle_upload() mints a new
document_uuid per file, so this request's keys can never be an existing
document's, and intake raises before creating any row — but that reasoning
lives in two files, so it is asserted here rather than trusted.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass

import pytest
from fastapi import BackgroundTasks
from starlette.datastructures import Headers, UploadFile

import app.api.v1.invoices as invoices_module
from app.api.v1.invoices import _discard_stored_uploads, process_invoice
from app.core.exceptions import DuplicateDocumentError, StorageError
from app.models.user import User, UserRole
from app.services.pipeline_service import PageUpload
from app.services.upload_service import UploadResult

# The object an already-processed document owns. Nothing in a later, refused
# intake may ever touch this key.
EXISTING_OBJECT = "default/2026/09/e5cadf20-e4d2-486b-bc9c-a00a33995dd6.jpg"


class RecordingStorage:
    """Storage that records deletes instead of performing them."""

    def __init__(self, fail_on: set[str] | None = None):
        self.deleted: list[str] = []
        self._fail_on = fail_on or set()

    async def delete(self, file_path: str) -> None:
        if file_path in self._fail_on:
            raise StorageError(message="Supabase unavailable.", detail={"path": file_path})
        self.deleted.append(file_path)


def page(file_path: str, *, file_hash: str = "hash") -> PageUpload:
    return PageUpload(
        filename="IMG_6569.jpg", mime_type="image/jpeg", file_size_bytes=1_759_083,
        file_path=file_path, file_hash=file_hash, content=b"",
    )


class TestDiscardStoredUploads:
    """The cleanup helper itself."""

    async def test_deletes_every_object_this_request_stored(self, monkeypatch):
        storage = RecordingStorage()
        monkeypatch.setattr(invoices_module, "get_storage_service", lambda: storage)

        await _discard_stored_uploads(
            [page("default/2026/09/new-a.jpg"), page("default/2026/09/new-b.jpg")],
            reason="intake_rejected",
        )

        assert storage.deleted == ["default/2026/09/new-a.jpg", "default/2026/09/new-b.jpg"]

    async def test_never_touches_the_existing_documents_object(self, monkeypatch):
        storage = RecordingStorage()
        monkeypatch.setattr(invoices_module, "get_storage_service", lambda: storage)

        # Only ever given THIS request's pages — the duplicate it collided
        # with is not among them, and cannot be reached from them.
        await _discard_stored_uploads([page("default/2026/09/new-copy.jpg")], reason="intake_rejected")

        assert EXISTING_OBJECT not in storage.deleted
        assert storage.deleted == ["default/2026/09/new-copy.jpg"]

    async def test_a_failed_delete_is_swallowed_so_it_cannot_mask_the_real_error(self, monkeypatch):
        storage = RecordingStorage(fail_on={"default/2026/09/unreachable.jpg"})
        monkeypatch.setattr(invoices_module, "get_storage_service", lambda: storage)

        # Must not raise: the operator needs to be told "duplicate", not
        # "cleanup failed". The orphan is logged and left behind instead.
        await _discard_stored_uploads(
            [page("default/2026/09/unreachable.jpg"), page("default/2026/09/fine.jpg")],
            reason="intake_rejected",
        )

        # The failure of one object must not stop the others being removed.
        assert storage.deleted == ["default/2026/09/fine.jpg"]

    async def test_unconfigured_storage_does_not_raise(self, monkeypatch):
        def unconfigured():
            raise StorageError(message="Supabase storage is not configured.")

        monkeypatch.setattr(invoices_module, "get_storage_service", unconfigured)

        await _discard_stored_uploads([page("default/2026/09/new.jpg")], reason="intake_rejected")


@dataclass
class _FakeUploadService:
    """Stands in for the real upload: reports where it stored the file."""

    stored_path: str

    async def handle_upload(self, file: UploadFile) -> UploadResult:
        from datetime import UTC, datetime

        return UploadResult(
            document_uuid=str(uuid.uuid4()), filename=file.filename or "IMG_6569.jpg",
            file_size_bytes=1_759_083, mime_type="image/jpeg", file_path=self.stored_path,
            file_hash="4dcc5b3e41ace9b12fa85503546f0f4365e8c724187d1ffc7ba37d53f055811b",
            status="INGESTED", created_at=datetime.now(UTC),
        )


class _DuplicatePipeline:
    """Intake that refuses, the way a re-uploaded invoice is refused."""

    async def intake_pages(self, session, pages, *, store_id=None, uploaded_by_user_id=None):
        raise DuplicateDocumentError(
            detail={"existing_document_id": "f6f714c0-a326-42df-adbb-eee6ab8d1391",
                    "existing_status": "STORE_CONFIRMATION_REQUIRED"}
        )


class _AcceptingPipeline:
    """Intake that succeeds — nothing may be deleted."""

    def __init__(self):
        self.document = type("Doc", (), {
            "id": uuid.uuid4(), "filename": "IMG_6569.jpg", "status": "UPLOADED",
        })()

    async def intake_pages(self, session, pages, *, store_id=None, uploaded_by_user_id=None):
        return self.document


def _photo() -> UploadFile:
    return UploadFile(
        io.BytesIO(b"x" * 2048), filename="IMG_6569.jpg",
        headers=Headers({"content-type": "image/jpeg"}),
    )


def _operator() -> User:
    # An ADMIN: the one role that may process without naming a store, which these
    # intake-cleanup paths do (the store rule by role: tests/test_phase_d_relationships.py).
    return User(id=uuid.uuid4(), username="pilot", password_hash="x",
                role=UserRole.ADMIN.value, is_active=True)


class TestProcessRouteCleansUpWhatItStored:
    """The route's two intake-failure paths, end to end."""

    async def test_a_duplicate_removes_the_copy_it_just_stored(self, monkeypatch):
        storage = RecordingStorage()
        monkeypatch.setattr(invoices_module, "get_storage_service", lambda: storage)
        new_object = "default/2026/09/de0fd3e7-6c5e-4c88-b438-6fe68eb041b8.jpg"

        with pytest.raises(DuplicateDocumentError) as raised:
            await process_invoice(
                background_tasks=BackgroundTasks(), file=_photo(), files=[], store_id=None,
                db=None, upload_service=_FakeUploadService(new_object),
                pipeline=_DuplicatePipeline(), user=_operator(),
            )

        # The refusal reaches the operator unchanged...
        assert raised.value.error_code == "ERR_DUPLICATE_DOCUMENT"
        assert raised.value.detail["existing_document_id"] == "f6f714c0-a326-42df-adbb-eee6ab8d1391"
        # ...and only the copy this request wrote is gone.
        assert storage.deleted == [new_object]
        assert EXISTING_OBJECT not in storage.deleted

    async def test_a_successful_intake_keeps_its_object(self, monkeypatch):
        storage = RecordingStorage()
        monkeypatch.setattr(invoices_module, "get_storage_service", lambda: storage)

        response = await process_invoice(
            background_tasks=BackgroundTasks(), file=_photo(), files=[], store_id=None,
            db=None, upload_service=_FakeUploadService("default/2026/09/keep-me.jpg"),
            pipeline=_AcceptingPipeline(), user=_operator(),
        )

        assert response.data.filename == "IMG_6569.jpg"
        assert storage.deleted == []

    async def test_a_duplicate_still_refuses_when_cleanup_itself_fails(self, monkeypatch):
        new_object = "default/2026/09/de0fd3e7-6c5e-4c88-b438-6fe68eb041b8.jpg"
        storage = RecordingStorage(fail_on={new_object})
        monkeypatch.setattr(invoices_module, "get_storage_service", lambda: storage)

        # A failed cleanup must never turn a 409 into a 500.
        with pytest.raises(DuplicateDocumentError):
            await process_invoice(
                background_tasks=BackgroundTasks(), file=_photo(), files=[], store_id=None,
                db=None, upload_service=_FakeUploadService(new_object),
                pipeline=_DuplicatePipeline(), user=_operator(),
            )

        assert storage.deleted == []
