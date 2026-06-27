"""
Global Exception Handler Middleware — app/middleware/exception_handler.py

Catches every exception that propagates out of route handlers and converts
it into a standard JSON error response envelope.

Response envelope format::

    {
        "success": false,
        "data": null,
        "error": {
            "error_code": "ERR_FILE_TOO_LARGE",
            "message": "The uploaded file exceeds the maximum allowed size.",
            "detail": ...
        },
        "request_id": "req_abc123"
    }

Design decisions:
- All responses — success AND error — share the same envelope so clients
  never need to special-case the shape of an error response.
- Platform exceptions carry their own HTTP status and error_code, so this
  handler needs no switch statement.
- Unhandled Python exceptions are caught as a last resort and logged
  with full tracebacks before returning 500 to avoid leaking internals.
"""

from __future__ import annotations

import traceback
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.exceptions import InvoiceBaseException
from app.core.logging import get_logger

logger = get_logger(__name__)


def _build_error_response(
    request_id: str,
    status_code: int,
    error_code: str,
    message: str,
    detail: object = None,
) -> JSONResponse:
    """Build the standard error response envelope."""
    body: dict = {
        "success": False,
        "data": None,
        "error": {
            "error_code": error_code,
            "message": message,
        },
        "request_id": request_id,
    }
    if detail is not None:
        body["error"]["detail"] = detail
    return JSONResponse(status_code=status_code, content=body)


async def platform_exception_handler(
    request: Request,
    exc: InvoiceBaseException,
) -> JSONResponse:
    """
    Handle all InvoiceBaseException subclasses.

    Logs at WARNING level (these are expected operational errors, not bugs)
    and returns the exception's own HTTP status and error_code.
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.warning(
        "platform_error",
        error_code=exc.error_code,
        message=exc.message,
        path=str(request.url),
        request_id=request_id,
        **exc.context,
    )
    return _build_error_response(
        request_id=request_id,
        status_code=exc.http_status,
        error_code=exc.error_code,
        message=exc.message,
        detail=exc.detail,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """
    Handle Pydantic request validation errors (422 Unprocessable Entity).

    FastAPI raises these when request body or query params don't match the
    declared schema. Surfaces field-level errors to the client.
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    errors = exc.errors()
    logger.info(
        "request_validation_error",
        errors=errors,
        path=str(request.url),
        request_id=request_id,
    )
    return _build_error_response(
        request_id=request_id,
        status_code=422,
        error_code="ERR_VALIDATION",
        message="Request validation failed.",
        detail=errors,
    )


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """
    Catch-all handler for unexpected exceptions.

    Logs the full traceback at ERROR level. Returns a generic 500 response
    that does NOT expose internal details to the client.
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.error(
        "unhandled_exception",
        exc_type=type(exc).__name__,
        exc_message=str(exc),
        traceback=traceback.format_exc(),
        path=str(request.url),
        request_id=request_id,
    )
    return _build_error_response(
        request_id=request_id,
        status_code=500,
        error_code="ERR_INTERNAL",
        message="An unexpected internal error occurred. Please contact support.",
    )


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that assigns a unique request_id to every incoming request.

    The ID is stored on request.state and added to all log messages via
    structlog context vars. It is also echoed in the response header
    X-Request-ID so clients can correlate logs.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Bind request_id to all log messages within this request context
        import structlog
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
