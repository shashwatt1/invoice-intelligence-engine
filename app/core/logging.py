"""
Structured Logging — app/core/logging.py

Configures structlog for JSON-formatted, context-aware logging.
Produces machine-parseable JSON in production and colourised console
output in development — controlled by LOG_FORMAT environment variable.

Design decisions:
- structlog is chosen over stdlib logging because it natively supports
  structured key-value pairs, making logs queryable in log aggregators.
- A single `configure_logging()` call is made at application startup.
- Log level is configurable per environment.
- File logging uses a rotating handler to prevent unbounded disk growth.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    """
    Set up structlog with processors appropriate for the current environment.

    Call this once at application startup, before any log messages are emitted.
    """
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # ------------------------------------------------------------------
    # Shared processors applied to every log event regardless of renderer
    # ------------------------------------------------------------------
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    # ------------------------------------------------------------------
    # Handler configuration
    # ------------------------------------------------------------------
    handlers: list[logging.Handler] = []

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    handlers.append(console_handler)

    # File handler (optional, rotating)
    if settings.log_file_enabled:
        log_path = Path(settings.log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=30,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        handlers.append(file_handler)

    # ------------------------------------------------------------------
    # Renderer selection — JSON in production/staging, pretty in dev
    # ------------------------------------------------------------------
    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # ------------------------------------------------------------------
    # Configure structlog
    # ------------------------------------------------------------------
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ------------------------------------------------------------------
    # Configure stdlib logging to route through structlog
    # ------------------------------------------------------------------
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove any existing handlers (avoids duplicate messages in tests)
    root_logger.handlers.clear()

    for handler in handlers:
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.DEBUG if settings.database_echo else logging.WARNING
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Return a bound logger for the given module name.

    Usage::

        from app.core.logging import get_logger
        logger = get_logger(__name__)
        logger.info("event happened", key="value")
    """
    return structlog.get_logger(name)
