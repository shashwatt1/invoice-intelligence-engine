"""
Document Endpoints — app/api/v1/documents.py

    GET /documents/{id}   Live processing status + stage timeline

This is the polling target returned by POST /invoices/process. Because
the pipeline commits every status transition, each poll observes real
progress: status, the stages completed so far, the invoice id once
persistence finishes, and the failure payload if a stage failed.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.mappers import to_stage_entry
from app.core.exceptions import RecordNotFoundError
from app.database.session import get_db
from app.models.document import DocumentStatus
from app.models.processing_log import LogStatus
from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.schemas.base import APIResponse
from app.schemas.processing import DocumentStatusData

router = APIRouter(tags=["Documents"])

TERMINAL_STATUSES = {
    DocumentStatus.COMPLETED,
    DocumentStatus.REVIEW_REQUIRED,
    DocumentStatus.FAILED,
}


@router.get(
    "/documents/{document_id}",
    response_model=APIResponse[DocumentStatusData],
    summary="Live document processing status",
    description=(
        "Current lifecycle status plus the stage-by-stage processing log. "
        "Poll until `is_terminal` is true; `invoice_id` appears once the "
        "invoice is persisted, `error` when a stage failed."
    ),
)
async def get_document_status(
    document_id: uuid.UUID,
    include_payloads: bool = Query(
        default=False, description="Include full stage payloads (developer use)."
    ),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[DocumentStatusData]:
    document = await DocumentRepository(db).get(document_id)
    if document is None:
        raise RecordNotFoundError(
            message="Document not found.", detail={"document_id": str(document_id)}
        )

    logs = await ProcessingLogRepository(db).for_document(document_id)
    invoice = await InvoiceRepository(db).get_by_document(document_id)
    failure = next((log for log in logs if log.status == LogStatus.FAILURE), None)

    data = DocumentStatusData(
        document_id=document.id,
        filename=document.filename,
        status=document.status,
        is_terminal=document.status in TERMINAL_STATUSES,
        source_type=document.source_type,
        invoice_id=invoice.id if invoice else None,
        error=(
            {"stage": failure.stage, "message": failure.message, **(failure.payload or {})}
            if failure
            else None
        ),
        stages=[to_stage_entry(log, include_payload=include_payloads) for log in logs],
        created_at=document.created_at,
        updated_at=document.updated_at,
    )
    return APIResponse(data=data)
