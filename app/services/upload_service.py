"""
Upload Service — app/services/upload_service.py

Handles all business logic for the file upload pipeline:
    1. Validate MIME type and file size.
    2. Read file contents and calculate SHA-256 hash.
    3. Write file to storage (local disk in Phase 1; S3/Supabase in Phase 2).
    4. Return a structured UploadResult.

This service is intentionally storage-agnostic: it delegates file I/O
to the StorageService, so the storage backend can be swapped without
touching this layer.

NOT in scope for this service:
- OCR processing (Sprint 2)
- AI structuring (Sprint 3)
- Database persistence of invoice records (that belongs in a repository)
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from app.core.logging import get_logger
from app.services.storage_service import StorageService

logger = get_logger(__name__)

# Minimum file size in bytes — reject empty or near-empty files
MIN_FILE_SIZE_BYTES = 1024  # 1 KB


@dataclass
class UploadResult:
    """
    Structured result returned by UploadService.handle_upload().

    This is the contract between the service layer and the API layer.
    All fields are primitive types so the result can be serialised to JSON
    without any ORM dependency.
    """

    document_uuid: str
    filename: str
    file_size_bytes: int
    mime_type: str
    file_path: str
    file_hash: str
    status: str
    created_at: datetime


class UploadService:
    """
    Orchestrates the file upload pipeline.

    Dependencies are injected via the constructor to keep the service
    fully testable without a real filesystem or database.
    """

    def __init__(self, storage: StorageService) -> None:
        self._storage = storage
        self._settings = get_settings()

    async def handle_upload(self, file: UploadFile) -> UploadResult:
        """
        Validate, hash, and persist an uploaded invoice file.

        Args:
            file: The UploadFile instance from FastAPI.

        Returns:
            UploadResult containing metadata about the persisted file.

        Raises:
            UnsupportedFileTypeError: If the MIME type is not allowed.
            FileTooLargeError: If the file exceeds the configured maximum.
            EmptyFileError: If the file is empty or below minimum size.
        """
        logger.info("upload_started", filename=file.filename, content_type=file.content_type)

        # ------------------------------------------------------------------
        # Step 1: Validate MIME type
        # ------------------------------------------------------------------
        self._validate_mime_type(file.content_type or "")

        # ------------------------------------------------------------------
        # Step 2: Read file into memory, check size, compute hash
        # ------------------------------------------------------------------
        contents = await file.read()
        file_size = len(contents)

        if file_size < MIN_FILE_SIZE_BYTES:
            raise EmptyFileError(
                message=f"File is too small ({file_size} bytes). Minimum is {MIN_FILE_SIZE_BYTES} bytes.",
                detail={"file_size_bytes": file_size},
            )

        if file_size > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(
                message=(
                    f"File size {file_size / (1024**2):.1f} MB exceeds the "
                    f"{self._settings.max_upload_size_mb} MB limit."
                ),
                detail={
                    "file_size_bytes": file_size,
                    "max_allowed_bytes": self._settings.max_upload_size_bytes,
                },
            )

        file_hash = self._compute_sha256(contents)

        # ------------------------------------------------------------------
        # Step 3: Generate a document UUID and persist to storage
        # ------------------------------------------------------------------
        document_uuid = str(uuid.uuid4())
        original_filename = file.filename or "unnamed_file"
        file_path = await self._storage.save(
            content=contents,
            document_uuid=document_uuid,
            original_filename=original_filename,
        )

        result = UploadResult(
            document_uuid=document_uuid,
            filename=original_filename,
            file_size_bytes=file_size,
            mime_type=file.content_type or "application/octet-stream",
            file_path=file_path,
            file_hash=file_hash,
            status="INGESTED",
            created_at=datetime.now(UTC),
        )

        logger.info(
            "upload_completed",
            document_uuid=document_uuid,
            filename=original_filename,
            file_size_bytes=file_size,
            file_hash=file_hash,
        )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate_mime_type(self, content_type: str) -> None:
        """
        Raise UnsupportedFileTypeError if the content_type is not allowed.

        Strips any parameters (e.g. 'application/pdf; charset=utf-8')
        before comparing against the allowed list.
        """
        # Content-Type may include parameters like '; charset=...'
        base_type = content_type.split(";")[0].strip().lower()
        allowed = [m.lower() for m in self._settings.allowed_mime_types]
        if base_type not in allowed:
            raise UnsupportedFileTypeError(
                detail={"received": base_type, "allowed": allowed},
            )

    @staticmethod
    def _compute_sha256(data: bytes) -> str:
        """Return the hex-encoded SHA-256 digest of `data`."""
        return hashlib.sha256(data).hexdigest()
