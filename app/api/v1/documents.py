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
from dataclasses import asdict

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.invoices import get_pipeline
from app.api.v1.mappers import to_stage_entry
from app.core.dependencies import require_admin, require_authenticated_user, require_manager
from app.core.exceptions import InvoiceBaseException, RecordNotFoundError, ValidationError
from app.core.logging import get_logger
from app.database.session import get_db, get_session_factory
from app.models.document import DocumentStatus
from app.models.processing_log import LogStatus, PipelineStage
from app.models.user import User, UserRole
from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.store_repository import StoreRepository
from app.schemas.base import APIResponse
from app.schemas.processing import (
    DocumentPhoto,
    DocumentStatusData,
    ReprocessResultData,
    StoreCandidateOut,
    StoreConfirmation,
    StoreDeferral,
    StoreRef,
)
from app.services.document_lifecycle import (
    ensure_document_visible,
    move_document_to_bin,
    stop_document,
)
from app.services.pipeline_service import InvoiceProcessingPipeline
from app.services.reprocess_service import reprocess_document

logger = get_logger(__name__)

router = APIRouter(tags=["Documents"])

TERMINAL_STATUSES = {
    DocumentStatus.COMPLETED,
    DocumentStatus.REVIEW_REQUIRED,
    DocumentStatus.STOPPED,
    DocumentStatus.BINNED,
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
        default=False, description="Include full stage payloads (ADMIN only, regardless of this flag for anyone else)."
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> APIResponse[DocumentStatusData]:
    document = await DocumentRepository(db).get(document_id)
    if document is None:
        raise RecordNotFoundError(
            message="Document not found.", detail={"document_id": str(document_id)}
        )
    ensure_document_visible(document, user)

    logs = await ProcessingLogRepository(db).for_document(document_id)
    invoice = await InvoiceRepository(db).get_by_document(document_id)
    failure = next((log for log in logs if log.status == LogStatus.FAILURE), None)
    data = await _status_data(
        db, document, logs, invoice, failure, include_payloads and user.role == UserRole.ADMIN.value
    )
    return APIResponse(data=_redact_document_status(data, user.role))


@router.post(
    "/documents/{document_id}/stop",
    response_model=APIResponse[DocumentStatusData],
    summary="Cancel the current active processing attempt",
    description=(
        "Backend-authoritative cancellation. Only valid while the document has an active "
        "attempt in progress (not yet COMPLETED, REVIEW_REQUIRED, FAILED or already BINNED) — "
        "otherwise 422. Idempotent: calling this again on an already-STOPPED document returns "
        "200 with no change. If OCR/AI structuring is already in flight it may finish "
        "technically, but its result can never advance this document to a successful state or "
        "generate EDI once stopped — every pipeline stage boundary re-checks the live status. "
        "The source file, document row and full audit trail are preserved; nothing about the "
        "invoice, mappings or proposals is touched. USER may only stop a document they "
        "uploaded themselves (documents.uploaded_by_user_id); MANAGER and ADMIN may stop any "
        "document. The actor is the authenticated session — never a request field."
    ),
    responses={
        401: {"description": "Not authenticated"},
        404: {"description": "Document not found"},
        403: {"description": "USER role, but not this document's uploader"},
        422: {"description": "No active attempt to stop"},
    },
)
async def stop_document_endpoint(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> APIResponse[DocumentStatusData]:
    document = await DocumentRepository(db).get(document_id)
    if document is None:
        raise RecordNotFoundError(message="Document not found.", detail={"document_id": str(document_id)})
    await stop_document(db, document, user)
    await db.commit()
    await db.refresh(document)

    logs = await ProcessingLogRepository(db).for_document(document_id)
    invoice = await InvoiceRepository(db).get_by_document(document_id)
    return APIResponse(data=_redact_document_status(
        await _status_data(db, document, logs, invoice, None, False), user.role
    ))


@router.post(
    "/documents/{document_id}/move-to-bin",
    response_model=APIResponse[DocumentStatusData],
    summary="Move a document out of the active workflow (recoverable)",
    description=(
        "Backend-authoritative, recoverable discard. Removes the document from active "
        "processing/queues without physically deleting the source file, the document row, "
        "its invoice (if any), or any history — only documents.status changes, plus one audit "
        "entry. Never touches mappings, proposals, or generates EDI, so a COMPLETED invoice is "
        "never silently mutated by this call. Works from any status, including mid-pipeline "
        "(the same withdrawal check STOP relies on) and already-terminal ones. Idempotent: "
        "calling this again on an already-BINNED document returns 200 with no change. USER may "
        "only bin a document they uploaded themselves (documents.uploaded_by_user_id); MANAGER "
        "and ADMIN may bin any document. The actor is the authenticated session — never a "
        "request field. The frontend must confirm this action before calling it."
    ),
    responses={
        401: {"description": "Not authenticated"},
        404: {"description": "Document not found"},
        403: {"description": "USER role, but not this document's uploader"},
    },
)
async def move_document_to_bin_endpoint(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> APIResponse[DocumentStatusData]:
    document = await DocumentRepository(db).get(document_id)
    if document is None:
        raise RecordNotFoundError(message="Document not found.", detail={"document_id": str(document_id)})
    await move_document_to_bin(db, document, user)
    await db.commit()
    await db.refresh(document)

    logs = await ProcessingLogRepository(db).for_document(document_id)
    invoice = await InvoiceRepository(db).get_by_document(document_id)
    return APIResponse(data=_redact_document_status(
        await _status_data(db, document, logs, invoice, None, False), user.role
    ))


async def _status_data(db, document, logs, invoice, failure, include_payloads) -> DocumentStatusData:
    store_id = invoice.store_id if invoice else document.store_id
    store = await StoreRepository(db).get(store_id) if store_id else None
    photos = await DocumentRepository(db).pages(document)
    return DocumentStatusData(
        photos=[DocumentPhoto(
            page_number=p.page_number, filename=p.filename, mime_type=p.mime_type,
            file_size_bytes=p.file_size_bytes, source_type=p.source_type,
            mean_confidence=p.mean_confidence,
            text_chars=len(p.raw_ocr_text) if p.raw_ocr_text else None,
        ) for p in photos] if len(photos) > 1 else [],
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


def _redact_document_status(data: DocumentStatusData, role: str) -> DocumentStatusData:
    """
    ADMIN sees everything unchanged. MANAGER/USER never get a stage's raw
    payload (OCR text, LLM response, validation internals) regardless of
    what the request asked for — 'processing logs' are ADMIN-only per
    the role matrix. USER additionally gets no stage timeline at all
    (only 'basic processing status') and a simplified error, since a
    per-stage breakdown is the kind of technical diagnostic USER must
    not see.
    """
    if role == UserRole.ADMIN.value:
        return data
    stripped_stages = [s.model_copy(update={"payload": None}) for s in data.stages]
    if role == UserRole.USER.value:
        return data.model_copy(update={
            "stages": [],
            "error": {"message": data.error.get("message", "Processing failed.")} if data.error else None,
        })
    return data.model_copy(update={"stages": stripped_stages})


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
    user: User = Depends(require_manager),
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
    return APIResponse(data=_redact_document_status(
        await _status_data(db, document, logs, invoice, None, False), user.role
    ))


@router.post(
    "/documents/{document_id}/defer-store",
    response_model=APIResponse[DocumentStatusData],
    summary="Read the document now, assign its store later",
    description=(
        "Only for a document in `STORE_CONFIRMATION_REQUIRED`. The person says the store is "
        "not known (or its data is not ready) and processing continues WITHOUT one: "
        "structuring → validation → persistence run from the text already extracted and the "
        "invoice is stored STORE_PENDING. No reference data, case mapping, proposal or EDI "
        "is possible until POST /invoices/{id}/assign-store. No store is invented."
    ),
    responses={404: {"description": "Not found"}, 422: {"description": "Not waiting for a store"}},
)
async def defer_store(
    document_id: uuid.UUID,
    body: StoreDeferral,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    pipeline: InvoiceProcessingPipeline = Depends(get_pipeline),
    user: User = Depends(require_manager),
) -> APIResponse[DocumentStatusData]:
    document = await DocumentRepository(db).get(document_id)
    if document is None:
        raise RecordNotFoundError(message="Document not found.", detail={"document_id": str(document_id)})
    if document.status != DocumentStatus.STORE_CONFIRMATION_REQUIRED:
        raise ValidationError(
            message=f"Document is {document.status}, not waiting for a store.",
            detail={"document_id": str(document_id), "status": document.status},
        )
    document.store_id = None
    await DocumentRepository(db).set_status(document, DocumentStatus.OCR_COMPLETED)
    await ProcessingLogRepository(db).add(
        document_id=document.id,
        stage=PipelineStage.STORE_IDENTIFICATION,
        message=f"Store deferred by {body.deferred_by}: processing continues with no store (STORE_PENDING).",
        payload={"event": "store_deferred", "deferred_by": body.deferred_by, "note": body.note,
                 "candidates_offered": sorted({c.get("store_id") for c in (document.store_candidates or [])
                                               if c.get("store_id")})},
    )
    await db.commit()
    await db.refresh(document)
    background_tasks.add_task(_resume_background, pipeline, document.id, True)

    logs = await ProcessingLogRepository(db).for_document(document_id)
    invoice = await InvoiceRepository(db).get_by_document(document_id)
    return APIResponse(data=_redact_document_status(
        await _status_data(db, document, logs, invoice, None, False), user.role
    ))


async def _resume_background(
    pipeline: InvoiceProcessingPipeline, document_id: uuid.UUID, store_deferred: bool = False
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        document = await DocumentRepository(session).get(document_id)
        if document is None:  # pragma: no cover
            return
        try:
            await pipeline.resume_after_store_confirmation(session, document, store_deferred=store_deferred)
        except InvoiceBaseException as exc:
            logger.warning("background_resume_failed", document_id=str(document_id),
                           error_code=exc.error_code)
        except Exception:  # pragma: no cover — defensive: never kill the worker
            logger.exception("background_resume_crashed", document_id=str(document_id))


@router.post(
    "/documents/{document_id}/reprocess",
    response_model=APIResponse[ReprocessResultData],
    summary="Re-extract a stored document under the active prompt and model",
    description=(
        "Runs the stored source through OCR, structuring and validation again and replaces "
        "the invoice's extraction result in place. The document id, the invoice id, the "
        "source file and its hash are unchanged, and every historical processing log is "
        "kept — the attempt stamps its own entries with a run id and an attempt number so "
        "the runs stay distinguishable.\n\n"
        "Extraction runs in full before anything is written, so a failure leaves the "
        "previous result intact. Line items are replaced outright, so a row the new "
        "extraction does not contain does not linger. The prompt and model are the "
        "application's active configuration; they are not selectable here.\n\n"
        "Refused for an invoice carrying manual corrections: those were made against "
        "figures this would replace. Master data is untouched — no mapping is approved, no "
        "proposal is altered — and no EDI is generated; export readiness is reported by the "
        "normal gate and acted on separately."
    ),
    responses={404: {"description": "Document not found"},
               422: {"description": "No invoice to replace, or the invoice has manual corrections"}},
)
async def reprocess_document_endpoint(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    pipeline: InvoiceProcessingPipeline = Depends(get_pipeline),
    user: User = Depends(require_admin),
) -> APIResponse[ReprocessResultData]:
    document = await DocumentRepository(db).get(document_id)
    if document is None:
        raise RecordNotFoundError(message="Document not found.", detail={"document_id": str(document_id)})
    result = await reprocess_document(db, document, pipeline=pipeline)
    return APIResponse(data=ReprocessResultData(**asdict(result)))
