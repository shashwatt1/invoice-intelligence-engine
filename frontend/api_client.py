"""
frontend/api_client.py — HTTP client for the Invoice Intelligence API.

The frontend's ONLY integration point with the backend. No imports from
app.* anywhere in frontend/ — this module could be ported to a React/
Next.js data layer verbatim, which is the architectural guarantee that
the backend stays the single source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")
TIMEOUT = httpx.Timeout(30.0, connect=3.0)


@dataclass
class APIError(Exception):
    """Structured error surfaced from the platform's error envelope."""

    status_code: int
    error_code: str
    message: str
    detail: Any = None

    def __str__(self) -> str:  # pragma: no cover — display helper
        return f"{self.error_code}: {self.message}"


def _raise_for_envelope(response: httpx.Response) -> dict[str, Any]:
    """Return the parsed body, raising APIError on failure envelopes."""
    try:
        body = response.json()
    except ValueError as exc:
        raise APIError(response.status_code, "ERR_BAD_RESPONSE", response.text[:200]) from exc
    if response.status_code >= 400 or body.get("success") is False:
        error = body.get("error") or {}
        raise APIError(
            status_code=response.status_code,
            error_code=error.get("error_code", "ERR_UNKNOWN"),
            message=error.get("message", "Unexpected API error."),
            detail=error.get("detail"),
        )
    return body


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(base_url=API_BASE_URL, timeout=TIMEOUT) as client:
        return _raise_for_envelope(client.get(path, params=params))


def api_available() -> bool:
    """Lightweight liveness probe for the sidebar indicator."""
    try:
        with httpx.Client(base_url=API_BASE_URL, timeout=httpx.Timeout(2.0)) as client:
            return client.get("/health").status_code == 200
    except httpx.HTTPError:
        return False


def process_invoice(filename: str, content: bytes, mime_type: str) -> dict[str, Any]:
    """POST /invoices/process → ProcessAccepted data (202)."""
    with httpx.Client(base_url=API_BASE_URL, timeout=TIMEOUT) as client:
        response = client.post(
            "/invoices/process", files={"file": (filename, content, mime_type)}
        )
    return _raise_for_envelope(response)["data"]


def get_document_status(document_id: str) -> dict[str, Any]:
    """GET /documents/{id} → DocumentStatusData."""
    return _get(f"/documents/{document_id}")["data"]


def get_invoice(invoice_id: str) -> dict[str, Any]:
    """GET /invoices/{id} → InvoiceDetailData."""
    return _get(f"/invoices/{invoice_id}")["data"]


def list_invoices(
    *,
    search: str | None = None,
    status: str | None = None,
    sort_by: str = "created_at",
    descending: bool = True,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """GET /invoices → PaginatedResponse (full envelope: items/total/page)."""
    params: dict[str, Any] = {
        "sort_by": sort_by,
        "descending": descending,
        "page": page,
        "page_size": page_size,
    }
    if search:
        params["search"] = search
    if status:
        params["status"] = status
    return _get("/invoices", params=params)


def dashboard_summary(recent_limit: int = 8) -> dict[str, Any]:
    """GET /dashboard/summary → DashboardData."""
    return _get("/dashboard/summary", params={"recent_limit": recent_limit})["data"]
