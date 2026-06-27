"""
tests/conftest.py — Shared pytest fixtures for the entire test suite.

This file is automatically loaded by pytest before any test module.
It provides:
    - A test FastAPI application with overridden settings.
    - An async HTTP test client (httpx.AsyncClient).
    - A temporary upload directory per test session.

Design decisions:
- Tests use a separate in-memory or test-database URL to avoid
  polluting the development database.
- get_settings is cleared from lru_cache before each test session
  so environment overrides take effect.
- All fixtures are async to match the async application.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Override settings BEFORE importing the app so lru_cache picks them up
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql+psycopg2://test:test@localhost:5432/test_db")
os.environ.setdefault("LOG_FILE_ENABLED", "false")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("STORAGE_BACKEND", "local")


@pytest.fixture(scope="session")
def temp_upload_dir(tmp_path_factory):
    """Create a temporary upload directory for the test session."""
    return tmp_path_factory.mktemp("uploads")


@pytest.fixture(scope="session", autouse=True)
def override_upload_path(temp_upload_dir):
    """Override UPLOAD_PATH to use a temp directory for all tests."""
    os.environ["UPLOAD_PATH"] = str(temp_upload_dir)
    # Clear the lru_cache so the new env var is picked up
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def app():
    """Create a test FastAPI application instance."""
    from app.main import create_app
    return create_app()


@pytest_asyncio.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """
    Provide an async HTTP client bound to the test application.

    Use this in tests instead of making real HTTP requests:

        async def test_health(client):
            response = await client.get("/api/v1/health")
            assert response.status_code == 200
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """
    Minimal valid PDF bytes padded to exceed the 1 KB minimum upload size.

    This is the smallest valid PDF structure with a comment block appended
    to push it past the MIN_FILE_SIZE_BYTES (1024) threshold.
    """
    header = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\n"
        b"startxref\n9\n%%EOF"
    )
    # Pad with PDF comment bytes to exceed 1 KB
    padding = b"% " + b"x" * 1024
    return header + padding


@pytest.fixture
def sample_png_bytes() -> bytes:
    """
    Minimal valid PNG bytes padded to exceed the 1 KB minimum upload size.
    """
    import base64
    # Minimal 1x1 white PNG (base64 encoded)
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
    )
    base_png = base64.b64decode(png_b64)
    # Append a PNG comment-style padding block to exceed 1 KB
    padding = b"\x00" * (1200 - len(base_png))
    return base_png + padding
