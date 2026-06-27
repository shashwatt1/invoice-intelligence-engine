"""
Custom Exceptions — app/core/exceptions.py

Defines the exception hierarchy for the Invoice Intelligence Platform.

Design decisions:
- Every exception carries a machine-readable `error_code` string.
  This lets clients handle errors programmatically without parsing messages.
- HTTP status codes are embedded on exceptions so the global handler
  can render the correct response without switch statements.
- The hierarchy is flat enough to be readable but deep enough to allow
  fine-grained catching in service layers.
"""

from __future__ import annotations

from typing import Any


class InvoiceBaseException(Exception):
    """
    Base exception for all platform-specific errors.

    All custom exceptions must inherit from this class so that the global
    exception handler can distinguish platform errors from unexpected bugs.
    """

    error_code: str = "ERR_INTERNAL"
    http_status: int = 500
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        detail: Any = None,
        **context: Any,
    ) -> None:
        self.message = message or self.__class__.message
        self.detail = detail
        self.context = context
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the exception for API error responses."""
        payload: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


# ---------------------------------------------------------------------------
# Upload & Ingestion Errors
# ---------------------------------------------------------------------------


class FileTooLargeError(InvoiceBaseException):
    """Raised when an uploaded file exceeds the configured size limit."""

    error_code = "ERR_FILE_TOO_LARGE"
    http_status = 413
    message = "The uploaded file exceeds the maximum allowed size."


class UnsupportedFileTypeError(InvoiceBaseException):
    """Raised when an uploaded file has an unsupported MIME type."""

    error_code = "ERR_UNSUPPORTED_FORMAT"
    http_status = 415
    message = "The uploaded file format is not supported. Accepted: PDF, PNG, JPEG."


class DuplicateDocumentError(InvoiceBaseException):
    """Raised when a file with the same SHA-256 hash already exists."""

    error_code = "ERR_DUPLICATE_DOCUMENT"
    http_status = 409
    message = "This document has already been uploaded and processed."


class EmptyFileError(InvoiceBaseException):
    """Raised when an uploaded file is empty or below the minimum size."""

    error_code = "ERR_EMPTY_FILE"
    http_status = 422
    message = "The uploaded file is empty or too small to process."


# ---------------------------------------------------------------------------
# Storage Errors
# ---------------------------------------------------------------------------


class StorageError(InvoiceBaseException):
    """Raised when writing to or reading from object storage fails."""

    error_code = "ERR_STORAGE_UNAVAILABLE"
    http_status = 503
    message = "File storage is temporarily unavailable. Please try again."


# ---------------------------------------------------------------------------
# Database Errors
# ---------------------------------------------------------------------------


class DatabaseError(InvoiceBaseException):
    """Raised for unexpected database-level failures."""

    error_code = "ERR_DATABASE_FAILURE"
    http_status = 503
    message = "A database error occurred. Please try again."


class RecordNotFoundError(InvoiceBaseException):
    """Raised when a requested database record does not exist."""

    error_code = "ERR_NOT_FOUND"
    http_status = 404
    message = "The requested resource was not found."


# ---------------------------------------------------------------------------
# Processing Errors (placeholders — implemented in Sprint 2+)
# ---------------------------------------------------------------------------


class OCRExtractionError(InvoiceBaseException):
    """Raised when OCR returns zero tokens from a non-empty image."""

    error_code = "ERR_OCR_FAILED"
    http_status = 422
    message = "OCR extraction failed. The document may be unreadable."


class AIStructuringError(InvoiceBaseException):
    """Raised when the AI structuring layer fails after all retries."""

    error_code = "ERR_AI_STRUCTURING_FAILED"
    http_status = 422
    message = "AI-based invoice structuring failed. The document will be queued for review."


class ValidationError(InvoiceBaseException):
    """Raised when extracted invoice data fails mathematical validation."""

    error_code = "ERR_VALIDATION_FAILED"
    http_status = 422
    message = "Invoice data failed validation checks."


# ---------------------------------------------------------------------------
# Authorization Errors
# ---------------------------------------------------------------------------


class AuthenticationError(InvoiceBaseException):
    """Raised when an API request is missing or has invalid credentials."""

    error_code = "ERR_UNAUTHORIZED"
    http_status = 401
    message = "Authentication credentials are missing or invalid."


class PermissionDeniedError(InvoiceBaseException):
    """Raised when an authenticated user lacks permission for an action."""

    error_code = "ERR_FORBIDDEN"
    http_status = 403
    message = "You do not have permission to perform this action."
