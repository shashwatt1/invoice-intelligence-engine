"""
Application Factory — app/main.py

Creates and configures the FastAPI application instance.

This module is the composition root for the entire backend. It:
    1. Configures structured logging.
    2. Creates the FastAPI application with full OpenAPI metadata.
    3. Registers middleware (CORS, request ID, exception handlers).
    4. Registers all API routers.
    5. Registers startup and shutdown lifecycle event handlers.

Design decisions:
- The `create_app()` factory pattern (instead of a module-level `app = FastAPI()`)
  makes the application fully testable: tests call `create_app()` with
  overridden settings instead of importing a shared global.
- CORS is configured from settings so it works across development/staging/production
  without code changes.
- OpenAPI docs are disabled in production (SECURITY best practice).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import InvoiceBaseException
from app.core.logging import configure_logging, get_logger
from app.middleware.exception_handler import (
    RequestIDMiddleware,
    platform_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)

# Logger is obtained after configure_logging() is called in create_app()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan context manager.

    Code before `yield` runs at startup; code after runs at shutdown.
    This replaces the deprecated @app.on_event("startup") pattern.
    """
    settings: Settings = app.state.settings

    # ------------------------------------------------------------------
    # STARTUP
    # ------------------------------------------------------------------
    logger.info(
        "application_starting",
        app_name=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        api_prefix=settings.api_prefix,
    )

    # Ensure upload and log directories exist
    Path(settings.upload_path).mkdir(parents=True, exist_ok=True)
    Path(settings.log_file_path).parent.mkdir(parents=True, exist_ok=True)

    # Future startup tasks (Sprint 2+):
    # - Warm up database connection pool
    # - Verify object storage bucket is accessible
    # - Load vendor prompt cache from DB

    logger.info("application_ready", host=settings.api_host, port=settings.api_port)

    yield

    # ------------------------------------------------------------------
    # SHUTDOWN
    # ------------------------------------------------------------------
    logger.info("application_shutting_down")

    # Future shutdown tasks (Sprint 2+):
    # - Drain in-flight async tasks
    # - Close connection pool gracefully
    # - Flush any buffered log events

    logger.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """
    Application factory — creates and returns a fully configured FastAPI app.

    Args:
        settings: Optional Settings override (used in tests to inject
                  test-specific configuration without touching .env).

    Returns:
        Configured FastAPI application instance.
    """
    if settings is None:
        settings = get_settings()

    # Configure logging before anything else so all startup logs are captured
    configure_logging()

    # Show OpenAPI docs only in non-production environments
    docs_url = "/docs" if not settings.is_production else None
    redoc_url = "/redoc" if not settings.is_production else None
    openapi_url = "/openapi.json" if not settings.is_production else None

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "## Invoice Intelligence Platform API\n\n"
            "AI-powered invoice extraction and intelligence engine. "
            "Converts PDF and image invoices into validated, structured "
            "ERP-ready data.\n\n"
            "### Phase 1 — Core Extraction MVP\n"
            "- Document upload and ingestion\n"
            "- PDF text extraction (direct and OCR paths)\n"
            "- AI-based semantic structuring\n"
            "- Mathematical validation\n"
            "- PostgreSQL storage and CSV export\n\n"
            "### Authentication\n"
            "All requests require an `X-API-Key` header (Sprint 2+)."
        ),
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        contact={
            "name": "Invoice Intelligence Engineering",
            "url": "https://github.com/shashwatt1/invoice-intelligence-engine",
        },
        license_info={"name": "MIT"},
        lifespan=lifespan,
    )

    # Store settings on app.state so lifespan and routes can access them
    app.state.settings = settings

    # ------------------------------------------------------------------
    # Middleware (order matters — applied in reverse registration order)
    # ------------------------------------------------------------------

    # GZip compression for responses > 1KB
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # CORS — allow configured origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # Request ID — assigns a UUID to each request for log correlation
    app.add_middleware(RequestIDMiddleware)

    # ------------------------------------------------------------------
    # Exception handlers
    # ------------------------------------------------------------------
    app.add_exception_handler(InvoiceBaseException, platform_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    app.include_router(api_router, prefix=settings.api_prefix)

    return app


# ---------------------------------------------------------------------------
# Module-level app instance — used by Uvicorn: `uvicorn app.main:app`
# ---------------------------------------------------------------------------
app = create_app()
