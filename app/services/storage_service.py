"""
Storage Service — app/services/storage_service.py

Provides a backend-agnostic interface for persisting raw invoice files.

Phase 1: Files are written to the local filesystem under UPLOAD_PATH.
Phase 2: This service will be extended with S3 and Supabase backends,
         selectable via the STORAGE_BACKEND environment variable.

Design decisions:
- The StorageService is an abstract base class. The concrete backend
  is selected at startup based on configuration and injected wherever needed.
- Local storage uses aiofiles for non-blocking disk I/O.
- The file is stored at: {upload_path}/{organization_id}/{year}/{month}/{uuid}.{ext}
  This mirrors the S3 key structure so that migration to object storage
  requires no changes to the path scheme.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

import aiofiles

from app.core.config import get_settings
from app.core.exceptions import StorageError
from app.core.logging import get_logger

logger = get_logger(__name__)


class StorageService(ABC):
    """
    Abstract base class for all storage backends.

    Any concrete implementation must expose `save()` and `delete()`.
    """

    @abstractmethod
    async def save(
        self,
        content: bytes,
        document_uuid: str,
        original_filename: str,
        organization_id: str = "default",
    ) -> str:
        """
        Persist raw file bytes and return the storage path/URL.

        Args:
            content: Raw file bytes to store.
            document_uuid: Unique identifier for this document (used in the path).
            original_filename: Original filename, used to determine the extension.
            organization_id: Tenant identifier for path namespacing.

        Returns:
            Opaque string reference (local path or cloud URL) suitable for
            storing in the `invoices.raw_file_url` database column.

        Raises:
            StorageError: If the write operation fails.
        """

    @abstractmethod
    async def delete(self, file_path: str) -> None:
        """
        Remove a previously stored file.

        Args:
            file_path: The reference string returned by `save()`.

        Raises:
            StorageError: If the delete operation fails.
        """


class LocalStorageService(StorageService):
    """
    Stores files on the local filesystem.

    Used in Phase 1 development. Files are stored at::

        {base_path}/{organization_id}/{year}/{month}/{uuid}.{ext}

    This path structure is intentionally compatible with S3 key naming
    so that a migration to object storage requires minimal code changes.
    """

    def __init__(self, base_path: str | None = None) -> None:
        settings = get_settings()
        self._base = Path(base_path or settings.upload_path)
        self._base.mkdir(parents=True, exist_ok=True)
        logger.info("local_storage_initialized", base_path=str(self._base))

    async def save(
        self,
        content: bytes,
        document_uuid: str,
        original_filename: str,
        organization_id: str = "default",
    ) -> str:
        """Write file bytes to local disk and return the relative path."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        ext = Path(original_filename).suffix.lower() or ".bin"

        # Build directory: base / org / year / month
        directory = self._base / organization_id / str(now.year) / f"{now.month:02d}"
        directory.mkdir(parents=True, exist_ok=True)

        file_path = directory / f"{document_uuid}{ext}"

        try:
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(content)
        except OSError as exc:
            logger.error(
                "local_storage_write_failed",
                path=str(file_path),
                error=str(exc),
            )
            raise StorageError(
                message="Failed to write file to local storage.",
                detail={"path": str(file_path), "error": str(exc)},
            ) from exc

        logger.info(
            "file_saved",
            document_uuid=document_uuid,
            path=str(file_path),
            size_bytes=len(content),
        )
        return str(file_path)

    async def delete(self, file_path: str) -> None:
        """Remove a file from the local filesystem."""
        path = Path(file_path)
        try:
            if path.exists():
                path.unlink()
                logger.info("file_deleted", path=str(path))
        except OSError as exc:
            raise StorageError(
                message="Failed to delete file from local storage.",
                detail={"path": file_path, "error": str(exc)},
            ) from exc


def get_storage_service() -> StorageService:
    """
    Factory function that returns the configured storage backend.

    Currently only 'local' is implemented. S3 and Supabase backends
    will be added in Phase 2 without changing this interface.
    """
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalStorageService()
    # Future: if settings.storage_backend == "s3": return S3StorageService()
    # Future: if settings.storage_backend == "supabase": return SupabaseStorageService()
    raise ValueError(f"Unknown storage backend: {settings.storage_backend}")
