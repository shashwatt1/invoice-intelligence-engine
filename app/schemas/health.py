"""
Health Check Schemas — app/schemas/health.py

Pydantic models for the health, readiness, and version endpoints.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Response for GET /health — confirms the application is running."""

    status: Literal["healthy", "degraded", "unhealthy"]
    database: Literal["connected", "disconnected"]
    storage: Literal["available", "unavailable"]
    version: str = Field(description="Current application version string.")


class ReadyResponse(BaseModel):
    """
    Response for GET /ready — confirms the application is ready to serve traffic.

    Returns 200 when all dependencies are reachable, 503 otherwise.
    Used by Kubernetes readiness probes and load balancers.
    """

    ready: bool
    checks: dict[str, bool] = Field(
        description="Map of dependency name to availability flag."
    )


class VersionResponse(BaseModel):
    """Response for GET /version — returns version metadata."""

    app_name: str
    version: str
    environment: str
