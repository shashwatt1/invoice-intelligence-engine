"""
tests/test_health.py — Health endpoint tests.

Tests for:
    GET /api/v1/health
    GET /api/v1/version

Note: /ready is not tested here because it requires a live database.
It is tested in integration tests (tests/integration/).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    """GET /health must return 200 and status=healthy."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_response_shape(client: AsyncClient) -> None:
    """Health response must include all required fields."""
    response = await client.get("/api/v1/health")
    data = response.json()["data"]
    assert "status" in data
    assert "database" in data
    assert "storage" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_version_returns_app_info(client: AsyncClient) -> None:
    """GET /version must return app_name, version, and environment."""
    response = await client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["app_name"] == "Invoice Intelligence Platform"
    assert data["version"] == "0.1.0"
    assert data["environment"] == "development"
