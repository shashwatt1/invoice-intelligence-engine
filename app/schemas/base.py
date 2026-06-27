"""
API Response Schemas — app/schemas/base.py

Defines the standard envelope that wraps every API response.
Both success and error responses use the same outer shape,
ensuring clients have a predictable contract regardless of outcome.

Response contract::

    {
        "success": true | false,
        "data":    <payload> | null,
        "error":   null | { "error_code": "...", "message": "..." },
        "request_id": "uuid-string"
    }
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """Machine-readable error information."""

    error_code: str = Field(description="Stable error code for programmatic handling.")
    message: str = Field(description="Human-readable error description.")
    detail: Any = Field(default=None, description="Optional field-level or contextual detail.")


class APIResponse(BaseModel, Generic[T]):
    """
    Standard success response envelope.

    All route handlers should return this type (or its subclass) so that
    the API surface is consistent across the entire platform.

    Example::

        return APIResponse(data={"document_uuid": "..."})
    """

    success: bool = True
    data: T | None = None
    error: ErrorDetail | None = None
    request_id: str | None = Field(default=None, description="Echoed from X-Request-ID header.")


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Paginated list response envelope for collection endpoints.

    Example::

        return PaginatedResponse(items=[...], total=482, page=1, page_size=50)
    """

    success: bool = True
    items: list[T]
    total: int = Field(description="Total number of records matching the query.")
    page: int = Field(ge=1, description="Current page number (1-indexed).")
    page_size: int = Field(ge=1, le=200, description="Number of records per page.")
    request_id: str | None = None
