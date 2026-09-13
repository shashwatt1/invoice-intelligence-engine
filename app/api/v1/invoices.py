"""
Invoice Endpoints — app/api/v1/invoices.py

    POST /invoices/process   Upload + intake, run pipeline in background (202)
    GET  /invoices           Processing history (search / filter / sort / page)
    GET  /invoices/{id}      Full invoice detail (one request feeds the whole view)

Design decisions:
- POST returns 202 with a status URL instead of blocking: Milestone D's
  per-stage status commits exist precisely so clients can watch progress
  live. The remaining stages run on a background task with its own
  session; Phase 2 swaps this for a real queue without changing the
  API contract.
- The pipeline is a module singleton behind get_pipeline() so tests can
  override it with fakes via FastAPI dependency_overrides.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.mappers import to_history_row
from app.api.v1.upload import get_upload_service
from app.core.config import get_settings
from app.core.exceptions import (
    InvoiceBaseException,
    RecordNotFoundError,
    StorageError,
    ValidationError,
)
from app.core.logging import get_logger
from app.database.session import get_db, get_session_factory
from app.models.document import DocumentStatus
from app.models.processing_log import PipelineStage
from app.models.product_case_mapping import MAX_UNITS_PER_CASE, MIN_UNITS_PER_CASE
from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.processing import (
    CaseMappingRequest,
    CaseMappingResult,
    CaseMappingRow,
    CorrectedLineItem,
    DatabaseConfirmation,
    HistoryRow,
    InvoiceDeleteResult,
    InvoiceDetailData,
    LineItemCorrection,
    LineItemCorrectionResult,
    LineItemData,
    ProcessAccepted,
    VendorData,
)
from app.services.case_mapping_service import (
    build_case_mapping_status,
    invoice_units_by_item_code,
)
from app.services.export_service import normalize_item_code, pdi_export_eligibility
from app.services.pipeline_service import InvoiceProcessingPipeline
from app.services.proposal_service import pending_by_item_code, propose_case_mapping
from app.services.revalidation_service import revalidate_invoice
from app.services.storage_service import get_storage_service
from app.services.store_reference_service import match_invoice_against_reference
from app.services.upload_service import UploadService

logger = get_logger(__name__)
router = APIRouter(tags=["Invoices"])

_pipeline: InvoiceProcessingPipeline | None = None


def get_pipeline() -> InvoiceProcessingPipeline:
    """Module-singleton pipeline; override in tests via dependency_overrides."""
    global _pipeline
    if _pipeline is None:
        _pipeline = InvoiceProcessingPipeline()
    return _pipeline


async def _run_pipeline_background(
    pipeline: InvoiceProcessingPipeline,
    document_id: uuid.UUID,
    file_content: bytes,
    mime_type: str,
    filename: str,
) -> None:
    """
    Execute the remaining pipeline stages after the 202 response.

    Uses its own session — the request session is closed by the time this
    runs. Stage failures are already persisted (document FAILED + failure
    log) by the pipeline, so they are only logged here, never re-raised.
    """
    factory = get_session_factory()
    async with factory() as session:
        document = await DocumentRepository(session).get(document_id)
        if document is None:  # pragma: no cover — intake committed the row
            logger.error("background_document_missing", document_id=str(document_id))
            return
        try:
            await pipeline.run_stages(
                session, document, file_content=file_content, mime_type=mime_type,
                filename=filename,
            )
        except InvoiceBaseException as exc:
            logger.warning(
                "background_pipeline_failed",
                document_id=str(document_id),
                error_code=exc.error_code,
            )
        except Exception:  # pragma: no cover — defensive: never kill the worker
            logger.exception("background_pipeline_crashed", document_id=str(document_id))


@router.post(
    "/invoices/process",
    response_model=APIResponse[ProcessAccepted],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload and process an invoice",
    description=(
        "Validates and stores the file, creates the document record, and runs "
        "OCR → AI structuring → validation → persistence in the background. "
        "Poll the returned `status_url` to follow each stage live."
        "\n\n**Accepted formats:** PDF, PNG, JPEG · **Max size:** 25 MB"
        "\n\n**409** if the same file (SHA-256) was already processed."
    ),
)
async def process_invoice(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Invoice file (PDF, PNG, or JPEG)."),
    db: AsyncSession = Depends(get_db),
    upload_service: UploadService = Depends(get_upload_service),
    pipeline: InvoiceProcessingPipeline = Depends(get_pipeline),
) -> APIResponse[ProcessAccepted]:
    upload = await upload_service.handle_upload(file)

    await file.seek(0)
    contents = await file.read()

    document = await pipeline.intake(
        db,
        filename=upload.filename,
        mime_type=upload.mime_type,
        file_size_bytes=upload.file_size_bytes,
        file_path=upload.file_path,
        file_hash=upload.file_hash,
    )
    background_tasks.add_task(
        _run_pipeline_background,
        pipeline,
        document.id,
        contents,
        upload.mime_type,
        upload.filename,
    )
    return APIResponse(
        data=ProcessAccepted(
            document_id=document.id,
            filename=upload.filename,
            status=document.status,
            status_url=f"/api/v1/documents/{document.id}",
        )
    )


@router.get(
    "/invoices",
    response_model=PaginatedResponse[HistoryRow],
    summary="Browse processing history",
    description=(
        "Documents with their extracted invoice (failed documents included). "
        "Supports search over filename / invoice number / vendor, status "
        "filtering, sorting, and pagination."
    ),
)
async def list_invoices(
    search: str | None = Query(default=None, max_length=200),
    document_status: DocumentStatus | None = Query(default=None, alias="status"),
    sort_by: str = Query(default="created_at"),
    descending: bool = Query(default=True),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[HistoryRow]:
    rows, total = await DocumentRepository(db).search_history(
        search=search,
        status=document_status,
        sort_by=sort_by,
        descending=descending,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[to_history_row(document, invoice) for document, invoice in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/invoices/{invoice_id}",
    response_model=APIResponse[InvoiceDetailData],
    summary="Full invoice detail",
    description=(
        "Header, vendor, line items, validation report, LLM call metadata, "
        "persistence confirmation, and developer-panel payloads (raw OCR "
        "text, raw structured output) — one request per detail view."
    ),
)
async def get_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[InvoiceDetailData]:
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(
            message="Invoice not found.", detail={"invoice_id": str(invoice_id)}
        )

    logs = await ProcessingLogRepository(db).for_document(invoice.document_id)
    payloads = {log.stage: log.payload for log in logs if log.payload}
    document = invoice.document
    units = await invoice_units_by_item_code(db, invoice)
    reference = await match_invoice_against_reference(
        db, invoice, get_settings().store_number
    )
    pdi_eligibility = pdi_export_eligibility(invoice, units)
    case_mappings = [
        CaseMappingRow(**vars(status))
        for status in build_case_mapping_status(invoice, units, reference)
    ]

    data = InvoiceDetailData(
        invoice_id=invoice.id,
        document_id=invoice.document_id,
        filename=document.filename,
        document_status=document.status,
        source_type=document.source_type,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        subtotal=float(invoice.subtotal) if invoice.subtotal is not None else None,
        tax_amount=float(invoice.tax_amount) if invoice.tax_amount is not None else None,
        discount_amount=float(invoice.discount_amount)
        if invoice.discount_amount is not None
        else None,
        grand_total=float(invoice.grand_total) if invoice.grand_total is not None else None,
        status=invoice.status,
        composite_confidence=float(invoice.composite_confidence)
        if invoice.composite_confidence is not None
        else None,
        extraction_model=invoice.extraction_model,
        created_at=invoice.created_at,
        pdi_export_allowed=pdi_eligibility.allowed,
        pdi_export_requires_confirmation=pdi_eligibility.requires_confirmation,
        pdi_export_blocked_reason=pdi_eligibility.blocked_reason,
        case_mappings=case_mappings,
        vendor=VendorData.model_validate(invoice.vendor, from_attributes=True)
        if invoice.vendor
        else None,
        line_items=[
            LineItemData(
                description=item.description,
                quantity=float(item.quantity),
                unit_price=float(item.unit_price) if item.unit_price is not None else None,
                line_total=float(item.line_total) if item.line_total is not None else None,
                tax_rate=float(item.tax_rate) if item.tax_rate is not None else None,
                unit_deposit=float(item.deposit) if item.deposit is not None else None,
                sort_order=item.sort_order,
                corrected_fields=item.corrected_fields or [],
            )
            for item in sorted(invoice.items, key=lambda i: i.sort_order)
        ],
        validation_report=payloads.get(PipelineStage.VALIDATION),
        llm_metadata=payloads.get(PipelineStage.AI_STRUCTURING),
        database=DatabaseConfirmation(
            vendor_saved=invoice.vendor_id is not None,
            invoice_saved=True,
            items_saved=len(invoice.items),
            logs_saved=len(logs),
            duplicate_check_passed=True,  # a persisted invoice implies the hash was unique
            processing_duration_ms=sum(log.duration_ms or 0 for log in logs),
        ),
        ocr_text=document.raw_ocr_text,
        raw_extraction=invoice.raw_extraction_json,
    )
    return APIResponse(data=data)


@router.delete(
    "/invoices/{invoice_id}",
    response_model=APIResponse[InvoiceDeleteResult],
    summary="Permanently delete an invoice",
    description=(
        "Deletes the underlying document, which cascades (via existing "
        "database foreign keys) to the invoice, its line items, and its "
        "processing logs — no partial state, no orphaned rows. Also "
        "removes the stored source file on a best-effort basis. This is a "
        "hard delete with no undo; intended for the development workflow "
        "of reprocessing the same invoice while refining extraction."
    ),
    responses={404: {"description": "Invoice not found"}},
)
async def delete_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[InvoiceDeleteResult]:
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(
            message="Invoice not found.", detail={"invoice_id": str(invoice_id)}
        )

    document = invoice.document
    document_id = document.id
    file_path = document.file_path

    # Deleting the document cascades to the invoice, its items, and its
    # processing logs at the database level — nothing else to clean up
    # manually. Commit explicitly so the deletion is durable before the
    # (separate, best-effort) physical file removal below.
    await DocumentRepository(db).delete(document)
    await db.commit()

    try:
        await get_storage_service().delete(file_path)
    except StorageError as exc:
        # The database is already correctly cleaned up — a leftover file
        # on disk is not user-visible and not worth failing the request
        # over, but it's worth knowing about.
        logger.warning(
            "invoice_delete_file_cleanup_failed",
            invoice_id=str(invoice_id),
            document_id=str(document_id),
            file_path=file_path,
            error=str(exc),
        )

    return APIResponse(
        data=InvoiceDeleteResult(invoice_id=invoice_id, document_id=document_id)
    )


@router.post(
    "/invoices/{invoice_id}/case-mappings",
    response_model=APIResponse[CaseMappingResult],
    summary="Confirm units-per-case for one or more products",
    description=(
        "Saves confirmed UPC → units-per-case mappings and returns the "
        "invoice's refreshed mapping state.\n\n"
        "Mappings are stored per product, not per invoice: once confirmed, "
        "the same UPC on any future invoice resolves automatically and is "
        "never asked about again. Re-confirming an existing product updates "
        "it in place rather than creating a duplicate.\n\n"
        "The PDI export stays blocked until every line item on the invoice "
        "has a mapping."
    ),
    responses={
        404: {"description": "Invoice not found"},
        422: {"description": "Invalid units_per_case or item code"},
    },
)
async def confirm_case_mappings(
    invoice_id: uuid.UUID,
    payload: CaseMappingRequest,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[CaseMappingResult]:
    """
    Submits the operator's values for review. Writes PROPOSALS only.

    This endpoint has no path to product_case_mappings. It once did, and
    values typed to unblock a download became indistinguishable from
    verified ones; that path is closed. A reviewer promotes a proposal
    through app.services.proposal_service.approve(), and only then does
    the value exist for the formatter.
    """
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(
            message="Invoice not found.", detail={"invoice_id": str(invoice_id)}
        )

    store = get_settings().store_number
    units = await invoice_units_by_item_code(db, invoice)
    reference = await match_invoice_against_reference(db, invoice, store)
    # The review rows tell the system how each value relates to what the
    # invoice offered, so the proposal's source is decided here, not by
    # the client.
    review_by_code = {
        status.item_code: status
        for status in build_case_mapping_status(invoice, units, reference)
        if status.item_code
    }

    submitted = 0
    for confirmation in payload.mappings:
        # Normalize on the way in so a value proposed from a dashed UPC is
        # keyed the same way the formatter and the mapping table key it.
        code = normalize_item_code(confirmation.item_code)
        if not code:
            raise ValidationError(
                message="Item code contains no usable digits.",
                detail={"item_code": confirmation.item_code},
            )
        if not MIN_UNITS_PER_CASE <= confirmation.units_per_case <= MAX_UNITS_PER_CASE:
            raise ValidationError(
                message=(
                    f"units_per_case must be between {MIN_UNITS_PER_CASE} and "
                    f"{MAX_UNITS_PER_CASE}."
                ),
                detail={"item_code": code},
            )
        await propose_case_mapping(
            db,
            store_number=store,
            invoice=invoice,
            item_code=code,
            units_per_case=confirmation.units_per_case,
            review_status=review_by_code.get(code),
            proposed_by="frontend:review-ui",
        )
        submitted += 1
    await db.commit()

    # Re-read: mappings are unchanged by design, but the queue is not.
    pending = await pending_by_item_code(db, store, list(review_by_code))
    eligibility = pdi_export_eligibility(invoice, units)
    return APIResponse(
        data=CaseMappingResult(
            saved=submitted,
            case_mappings=[
                CaseMappingRow(**vars(status))
                for status in build_case_mapping_status(invoice, units, reference, pending)
            ],
            pdi_export_allowed=eligibility.allowed,
            pdi_export_blocked_reason=eligibility.blocked_reason,
        )
    )


@router.patch(
    "/invoices/{invoice_id}/items/{sort_order}",
    response_model=APIResponse[LineItemCorrectionResult],
    summary="Correct a line item's transaction values",
    description=(
        "Replaces unit price, quantity and/or line total on one line item "
        "with figures a person read off the document, then re-runs "
        "validation and recomputes the PDI export gate.\n\n"
        "For the handful of values extraction cannot associate — OCR "
        "interleaves the description and price columns on some receipt "
        "layouts, and the model reports what it cannot place as null "
        "rather than guessing. No OCR or model call is made: only the "
        "deterministic checks run again, against the same rules the "
        "pipeline used.\n\n"
        "Corrected fields are recorded on the line, so a typed figure "
        "never continues to read as extracted data. Units-per-case is not "
        "editable here — it has its own confirmation endpoint and its own "
        "authority table."
    ),
    responses={
        404: {"description": "Invoice or line item not found"},
        422: {"description": "No fields given, or a negative value"},
    },
)
async def correct_line_item(
    invoice_id: uuid.UUID,
    sort_order: int,
    payload: LineItemCorrection,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[LineItemCorrectionResult]:
    updates = payload.updates()
    if not updates:
        raise ValidationError(
            message="Provide at least one of unit_price, quantity or line_total.",
            detail={"invoice_id": str(invoice_id), "sort_order": sort_order},
        )
    negative = sorted(field for field, value in updates.items() if value < 0)
    if negative:
        raise ValidationError(
            message=f"{', '.join(negative)} must not be negative.",
            detail={"invoice_id": str(invoice_id), "sort_order": sort_order,
                    "fields": negative},
        )

    repository = InvoiceRepository(db)
    if await repository.get(invoice_id) is None:
        raise RecordNotFoundError(
            message="Invoice not found.", detail={"invoice_id": str(invoice_id)}
        )

    item = await repository.correct_item(invoice_id, sort_order, updates)
    if item is None:
        raise RecordNotFoundError(
            message="Line item not found on this invoice.",
            detail={"invoice_id": str(invoice_id), "sort_order": sort_order},
        )

    invoice = await repository.get_detail(invoice_id)
    report = await revalidate_invoice(db, invoice)

    units = await invoice_units_by_item_code(db, invoice)
    eligibility = pdi_export_eligibility(invoice, units)
    await db.commit()

    return APIResponse(
        data=LineItemCorrectionResult(
            item=CorrectedLineItem(
                sort_order=item.sort_order,
                description=item.description,
                quantity=float(item.quantity),
                unit_price=float(item.unit_price) if item.unit_price is not None else None,
                line_total=float(item.line_total) if item.line_total is not None else None,
                unit_deposit=float(item.deposit) if item.deposit is not None else None,
                corrected_fields=item.corrected_fields or [],
            ),
            status=report.decision.value,
            composite_confidence=report.confidence.composite,
            failed_checks=len(report.failed_checks),
            review_reasons=report.review_reasons,
            pdi_export_allowed=eligibility.allowed,
            pdi_export_blocked_reason=eligibility.blocked_reason,
        )
    )
