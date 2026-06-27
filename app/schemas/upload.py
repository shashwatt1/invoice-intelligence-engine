"""
Upload Schemas — app/schemas/upload.py

Pydantic models for the upload API request and response contracts.
These are the typed inputs/outputs for the file upload endpoint (FR-001).

Design decisions:
- Response returns exactly what design.md specifies for POST /upload.
- All fields are documented so they appear correctly in Swagger/Redoc.
- `document_uuid` is returned as a string (not UUID object) for JSON serialisability.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    """
    Response body returned by POST /api/v1/upload after a successful upload.

    The client uses `document_uuid` to track the document through
    the processing pipeline via subsequent GET /invoice/{id} calls.
    """

    document_uuid: str = Field(
        description="Unique identifier assigned to this document. Use this to track processing."
    )
    filename: str = Field(description="Original filename as provided by the client.")
    file_size_bytes: int = Field(description="Size of the uploaded file in bytes.")
    mime_type: str = Field(description="Detected MIME type of the uploaded file.")
    file_path: str = Field(
        description="Storage path or URL where the raw file is persisted."
    )
    status: str = Field(
        default="INGESTED",
        description="Initial document status. Always INGESTED at upload time.",
    )
    created_at: datetime = Field(description="UTC timestamp when the upload was recorded.")


class UploadErrorResponse(BaseModel):
    """Error detail for failed uploads (used in OpenAPI documentation only)."""

    error_code: str
    message: str
    detail: object = None
