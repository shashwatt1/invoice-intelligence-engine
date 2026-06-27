"""
tests/test_upload.py — Upload endpoint tests.

Tests for:
    POST /api/v1/upload — happy paths and all error conditions.

These tests use an in-memory async HTTP client (no real network)
and a temporary upload directory (no real filesystem side effects beyond temp).

All tests are isolated — each test gets a fresh client per fixture scope.
"""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upload_pdf_returns_202(client: AsyncClient, sample_pdf_bytes: bytes) -> None:
    """Uploading a valid PDF must return 202 Accepted with document_uuid."""
    response = await client.post(
        "/api/v1/upload",
        files={"file": ("invoice.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert "document_uuid" in data
    assert len(data["document_uuid"]) == 36  # UUID format
    assert data["status"] == "INGESTED"
    assert data["mime_type"] == "application/pdf"
    assert data["filename"] == "invoice.pdf"


@pytest.mark.asyncio
async def test_upload_png_returns_202(client: AsyncClient, sample_png_bytes: bytes) -> None:
    """Uploading a valid PNG image must return 202 Accepted."""
    response = await client.post(
        "/api/v1/upload",
        files={"file": ("scan.png", io.BytesIO(sample_png_bytes), "image/png")},
    )
    assert response.status_code == 202
    data = response.json()["data"]
    assert data["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_upload_returns_file_size(client: AsyncClient, sample_pdf_bytes: bytes) -> None:
    """Upload response must include the correct file size in bytes."""
    response = await client.post(
        "/api/v1/upload",
        files={"file": ("invoice.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    data = response.json()["data"]
    assert data["file_size_bytes"] == len(sample_pdf_bytes)


@pytest.mark.asyncio
async def test_upload_unsupported_format_returns_415(client: AsyncClient) -> None:
    """Uploading a .txt file must return 415 Unsupported Media Type."""
    response = await client.post(
        "/api/v1/upload",
        files={"file": ("document.txt", io.BytesIO(b"hello world" * 200), "text/plain")},
    )
    assert response.status_code == 415
    body = response.json()
    assert body["success"] is False
    assert body["error"]["error_code"] == "ERR_UNSUPPORTED_FORMAT"


@pytest.mark.asyncio
async def test_upload_empty_file_returns_422(client: AsyncClient) -> None:
    """Uploading a file smaller than 1KB must return 422."""
    response = await client.post(
        "/api/v1/upload",
        files={"file": ("empty.pdf", io.BytesIO(b"tiny"), "application/pdf")},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["error_code"] == "ERR_EMPTY_FILE"


@pytest.mark.asyncio
async def test_upload_file_too_large_returns_413(client: AsyncClient) -> None:
    """Uploading a file exceeding 25 MB must return 413."""
    # Generate 26 MB of data
    oversized = b"x" * (26 * 1024 * 1024)
    response = await client.post(
        "/api/v1/upload",
        files={"file": ("big.pdf", io.BytesIO(oversized), "application/pdf")},
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error"]["error_code"] == "ERR_FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_upload_response_has_request_id_header(
    client: AsyncClient, sample_pdf_bytes: bytes
) -> None:
    """Every response must include an X-Request-ID header."""
    response = await client.post(
        "/api/v1/upload",
        files={"file": ("invoice.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    assert "x-request-id" in response.headers
    assert len(response.headers["x-request-id"]) == 36


@pytest.mark.asyncio
async def test_upload_creates_unique_uuids(
    client: AsyncClient, sample_pdf_bytes: bytes
) -> None:
    """Each upload must return a different document_uuid."""
    r1 = await client.post(
        "/api/v1/upload",
        files={"file": ("inv1.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    r2 = await client.post(
        "/api/v1/upload",
        files={"file": ("inv2.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")},
    )
    uuid1 = r1.json()["data"]["document_uuid"]
    uuid2 = r2.json()["data"]["document_uuid"]
    assert uuid1 != uuid2
