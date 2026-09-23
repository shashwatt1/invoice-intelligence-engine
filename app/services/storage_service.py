"""
Storage Service — app/services/storage_service.py

Provides a backend-agnostic interface for persisting raw invoice files.

Local development: files are written to the local filesystem under
UPLOAD_PATH — the application container there is not disposable, so this
is fine. Pilot deployment: the application container IS disposable
(Render free tier has no persistent disk), so files go to Supabase
Storage instead, selected via the STORAGE_BACKEND environment variable.
Every call site goes through get_storage_service() and never reaches
past this abstraction to a filesystem or HTTP client directly, so
switching backends requires no changes anywhere else in the pipeline.

Design decisions:
- The StorageService is an abstract base class. The concrete backend
  is selected at startup based on configuration and injected wherever needed.
- Local storage uses aiofiles for non-blocking disk I/O.
- The file is stored at: {upload_path}/{organization_id}/{year}/{month}/{uuid}.{ext}
  This mirrors the S3/Supabase object-key structure so that migration to
  object storage requires no changes to the path scheme.
- SupabaseStorageService talks to the Storage REST API directly over
  httpx (already a project dependency) rather than the supabase-py SDK,
  which would pull in postgrest/gotrue/realtime clients this application
  has no other use for — the same "no unnecessary dependency" call
  google_vision.py already made for OCR.
- The bucket is PRIVATE. save()/read()/delete() use the service-role key
  server-side only; the object key returned by save() and persisted on
  the document is never a public URL, since nothing in this application
  serves the raw file back to a browser (correction/review UI reads the
  persisted, structured data, not the source image/PDF).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC
from pathlib import Path

import aiofiles
import httpx

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
        content_type: str | None = None,
    ) -> str:
        """
        Persist raw file bytes and return the storage path/URL.

        Args:
            content: Raw file bytes to store.
            document_uuid: Unique identifier for this document (used in the path).
            original_filename: Original filename, used to determine the extension.
            organization_id: Tenant identifier for path namespacing.
            content_type: The uploaded file's MIME type, if known. Backends
                that send a Content-Type over HTTP (e.g. Supabase) use this;
                backends that don't need one (e.g. local disk) ignore it.

        Returns:
            Opaque string reference (local path or cloud URL) suitable for
            storing in the `invoices.raw_file_url` database column.

        Raises:
            StorageError: If the write operation fails.
        """

    @abstractmethod
    async def read(self, file_path: str) -> bytes:
        """
        Read back bytes previously written by save().

        Needed to run a stored document through the pipeline again without
        re-uploading it. Reading is the counterpart save() always implied;
        callers must not reach past the abstraction to the filesystem.

        Raises:
            StorageError: If the file is missing or cannot be read.
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


def _object_key(document_uuid: str, original_filename: str, organization_id: str) -> str:
    """
    The relative key every backend uses: {organization_id}/{year}/{month}/{uuid}{ext}.

    Shared here so LocalStorageService (a filesystem path built from it)
    and SupabaseStorageService (an object key, used as-is) can never
    drift on the naming scheme.
    """
    from datetime import datetime

    now = datetime.now(UTC)
    ext = Path(original_filename).suffix.lower() or ".bin"
    return f"{organization_id}/{now.year}/{now.month:02d}/{document_uuid}{ext}"


