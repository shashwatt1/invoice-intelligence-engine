"""
Upload Endpoints — app/api/v1/upload.py

Implements POST /api/v1/upload — the entry point for the invoice pipeline.

This endpoint:
    1. Accepts a multipart file upload.
    2. Delegates validation and storage to UploadService.
    3. Returns the document UUID and file metadata.

NOT implemented yet (Sprint 2+):
    - Database persistence of invoice records
    - Duplicate hash checking against DB
    - OCR / AI processing

Design decisions:
- Returns 202 Accepted (not 201 Created) because the document is ingested
  but not yet fully processed. Processing happens in subsequent steps.
- StorageService and UploadService are instantiated per-request via
  FastAPI's dependency injection, which keeps tests clean and deterministic.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.schemas.base import APIResponse
from app.schemas.upload import UploadResponse
from app.services.storage_service import get_storage_service
from app.services.upload_service import UploadService

logger = get_logger(__name__)
router = APIRouter(tags=["Upload"])


def get_upload_service() -> UploadService:
    """
    FastAPI dependency that builds the UploadService with its dependencies.

    This factory is the composition root for the upload pipeline.
    In tests, override this dependency to inject mock services.
    """
    storage = get_storage_service()
    return UploadService(storage=storage)


@router.post(
    "/upload",
    response_model=APIResponse[UploadResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload an invoice document",
    description=(
        "Upload a PDF, PNG, or JPEG invoice file for processing. "
        "The file is validated, hashed, and stored. "
        "Returns a `document_uuid` that uniquely identifies this document "
        "in the processing pipeline. "
        "\n\n**Accepted formats:** PDF, PNG, JPEG"
        "\n\n**Maximum file size:** 25 MB"
    ),
    responses={
        202: {"description": "File accepted and queued for processing."},
        409: {"description": "Duplicate document — this file has already been uploaded."},
        413: {"description": "File exceeds the 25 MB size limit."},
        415: {"description": "Unsupported file format."},
        422: {"description": "File is empty or request is malformed."},
    },
)
async def upload_invoice(
    file: UploadFile = File(
        ...,
        description="Invoice file to upload. Accepted: PDF, PNG, JPEG.",
    ),
    service: UploadService = Depends(get_upload_service),
) -> JSONResponse:
    """
    Accept an invoice file upload and persist it to storage.

    The document enters the pipeline with status `INGESTED`.
    Use the returned `document_uuid` to poll processing status.

    **Note:** OCR and AI structuring are not triggered by this endpoint.
    They will be triggered by POST /process (Sprint 2).
    """
    result = await service.handle_upload(file=file)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=APIResponse(
            data=UploadResponse(
                document_uuid=result.document_uuid,
                filename=result.filename,
                file_size_bytes=result.file_size_bytes,
                mime_type=result.mime_type,
                file_path=result.file_path,
                status=result.status,
                created_at=result.created_at,
            )
        ).model_dump(mode="json"),
    )
