"""
tests/test_storage_service.py — LOCAL OFFLINE TESTS for the storage
abstraction (app/services/storage_service.py).

These run with no external services: LocalStorageService against a real
temp directory, SupabaseStorageService against a mocked HTTP transport
(httpx.MockTransport) that behaves like the Supabase Storage REST API —
proving the request shape (URL, auth header, object key, error mapping)
is correct without needing a real Supabase project.

This is NOT a substitute for a real round-trip against a live Supabase
bucket. That is a separate, deliberately unautomated PILOT ENVIRONMENT
SMOKE TEST — see scripts/verify_supabase_storage.py, which a person runs
manually once real pilot credentials exist. Nothing in this file claims
to have exercised the real Supabase API.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

import app.services.storage_service as storage_service_module
from app.core.exceptions import StorageError
from app.services.storage_service import (
    LocalStorageService,
    SupabaseStorageService,
    _object_key,
    get_storage_service,
)


class TestObjectKeyScheme:
    """The naming scheme both backends share — {org}/{year}/{month}/{uuid}{ext}."""

    def test_key_includes_organization_year_month_uuid_and_extension(self):
        key = _object_key("abc-123", "invoice.PDF", "default")
        parts = key.split("/")
        assert parts[0] == "default"
        assert len(parts) == 4
        assert parts[3] == "abc-123.pdf"  # extension lowercased

    def test_missing_extension_falls_back_to_bin(self):
        key = _object_key("abc-123", "no_extension", "default")
        assert key.endswith("abc-123.bin")


class TestLocalStorageService:
    """Round-trip against a real temp directory — the local-dev backend."""

    @pytest.fixture
    def storage(self, tmp_path):
        return LocalStorageService(base_path=str(tmp_path))

    async def test_save_then_read_returns_the_same_bytes(self, storage):
        path = await storage.save(b"hello invoice", "doc-1", "invoice.pdf")
        assert await storage.read(path) == b"hello invoice"

    async def test_save_path_follows_the_shared_object_key_scheme(self, storage, tmp_path):
        path = await storage.save(b"x", "doc-2", "photo.JPG", organization_id="acme")
        now = datetime.now(UTC)
        assert path == str(tmp_path / "acme" / str(now.year) / f"{now.month:02d}" / "doc-2.jpg")

    async def test_delete_removes_the_file_and_is_idempotent(self, storage):
        path = await storage.save(b"gone soon", "doc-3", "invoice.pdf")
        await storage.delete(path)
        await storage.delete(path)  # second delete of an already-gone file must not raise
        with pytest.raises(StorageError):
            await storage.read(path)

    async def test_reading_a_nonexistent_path_raises_storage_error(self, storage, tmp_path):
        with pytest.raises(StorageError):
            await storage.read(str(tmp_path / "never-written.pdf"))


class TestSupabaseStorageServiceRequestShape:
    """
    Offline: a mocked transport standing in for the Supabase Storage REST
    API. Proves the exact request contract this service makes, not that
    a real Supabase project accepts it.
    """

    def _service(self, handler) -> SupabaseStorageService:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return SupabaseStorageService(
            supabase_url="https://project.supabase.co",
            service_key="service-role-key",
            bucket="invoice-sources",
            client=client,
        )

    async def test_save_posts_to_the_object_endpoint_with_auth_and_upsert(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = request.content
            return httpx.Response(200, json={"Key": "ok"})

        service = self._service(handler)
        key = await service.save(b"pdf bytes", "doc-1", "invoice.pdf", organization_id="pilot")

        assert seen["method"] == "POST"
        assert seen["url"] == f"https://project.supabase.co/storage/v1/object/invoice-sources/{key}"
        assert seen["headers"]["authorization"] == "Bearer service-role-key"
        assert seen["headers"]["apikey"] == "service-role-key"
        assert seen["headers"]["x-upsert"] == "true"
        assert seen["body"] == b"pdf bytes"
        assert key.startswith("pilot/")
        assert key.endswith("doc-1.pdf")

    async def test_read_gets_from_the_object_endpoint_and_returns_the_body(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.headers["authorization"] == "Bearer service-role-key"
            assert request.headers["apikey"] == "service-role-key"
            return httpx.Response(200, content=b"stored bytes")

        service = self._service(handler)
        assert await service.read("pilot/2026/09/doc-1.pdf") == b"stored bytes"

    async def test_delete_sends_delete_and_treats_404_as_already_gone(self):
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            assert request.headers["authorization"] == "Bearer service-role-key"
            assert request.headers["apikey"] == "service-role-key"
            calls.append(1)
            return httpx.Response(404 if len(calls) > 1 else 200)

        service = self._service(handler)
        await service.delete("pilot/2026/09/doc-1.pdf")  # 200
        await service.delete("pilot/2026/09/doc-1.pdf")  # 404 — must not raise

    async def test_a_failed_upload_raises_storage_error_not_a_raw_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Invalid token"})

        service = self._service(handler)
        with pytest.raises(StorageError):
            await service.save(b"x", "doc-1", "invoice.pdf")

    async def test_a_failed_read_raises_storage_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "not found"})

        service = self._service(handler)
        with pytest.raises(StorageError):
            await service.read("pilot/missing.pdf")

    @pytest.mark.parametrize(
        ("content_type", "expected_header"),
        [
            ("image/jpeg", "image/jpeg"),
            ("application/pdf", "application/pdf"),
            (None, "application/octet-stream"),
        ],
    )
    async def test_save_sends_the_given_content_type_or_falls_back_to_octet_stream(
        self, content_type, expected_header
    ):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["content_type"] = request.headers["content-type"]
            return httpx.Response(200, json={"Key": "ok"})

        service = self._service(handler)
        await service.save(b"bytes", "doc-1", "invoice.bin", content_type=content_type)

        assert seen["content_type"] == expected_header

    async def test_a_failed_upload_logs_the_status_and_response_body_without_the_key(self, monkeypatch):
        error_body = {
            "statusCode": "403",
            "error": "Unauthorized",
            "message": "Invalid Compact JWS",
            "code": "AccessDenied",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json=error_body)

        calls: list[dict] = []
        monkeypatch.setattr(
            storage_service_module.logger,
            "error",
            lambda event, **kwargs: calls.append({"event": event, **kwargs}),
        )

        service = self._service(handler)
        with pytest.raises(StorageError):
            await service.save(b"x", "doc-1", "invoice.pdf")

        assert len(calls) == 1
        logged = calls[0]
        assert logged["status"] == 400
        assert "Invalid Compact JWS" in logged["response_body"]
        assert "AccessDenied" in logged["response_body"]
        # The response body must be logged, but never the secret key used to
        # authenticate the request that produced it, and never the raw headers.
        assert "service-role-key" not in str(logged)
        assert "headers" not in logged


class TestUploadServiceForwardsContentType:
    """UploadService.handle_upload() must pass the uploaded file's real
    MIME type through to StorageService.save(), so Supabase stops
    receiving a hardcoded application/octet-stream for every file."""

    class _RecordingStorage:
        def __init__(self):
            self.save_kwargs: dict | None = None

        async def save(self, content, document_uuid, original_filename, organization_id="default", content_type=None):
            self.save_kwargs = {"content_type": content_type}
            return "irrelevant/path"

    @pytest.mark.parametrize("content_type", ["image/jpeg", "application/pdf"])
    async def test_handle_upload_forwards_the_uploaded_files_content_type(self, content_type):
        # Only allowed MIME types reach storage.save() at all — _validate_mime_type
        # rejects anything else (including a missing Content-Type) earlier in the
        # pipeline. The octet-stream fallback for an unknown type is a storage-layer
        # concern, covered separately in TestSupabaseStorageServiceRequestShape.
        import io

        from starlette.datastructures import Headers, UploadFile

        from app.services.upload_service import UploadService

        headers = Headers({"content-type": content_type})
        upload_file = UploadFile(io.BytesIO(b"x" * 2048), filename="invoice.bin", headers=headers)

        storage = self._RecordingStorage()
        service = UploadService(storage=storage)
        await service.handle_upload(upload_file)

        assert storage.save_kwargs == {"content_type": content_type}


class TestSupabaseStorageServiceConfiguration:
    """Fails fast, in the constructor, when required settings are missing —
    never silently sends requests with a blank auth token or bucket."""

    @pytest.fixture(autouse=True)
    def _no_real_supabase_settings(self, monkeypatch):
        """
        These tests pass "" for the constructor's own supabase_url/
        service_key/bucket args, expecting the constructor's fallback
        (`arg or settings.supabase_url`, etc.) to also come up empty. But
        get_settings() reads real SUPABASE_* values from a developer's
        local .env for the live pilot backend — and get_settings() is
        lru_cached, so those real values leak in here unless overridden.

        A blank os.environ value (not delenv) is required: pydantic-settings
        resolves env vars ahead of the .env file, so delenv would just fall
        through to the same real .env value this fixture needs to hide.
        """
        from app.core.config import get_settings

        monkeypatch.setenv("SUPABASE_URL", "")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "")
        monkeypatch.setenv("SUPABASE_BUCKET_NAME", "")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_missing_configuration_raises_immediately(self):
        with pytest.raises(StorageError):
            SupabaseStorageService(supabase_url="", service_key="", bucket="")

    def test_missing_bucket_alone_raises(self):
        with pytest.raises(StorageError):
            SupabaseStorageService(supabase_url="https://project.supabase.co", service_key="key", bucket="")


class TestStorageBackendFactory:
    """get_storage_service() selects the backend the STORAGE_BACKEND setting names."""

    def test_local_is_the_default_backend(self, monkeypatch):
        from app.core.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("STORAGE_BACKEND", "local")
        try:
            assert isinstance(get_storage_service(), LocalStorageService)
        finally:
            get_settings.cache_clear()

    def test_supabase_backend_is_selected_when_configured(self, monkeypatch):
        from app.core.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("STORAGE_BACKEND", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "key")
        monkeypatch.setenv("SUPABASE_BUCKET_NAME", "bucket")
        try:
            assert isinstance(get_storage_service(), SupabaseStorageService)
        finally:
            get_settings.cache_clear()

    def test_an_unknown_backend_name_fails_fast(self, monkeypatch):
        from app.core.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("STORAGE_BACKEND", "azure")
        try:
            with pytest.raises(ValueError):
                get_storage_service()
        finally:
            get_settings.cache_clear()