class LocalStorageService(StorageService):
    """
    Stores files on the local filesystem.

    Used in local development. Files are stored at::

        {base_path}/{organization_id}/{year}/{month}/{uuid}.{ext}

    This path structure is intentionally compatible with S3/Supabase key
    naming so that switching backends requires no changes to the scheme
    (see _object_key). Not used in the pilot deployment: the application
    container there is disposable and has no persistent disk.
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
        content_type: str | None = None,
    ) -> str:
        """Write file bytes to local disk and return the relative path.

        content_type is unused here — a filesystem path carries no
        Content-Type header, unlike the Supabase backend.
        """
        file_path = self._base / _object_key(document_uuid, original_filename, organization_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)

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

    async def read(self, file_path: str) -> bytes:
        """Read back the bytes save() wrote, for a document processed again."""
        try:
            async with aiofiles.open(Path(file_path), "rb") as f:
                return await f.read()
        except OSError as exc:
            logger.error("local_storage_read_failed", path=file_path, error=str(exc))
            raise StorageError(
                message="Failed to read the stored file.",
                detail={"path": file_path, "error": str(exc)},
            ) from exc

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


class SupabaseStorageService(StorageService):
    """
    Stores files in a private Supabase Storage bucket, over the Storage
    REST API (https://{project}.supabase.co/storage/v1/object/...).

    Used in the pilot deployment, where the application container is
    disposable (no persistent disk). The `file_path` this returns and
    later receives back in read()/delete() is the bucket-relative object
    key (e.g. "default/2026/09/<uuid>.pdf") — never a public URL. The
    bucket must be PRIVATE; every request here carries the service-role
    key server-side, which must never reach the frontend.
    """

    def __init__(
        self,
        supabase_url: str | None = None,
        service_key: str | None = None,
        bucket: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        base_url = (supabase_url or settings.supabase_url).rstrip("/")
        self._key = service_key or settings.supabase_service_key
        self._bucket = bucket or settings.supabase_bucket_name
        if not base_url or not self._key or not self._bucket:
            raise StorageError(
                message=(
                    "Supabase storage is not configured — SUPABASE_URL, "
                    "SUPABASE_SERVICE_KEY and SUPABASE_BUCKET_NAME are all required "
                    "when STORAGE_BACKEND=supabase."
                ),
            )
        self._object_base = f"{base_url}/storage/v1/object/{self._bucket}"
        # Injectable for tests; otherwise a fresh client per instance, closed
        # by the caller's process lifetime like the rest of this MVP's
        # per-request service construction (see get_storage_service()).
        self._client = client or httpx.AsyncClient(timeout=30.0)
        logger.info("supabase_storage_initialized", bucket=self._bucket)

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        # Supabase's API gateway requires BOTH `apikey` and `Authorization:
        # Bearer` on Storage REST requests — Authorization alone is rejected
        # (confirmed live: 400 "Invalid Compact JWS"/AccessDenied without
        # apikey; 200 with it present, same key).
        headers = {"Authorization": f"Bearer {self._key}", "apikey": self._key}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _log_http_error(event: str, exc: httpx.HTTPError, **fields: object) -> None:
        """Log a Storage HTTP failure, including Supabase's response body
        when one was received — never the request headers (which carry the
        API key), so the log can never contain credentials."""
        if isinstance(exc, httpx.HTTPStatusError):
            logger.error(
                event,
                status=exc.response.status_code,
                response_body=exc.response.text,
                **fields,
            )
        else:
            logger.error(event, error=str(exc), **fields)

    async def save(
        self,
        content: bytes,
        document_uuid: str,
        original_filename: str,
        organization_id: str = "default",
        content_type: str | None = None,
    ) -> str:
        """Upload file bytes to the bucket and return the object key."""
        key = _object_key(document_uuid, original_filename, organization_id)
        try:
            response = await self._client.post(
                f"{self._object_base}/{key}",
                content=content,
                headers={
                    **self._headers(content_type or "application/octet-stream"),
                    "x-upsert": "true",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._log_http_error("supabase_storage_write_failed", exc, key=key)
            raise StorageError(
                message="Failed to write file to Supabase storage.",
                detail={"key": key, "error": str(exc)},
            ) from exc

        logger.info("file_saved", document_uuid=document_uuid, path=key, size_bytes=len(content))
        return key

    async def read(self, file_path: str) -> bytes:
        """Download the bytes save() wrote, for a document processed again."""
        try:
            response = await self._client.get(f"{self._object_base}/{file_path}", headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._log_http_error("supabase_storage_read_failed", exc, path=file_path)
            raise StorageError(
                message="Failed to read the stored file.",
                detail={"path": file_path, "error": str(exc)},
            ) from exc
        return response.content

    async def delete(self, file_path: str) -> None:
        """Remove a file from the bucket."""
        try:
            response = await self._client.delete(f"{self._object_base}/{file_path}", headers=self._headers())
            # A file already gone is not a failure worth surfacing — same
            # idempotent-delete contract LocalStorageService's path.exists() gives.
            if response.status_code not in (200, 404):
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self._log_http_error("supabase_storage_delete_failed", exc, path=file_path)
            raise StorageError(
                message="Failed to delete file from Supabase storage.",
                detail={"path": file_path, "error": str(exc)},
            ) from exc
        logger.info("file_deleted", path=file_path)


def get_storage_service() -> StorageService:
    """Factory function that returns the configured storage backend."""
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalStorageService()
    if settings.storage_backend == "supabase":
        return SupabaseStorageService()
    # Future: if settings.storage_backend == "s3": return S3StorageService()
    raise ValueError(f"Unknown storage backend: {settings.storage_backend}")
