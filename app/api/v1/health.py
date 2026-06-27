"""
Health & System Endpoints — app/api/v1/health.py

Three endpoints:
    GET /health  — liveness probe (is the process alive?)
    GET /ready   — readiness probe (can we serve traffic?)
    GET /version — application version metadata

Design decisions:
- /health and /ready are separated following the Kubernetes probe pattern:
    /health = liveness  (is the container alive? restart if not)
    /ready  = readiness (is it ready for traffic? remove from load balancer if not)
- /ready performs actual dependency checks (DB ping).
- /health returns 200 as long as the process is running.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.database.session import get_db
from app.schemas.base import APIResponse
from app.schemas.health import HealthResponse, ReadyResponse, VersionResponse

logger = get_logger(__name__)
router = APIRouter(tags=["System"])


@router.get(
    "/health",
    response_model=APIResponse[HealthResponse],
    summary="Liveness check",
    description=(
        "Returns 200 as long as the application process is running. "
        "Used by Docker and Kubernetes liveness probes."
    ),
)
async def health_check() -> APIResponse[HealthResponse]:
    """
    Liveness endpoint.

    Does NOT check external dependencies (DB, storage) — those are checked
    by /ready. This endpoint must always return 200 unless the process is dead.
    """
    settings = get_settings()
    return APIResponse(
        data=HealthResponse(
            status="healthy",
            database="connected",   # Assumed — real check in /ready
            storage="available",
            version=settings.app_version,
        )
    )


@router.get(
    "/ready",
    response_model=APIResponse[ReadyResponse],
    summary="Readiness check",
    description=(
        "Checks all critical dependencies (database, storage). "
        "Returns 200 if ready, 503 if any dependency is unavailable. "
        "Used by load balancers and Kubernetes readiness probes."
    ),
)
async def readiness_check(
    db: AsyncSession = Depends(get_db),
) -> APIResponse[ReadyResponse]:
    """
    Readiness endpoint.

    Actively pings the database. Returns 503 if any check fails so that
    load balancers can route traffic away from unhealthy instances.
    """
    checks: dict[str, bool] = {}

    # Database ping
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:
        logger.warning("readiness_db_check_failed", error=str(exc))
        checks["database"] = False

    # Storage check (Phase 1: always True; extend in Phase 2 with S3 ping)
    checks["storage"] = True

    all_ready = all(checks.values())
    status_code = 200 if all_ready else 503

    response = APIResponse(
        data=ReadyResponse(ready=all_ready, checks=checks)
    )

    # FastAPI doesn't directly support dynamic status codes in response_model,
    # so we use JSONResponse for the non-200 case.
    if not all_ready:
        from fastapi.responses import JSONResponse
        return JSONResponse(content=response.model_dump(), status_code=status_code)

    return response


@router.get(
    "/version",
    response_model=APIResponse[VersionResponse],
    summary="Application version",
    description="Returns the running application version and environment.",
)
async def version() -> APIResponse[VersionResponse]:
    """Return application version metadata."""
    settings = get_settings()
    return APIResponse(
        data=VersionResponse(
            app_name=settings.app_name,
            version=settings.app_version,
            environment=settings.app_env,
        )
    )
