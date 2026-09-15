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

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.invoices import get_pipeline
from app.api.v1.mappers import to_stage_entry
from app.core.exceptions import InvoiceBaseException, RecordNotFoundError, ValidationError
from app.core.logging import get_logger
from app.database.session import get_db, get_session_factory
from app.models.document import DocumentStatus
from app.models.processing_log import LogStatus, PipelineStage
from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.store_repository import StoreRepository
from app.schemas.base import APIResponse
from app.schemas.processing import (
    DocumentStatusData,
    StoreCandidateOut,
    StoreConfirmation,
    StoreRef,
)
from app.services.pipeline_service import InvoiceProcessingPipeline

logger = get_logger(__name__)

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
    return APIResponse(data=await _status_data(db, document, logs, invoice, failure, include_payloads))


async def _status_data(db, document, logs, invoice, failure, include_payloads) -> DocumentStatusData:
    store_id = invoice.store_id if invoice else document.store_id
    store = await StoreRepository(db).get(store_id) if store_id else None
    return DocumentStatusData(
        document_id=document.id,
        filename=document.filename,
        status=document.status,
        is_terminal=document.status in TERMINAL_STATUSES,
        source_type=document.source_type,
        store=StoreRef.from_store(store) if store else None,
        awaiting_store_confirmation=document.status == DocumentStatus.STORE_CONFIRMATION_REQUIRED,
        store_candidates=[StoreCandidateOut(**c) for c in (document.store_candidates or [])],
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


@router.post(
    "/documents/{document_id}/confirm-store",
    response_model=APIResponse[DocumentStatusData],
    summary="Confirm which store a paused upload is for, and finish processing it",
    description=(
        "Only for a document in `STORE_CONFIRMATION_REQUIRED`. The person names the store "
        "(one of the candidates, or any store from the directory); it is recorded on the "
        "document with who confirmed it, and structuring → validation → persistence run in "
        "the background from the text already extracted. The pipeline never picks a store "
        "on its own; this call is the decision."
    ),
    responses={404: {"description": "Not found"}, 422: {"description": "Not waiting, or unknown store"}},
)
async def confirm_store(
    document_id: uuid.UUID,
    body: StoreConfirmation,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    pipeline: InvoiceProcessingPipeline = Depends(get_pipeline),
) -> APIResponse[DocumentStatusData]:
    document = await DocumentRepository(db).get(document_id)
    if document is None:
        raise RecordNotFoundError(message="Document not found.", detail={"document_id": str(document_id)})
    if document.status != DocumentStatus.STORE_CONFIRMATION_REQUIRED:
        raise ValidationError(
            message=f"Document is {document.status}, not waiting for a store.",
            detail={"document_id": str(document_id), "status": document.status},
        )
    store = await StoreRepository(db).get(body.store_id)
    if store is None:
        raise ValidationError(message="store_id is not a known store. Choose one from the directory.",
                              detail={"field": "store_id", "reason": "unknown", "value": str(body.store_id)})

    candidates = {c.get("store_id") for c in (document.store_candidates or [])}
    document.store_id = store.id
    await DocumentRepository(db).set_status(document, DocumentStatus.OCR_COMPLETED)
    await ProcessingLogRepository(db).add(
        document_id=document.id,
        stage=PipelineStage.STORE_IDENTIFICATION,
        message=f"Store confirmed by {body.confirmed_by or 'operator'}: {store.label}.",
        payload={
            "store_id": str(store.id), "store_label": store.label,
            "confirmed_by": body.confirmed_by,
            "was_a_candidate": str(store.id) in candidates,
            "candidates_offered": sorted(candidates),
        },
    )
    await db.commit()
    await db.refresh(document)
    background_tasks.add_task(_resume_background, pipeline, document.id)

    logs = await ProcessingLogRepository(db).for_document(document_id)
    invoice = await InvoiceRepository(db).get_by_document(document_id)
    return APIResponse(data=await _status_data(db, document, logs, invoice, None, False))


async def _resume_background(pipeline: InvoiceProcessingPipeline, document_id: uuid.UUID) -> None:
    factory = get_session_factory()
    async with factory() as session:
        document = await DocumentRepository(session).get(document_id)
        if document is None:  # pragma: no cover
            return
        try:
            await pipeline.resume_after_store_confirmation(session, document)
        except InvoiceBaseException as exc:
            logger.warning("background_resume_failed", document_id=str(document_id),
                           error_code=exc.error_code)
        except Exception:  # pragma: no cover — defensive: never kill the worker
            logger.exception("background_resume_crashed", document_id=str(document_id))
