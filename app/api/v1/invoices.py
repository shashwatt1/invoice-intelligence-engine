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
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.mappers import to_history_row
from app.api.v1.upload import get_upload_service
from app.core.dependencies import require_admin, require_authenticated_user, require_manager
from app.core.exceptions import (
    DatabaseError,
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
from app.models.user import User, UserRole
from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.repositories.store_repository import StoreRepository
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.processing import (
    CaseMappingRequest,
    CaseMappingResult,
    CaseMappingRow,
    CorrectedLineItem,
    DatabaseConfirmation,
    DocumentPhoto,
    DuplicateDecision,
    DuplicateDecisionResult,
    HistoryRow,
    InvoiceDateCorrection,
    InvoiceDateCorrectionResult,
    InvoiceDeleteResult,
    InvoiceDetailData,
    InvoiceReviewSummary,
    InvoiceTotalsCorrection,
    InvoiceTotalsCorrectionResult,
    LineItemCorrection,
    LineItemCorrectionResult,
    LineItemCreate,
    LineItemData,
    LineItemVoid,
    ProcessAccepted,
    StoreAssignment,
    StoreRef,
    VendorData,
)
from app.services.case_mapping_service import (
    build_case_mapping_status,
    invoice_commercial_resolution,
    invoice_units_by_item_code,
)
from app.services.document_lifecycle import ensure_document_visible
from app.services.export_service import (
    normalize_item_code,
    persisted_pdi_export_eligibility,
    unmapped_item_codes,
)
from app.services.pipeline_service import InvoiceProcessingPipeline, PageUpload
from app.services.product_master.commercial_resolution import line_product_names, resolution_fields
from app.services.proposal_service import pending_by_item_code, propose_case_mapping
from app.services.revalidation_service import revalidate_invoice
from app.services.storage_service import get_storage_service
from app.services.store_guard import ensure_physical_store, require_store_choice
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
    pages: list[PageUpload],
    store_id: uuid.UUID | None,
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
            await pipeline.run_stages_pages(session, document, pages, store_id=store_id)
        except InvoiceBaseException as exc:
            logger.warning(
                "background_pipeline_failed",
                document_id=str(document_id),
                error_code=exc.error_code,
            )
        except Exception:  # pragma: no cover — defensive: never kill the worker
            logger.exception("background_pipeline_crashed", document_id=str(document_id))


async def _discard_stored_uploads(pages: list[PageUpload], reason: str) -> None:
    """
    Delete the objects THIS request just stored, after intake refused them.

    Safe to call for any intake failure: handle_upload() mints a fresh
    document_uuid per file, so these keys belong to this request alone and
    can never be an existing document's object — and intake raises before
    it creates any row, so nothing references them. A duplicate therefore
    loses only the copy just uploaded; the original document keeps its file.

    Cleanup failure is logged, never raised: it must not replace the error
    that triggered it (the operator needs "duplicate", not "cleanup failed").
    """
    try:
        storage = get_storage_service()
    except Exception:
        logger.warning("upload_cleanup_unavailable", reason=reason, file_count=len(pages))
        return
    for page in pages:
        try:
            await storage.delete(page.file_path)
            logger.info("upload_discarded", file_path=page.file_path, reason=reason)
        except Exception:
            logger.warning("upload_cleanup_failed", file_path=page.file_path, reason=reason)


async def resolve_chosen_store(db: AsyncSession, value: str | None) -> uuid.UUID | None:
    """
    The store the operator chose up front, if any — as a Store id that
    exists. A blank value means "not chosen yet" (identification will
    ask); a value that is not a known store is refused outright. There
    is no configured store to fall back on.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        store_id = uuid.UUID(text)
    except ValueError as exc:
        raise ValidationError(
            message=f"store_id {value!r} is not a store id. Choose a store from the directory.",
            detail={"field": "store_id", "reason": "invalid", "value": value},
        ) from exc
    store = await StoreRepository(db).get(store_id)
    if store is None:
        raise ValidationError(
            message=f"store_id {value!r} is not a known store. Choose a store from the directory.",
            detail={"field": "store_id", "reason": "unknown", "value": value},
        )
    ensure_physical_store(store)
    return store.id


@router.post(
    "/invoices/process",
    response_model=APIResponse[ProcessAccepted],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload and process an invoice — one file, or several photos of one invoice",
    description=(
        "Validates and stores the file(s), creates the document record, and runs "
        "OCR → AI structuring → validation → persistence in the background. "
        "Poll the returned `status_url` to follow each stage live."
        "\n\n**One invoice, many photos:** send several `files` (in top-to-bottom order) "
        "when a long invoice was photographed in overlapping pieces. Each photo is OCR'd "
        "on its own; the texts are combined into one photo-separated context and ONE "
        "invoice is extracted from it, each row recording which photo(s) it was read from. "
        "`file` (single) is still accepted."
        "\n\n**Accepted formats:** PDF, PNG, JPEG · **Max size:** 25 MB per file"
        "\n\n**409** if the same file (SHA-256), the same set of photos, or a photo already "
        "used in another intake was already processed."
        "\n\n`store_id` (optional) is the store the operator says the invoice is for. After "
        "text extraction the document is matched against the store master; the run pauses in "
        "`STORE_CONFIRMATION_REQUIRED` for a person to confirm unless the operator's choice "
        "agrees with what the document says. There is no default store."
    ),
)
async def process_invoice(
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None, description="One invoice file (PDF, PNG, or JPEG)."),
    files: list[UploadFile] = File(
        default=[], description="Several photos of ONE invoice, in top-to-bottom order.",
    ),
    store_id: str | None = Form(
        default=None,
        description="The Store id the operator chose, if chosen up front. Never a default.",
    ),
    db: AsyncSession = Depends(get_db),
    upload_service: UploadService = Depends(get_upload_service),
    pipeline: InvoiceProcessingPipeline = Depends(get_pipeline),
    user: User = Depends(require_authenticated_user),
) -> APIResponse[ProcessAccepted]:
    uploads = ([file] if file is not None else []) + list(files)
    if not uploads:
        raise ValidationError(message="Send one `file`, or one or more `files` (photos of one invoice).")
    require_store_choice(user.role, store_id)
    # Checked before any file is touched: a store that does not exist is
    # refused outright — nothing is stored, no document row is made.
    chosen = await resolve_chosen_store(db, store_id)

    pages: list[PageUpload] = []
    for upload_file in uploads:
        upload = await upload_service.handle_upload(upload_file)
        await upload_file.seek(0)
        contents = await upload_file.read()
        pages.append(PageUpload(
            filename=upload.filename, mime_type=upload.mime_type,
            file_size_bytes=upload.file_size_bytes, file_path=upload.file_path,
            file_hash=upload.file_hash, content=contents,
        ))

    try:
        document = await pipeline.intake_pages(db, pages, store_id=chosen, uploaded_by_user_id=user.id)
    except InvoiceBaseException:
        # A safe, typed error (duplicate etc.) — re-raised unchanged. The files
        # were stored before intake could check, so this request's copies are
        # orphans the moment it refuses; the existing document keeps its own.
        await _discard_stored_uploads(pages, reason="intake_rejected")
        raise
    except Exception as exc:
        # Intake is the one synchronous stage: a failure here (the schema, the
        # database, the disk) must reach the operator as "nothing was
        # processed, try again" with a reference, never as a bare 500. The
        # traceback goes to the log under the same request id.
        await db.rollback()
        logger.exception("intake_failed", filenames=[p.filename for p in pages])
        await _discard_stored_uploads(pages, reason="intake_failed")
        raise DatabaseError(
            message=(
                "Invoice processing failed while recording the upload. Nothing was "
                "processed; the files can be uploaded again."
            ),
            detail={"stage": "intake", "photo_count": len(pages)},
        ) from exc
    background_tasks.add_task(_run_pipeline_background, pipeline, document.id, pages, chosen)
    return APIResponse(
        data=ProcessAccepted(
            document_id=document.id,
            filename=document.filename,
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
    user: User = Depends(require_authenticated_user),
) -> PaginatedResponse[HistoryRow]:
    own_only = user.id if user.role == UserRole.USER.value else None
    rows, total = await DocumentRepository(db).search_history(
        search=search,
        status=document_status,
        sort_by=sort_by,
        descending=descending,
        page=page,
        page_size=page_size,
        uploaded_by_user_id=own_only,
    )
    stores = await StoreRepository(db).labels(
        [i.store_id if i else d.store_id for d, i in rows])
    reviews = await ProductDataProposalRepository(db).by_invoice_ids(
        [i.id for _, i in rows if i is not None])
    photo_counts = await DocumentRepository(db).page_counts([d.id for d, _ in rows])
    # Mapping/EDI status is a MANAGER+ business surface (see PART 11 of the
    # Requires Mapping phase) — computed only for the rows on this page
    # (bounded by page_size), reusing the same gate functions the invoice
    # detail and export endpoints use, so the list can never disagree with them.
    mapping_and_edi: dict[uuid.UUID, tuple[int, str]] = {}
    if user.role != UserRole.USER.value:
        for _, invoice in rows:
            if invoice is None or invoice.store_id is None:
                continue
            units = await invoice_units_by_item_code(db, invoice)
            eligibility = persisted_pdi_export_eligibility(invoice, units)
            edi_status = (
                "blocked" if not eligibility.allowed
                else "needs_confirmation" if eligibility.requires_confirmation
                else "ready"
            )
            mapping_and_edi[invoice.id] = (len(unmapped_item_codes(invoice, units)), edi_status)
    return PaginatedResponse(
        items=[_redact_history_row(to_history_row(
                              document, invoice,
                              stores.get(invoice.store_id if invoice else document.store_id),
                              review=InvoiceReviewSummary.from_proposals(reviews.get(invoice.id, []))
                              if invoice else None,
                              photo_count=photo_counts.get(document.id, 1),
                              mapping_required=mapping_and_edi.get(invoice.id, (None, None))[0]
                              if invoice else None,
                              edi_status=mapping_and_edi.get(invoice.id, (None, None))[1]
                              if invoice else None), user.role)
               for document, invoice in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def _redact_history_row(row: HistoryRow, role: str) -> HistoryRow:
    """USER gets its 'basic processing status' shape: no confidence
    internals, no master-data review state (that is MANAGER's mapping
    evidence, not a USER concern)."""
    if role != UserRole.USER.value:
        return row
    return row.model_copy(update={"composite_confidence": None, "review": None, "source_type": None})


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
    user: User = Depends(require_authenticated_user),
) -> APIResponse[InvoiceDetailData]:
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(
            message="Invoice not found.", detail={"invoice_id": str(invoice_id)}
        )
    ensure_document_visible(invoice.document, user)

    logs = await ProcessingLogRepository(db).for_document(invoice.document_id)
    payloads = {log.stage: log.payload for log in logs if log.payload}
    document = invoice.document
    store = invoice.store_id
    pdi_eligibility_units: dict = {}
    resolution = None
    if store is None:
        # STORE_PENDING: the invoice is readable and correctable, but nothing
        # store-scoped is consulted or shown — no reference data, no mapping
        # rows, no proposals. The gate says why.
        case_mappings: list[CaseMappingRow] = []
        proposals = []
    else:
        resolution = await invoice_commercial_resolution(db, invoice)
        units = resolution.units
        pdi_eligibility_units = units
        reference = await match_invoice_against_reference(db, invoice)
        # Queued-but-unreviewed values, so the operator sees what is already
        # awaiting approval rather than submitting it again.
        codes = [
            code for code in (normalize_item_code(i.product_sku) for i in invoice.items) if code
        ]
        pending = await pending_by_item_code(db, store, codes)
        case_mappings = [
            CaseMappingRow(**vars(status), **resolution_fields(resolution, status.item_code))
            for status in build_case_mapping_status(invoice, units, reference, pending)
        ]
        proposals = await ProductDataProposalRepository(db).list(invoice_id=invoice.id, store_id=store)
    pdi_eligibility = persisted_pdi_export_eligibility(invoice, pdi_eligibility_units)
    photos = await DocumentRepository(db).pages(document)
    names = await line_product_names(db, invoice, resolution)

    data = InvoiceDetailData(
        invoice_id=invoice.id,
        document_id=invoice.document_id,
        filename=document.filename,
        document_status=document.status,
        source_type=document.source_type,
        store=StoreRef.from_store(await StoreRepository(db).get(store)) if store else None,
        store_pending=store is None,
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
        deposit_total=float(invoice.deposit_total) if invoice.deposit_total is not None else None,
        fuel_surcharge=float(invoice.fuel_surcharge) if invoice.fuel_surcharge is not None else None,
        corrected_fields=invoice.corrected_fields or [],
        correction_history=invoice.correction_history or [],
        status=invoice.status,
        composite_confidence=float(invoice.composite_confidence)
        if invoice.composite_confidence is not None
        else None,
        extraction_model=invoice.extraction_model,
        created_at=invoice.created_at,
        pdi_export_allowed=pdi_eligibility.allowed,
        pdi_export_requires_confirmation=pdi_eligibility.requires_confirmation,
        pdi_export_blocked_reason=pdi_eligibility.blocked_reason,
        photos=[DocumentPhoto(
            page_number=p.page_number, filename=p.filename, mime_type=p.mime_type,
            file_size_bytes=p.file_size_bytes, source_type=p.source_type,
            mean_confidence=p.mean_confidence,
            text_chars=len(p.raw_ocr_text) if p.raw_ocr_text else None,
        ) for p in photos] if len(photos) > 1 else [],
        duplicate_review_required=any(
            item.duplicate_candidate and not item.duplicate_candidate.get("resolution")
            for item in invoice.items
        ),
        review=InvoiceReviewSummary.from_proposals(proposals),
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
                line_type=item.line_type,
                sort_order=item.sort_order,
                product_code=item.product_sku,
                **names.get(item.sort_order, {}),
                unit_discount=float(item.discount) if item.discount is not None else None,
                entry_source=item.entry_source or "extracted",
                correction_history=item.correction_history or [],
                source_pages=item.source_pages or [],
                duplicate_candidate=item.duplicate_candidate,
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
    return APIResponse(data=_redact_invoice_detail(data, user.role))


def _redact_invoice_detail(data: InvoiceDetailData, role: str) -> InvoiceDetailData:
    """
    ADMIN sees the full technical model. MANAGER keeps every
    business/review field (vendor, printed totals, case mappings,
    mapping evidence, correction history, line items) but loses developer
    diagnostics: raw OCR text, raw LLM extraction, LLM call metadata, the
    raw validation report, the extraction model name, persistence
    internals, and the composite extraction-confidence score.

    USER is a data-team operational role, not merely upload-only: it
    keeps the same business fields MANAGER does (vendor, printed totals,
    line_items — UPC/product identity, description, quantity, price —
    and case_mappings) so it can read the invoice it is working on and
    submit a proposal via POST /invoices/{id}/case-mappings. It still
    loses every developer/technical field, confidence internals,
    correction history, and the invoice-level `review` summary (that's
    proposal governance detail — MANAGER's; a USER checks its OWN
    proposal status through GET /proposals, filtered to what it
    submitted, not through this endpoint).
    """
    if role == UserRole.ADMIN.value:
        return data
    technical_fields = {
        "ocr_text": None, "raw_extraction": None, "llm_metadata": None,
        "validation_report": None, "database": None, "extraction_model": None,
        "composite_confidence": None,
    }
    if role == UserRole.MANAGER.value:
        return data.model_copy(update=technical_fields)
    return data.model_copy(update={
        **technical_fields,
        "corrected_fields": [], "correction_history": [],
        "photos": [], "duplicate_review_required": False,
        "review": InvoiceReviewSummary(status="NONE"),
        # Kept for USER: vendor, printed totals, case_mappings (mapping
        # status) and line_items (UPC/product identity, description,
        # quantity, price) — the business/operational view a data-team
        # USER needs to read the invoice and submit a mapping proposal.
    })


@router.delete(
    "/invoices/{invoice_id}",
    response_model=APIResponse[InvoiceDeleteResult],
    summary="Permanently delete an invoice",
    description=(
        "Deletes the underlying document, which cascades (via existing "
        "database foreign keys) to the invoice, its line items, its photos "
        "and its processing logs — no partial state. Also removes the stored "
        "source file(s) on a best-effort basis. This is a hard delete with no "
        "undo; intended for the development workflow of reprocessing the same "
        "invoice while refining extraction."
        "\n\nWhat deliberately stays: the invoice's proposal rows (immutable "
        "review history — Data Review shows them with `invoice_deleted`) and any "
        "master-data mapping an approval wrote. Operational data goes; audit "
        "history and master data do not."
    ),
    responses={404: {"description": "Invoice not found"}},
)
async def delete_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> APIResponse[InvoiceDeleteResult]:
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(
            message="Invoice not found.", detail={"invoice_id": str(invoice_id)}
        )

    document = invoice.document
    document_id = document.id
    file_paths = [document.file_path] + [
        p.file_path for p in await DocumentRepository(db).pages(document)
        if p.file_path != document.file_path
    ]

    # Deleting the document cascades to the invoice, its items, and its
    # processing logs at the database level — nothing else to clean up
    # manually. Commit explicitly so the deletion is durable before the
    # (separate, best-effort) physical file removal below.
    await DocumentRepository(db).delete(document)
    await db.commit()

    for file_path in file_paths:
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
    user: User = Depends(require_authenticated_user),
) -> APIResponse[CaseMappingResult]:
    """
    Submits the operator's values for review. Writes PROPOSALS only.

    This endpoint has no path to product_case_mappings. It once did, and
    values typed to unblock a download became indistinguishable from
    verified ones; that path is closed. A reviewer promotes a proposal
    through app.services.proposal_service.approve(), and only then does
    the value exist for the formatter.

    Open to any authenticated role, including USER: submitting a
    proposal is data-entry, not a governance decision — approve/reject
    stay MANAGER+ (see app/api/v1/proposals.py). A USER may only propose
    against an invoice they can see (the same ownership rule as GET
    /invoices/{id}).
    """
    invoice = await InvoiceRepository(db).get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(
            message="Invoice not found.", detail={"invoice_id": str(invoice_id)}
        )
    ensure_document_visible(invoice.document, user)

    store = invoice.store_id
    if store is None:
        raise ValidationError(
            message="This invoice has no store yet. Assign its store before confirming case mappings — "
                    "a mapping belongs to a store.",
            detail={"invoice_id": str(invoice_id), "store_pending": True},
        )
    resolution = await invoice_commercial_resolution(db, invoice)
    units = resolution.units
    reference = await match_invoice_against_reference(db, invoice)
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
            store_id=store,
            invoice=invoice,
            item_code=code,
            units_per_case=confirmation.units_per_case,
            review_status=review_by_code.get(code),
            proposed_by=user.username,
        )
        submitted += 1
    await db.commit()

    # Re-read: mappings are unchanged by design, but the queue is not.
    pending = await pending_by_item_code(db, store, list(review_by_code))
    eligibility = persisted_pdi_export_eligibility(invoice, units)
    return APIResponse(
        data=CaseMappingResult(
            saved=submitted,
            case_mappings=[
                CaseMappingRow(**vars(status), **resolution_fields(resolution, status.item_code))
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
    user: User = Depends(require_manager),
) -> APIResponse[LineItemCorrectionResult]:
    updates = payload.updates()
    if not updates:
        raise ValidationError(
            message="Provide at least one field to correct (unit_price, quantity, line_total, "
                    "unit_deposit, unit_discount, description, product_code).",
            detail={"invoice_id": str(invoice_id), "sort_order": sort_order},
        )
    negative = sorted(field for field, value in updates.items()
                      if field in LineItemCorrection.NUMERIC and value < 0)
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

    item = await repository.correct_item(
        invoice_id, sort_order, updates, corrected_by=payload.corrected_by, note=payload.note,
    )
    if item is None:
        raise RecordNotFoundError(
            message="Line item not found on this invoice.",
            detail={"invoice_id": str(invoice_id), "sort_order": sort_order},
        )
    invoice = await repository.get_detail(invoice_id)
    await _log_correction(db, invoice, "line_item_corrected", payload.corrected_by, payload.note,
                          sort_order=sort_order, changes=(item.correction_history or [])[-len(updates):])
    report = await revalidate_invoice(db, invoice)

    units = await invoice_units_by_item_code(db, invoice)
    eligibility = persisted_pdi_export_eligibility(invoice, units)
    await db.commit()

    return APIResponse(
        data=LineItemCorrectionResult(
            item=_corrected_line(item),
            status=report.decision.value,
            composite_confidence=report.confidence.composite,
            failed_checks=len(report.failed_checks),
            review_reasons=report.review_reasons,
            pdi_export_allowed=eligibility.allowed,
            pdi_export_blocked_reason=eligibility.blocked_reason,
        )
    )


@router.post(
    "/invoices/{invoice_id}/items/{sort_order}/duplicate-decision",
    response_model=APIResponse[DuplicateDecisionResult],
    summary="Decide a possible cross-photo duplicate",
    description=(
        "When one invoice was read from overlapping photos and the model could not "
        "tell whether a row is the same physical row as an earlier one, it kept both "
        "and flagged the later one; the invoice is REVIEW_REQUIRED until a person "
        "decides here. 'same_row' keeps the flagged row for audit but types it "
        "'duplicate' so it leaves the subtotal and the EDI; 'separate_rows' clears "
        "the flag and both rows stay. Validation then runs again with the pipeline's "
        "own rules — nothing is re-extracted and no quantity is ever adjusted to make "
        "totals work."
    ),
    responses={404: {"description": "Invoice or line item not found"},
               422: {"description": "The row carries no duplicate candidate, or was decided"}},
)
async def decide_duplicate(
    invoice_id: uuid.UUID,
    sort_order: int,
    body: DuplicateDecision,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[DuplicateDecisionResult]:
    repository = InvoiceRepository(db)
    invoice = await repository.get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    item = next((i for i in invoice.items if i.sort_order == sort_order), None)
    if item is None:
        raise RecordNotFoundError(
            message="Line item not found on this invoice.",
            detail={"invoice_id": str(invoice_id), "sort_order": sort_order},
        )
    candidate = dict(item.duplicate_candidate or {})
    if not candidate or candidate.get("of_sort_order") is None:
        raise ValidationError(
            message="This row is not flagged as a possible duplicate.",
            detail={"invoice_id": str(invoice_id), "sort_order": sort_order},
        )
    if candidate.get("resolution"):
        raise ValidationError(
            message=(f"This row was already decided as '{candidate['resolution']}' by "
                     f"{candidate.get('decided_by')}; a decision is not changed here."),
            detail={"invoice_id": str(invoice_id), "sort_order": sort_order,
                    "resolution": candidate["resolution"]},
        )

    candidate.update({
        "resolution": body.decision,
        "decided_by": body.decided_by,
        "decided_at": datetime.now(UTC).isoformat(),
        "note": body.note,
    })
    item.duplicate_candidate = candidate
    if body.decision == "same_row":
        item.line_type = "duplicate"
    await db.flush()

    await ProcessingLogRepository(db).add(
        document_id=invoice.document_id,
        stage=PipelineStage.MANUAL_CORRECTION,
        message=(f"Row {sort_order} decided '{body.decision}' against row "
                 f"{candidate['of_sort_order']} by {body.decided_by}."),
        payload={"event": "duplicate_decision", "sort_order": sort_order,
                 "of_sort_order": candidate["of_sort_order"], "decision": body.decision,
                 "decided_by": body.decided_by, "note": body.note},
    )

    invoice = await repository.get_detail(invoice_id)
    report = await revalidate_invoice(db, invoice)
    units = await invoice_units_by_item_code(db, invoice)
    eligibility = persisted_pdi_export_eligibility(invoice, units)
    await db.commit()

    return APIResponse(data=DuplicateDecisionResult(
        sort_order=sort_order, of_sort_order=candidate["of_sort_order"], decision=body.decision,
        line_type=item.line_type, status=report.decision.value,
        failed_checks=len(report.failed_checks), review_reasons=report.review_reasons,
        pdi_export_allowed=eligibility.allowed, pdi_export_blocked_reason=eligibility.blocked_reason,
    ))


@router.post(
    "/invoices/{invoice_id}/assign-store",
    response_model=APIResponse[InvoiceDetailData],
    summary="Assign the store of a STORE_PENDING invoice",
    description=(
        "For an invoice read before its store was known. A person names the store; it is "
        "recorded on the invoice and its document with who assigned it, and from then on the "
        "store's reference data, case mappings, proposals and EDI apply. Nothing is "
        "re-extracted or re-validated — validation does not depend on the store. An invoice "
        "that already has a store is not moved here."
    ),
    responses={404: {"description": "Invoice or store not found"},
               422: {"description": "The invoice already has a store"}},
)
async def assign_store(
    invoice_id: uuid.UUID,
    body: StoreAssignment,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[InvoiceDetailData]:
    repository = InvoiceRepository(db)
    invoice = await repository.get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    if invoice.store_id is not None:
        raise ValidationError(
            message="This invoice already has a store; moving an invoice between stores is not done here.",
            detail={"invoice_id": str(invoice_id), "store_id": str(invoice.store_id)},
        )
    store = await StoreRepository(db).get(body.store_id)
    if store is None:
        raise RecordNotFoundError(message="Store not found.", detail={"store_id": str(body.store_id)})
    ensure_physical_store(store)

    invoice.store_id = store.id
    invoice.document.store_id = store.id
    await ProcessingLogRepository(db).add(
        document_id=invoice.document_id,
        stage=PipelineStage.STORE_IDENTIFICATION,
        message=f"Store assigned by {body.assigned_by}: {store.label}.",
        payload={"event": "store_assigned", "store_id": str(store.id), "store_label": store.label,
                 "assigned_by": body.assigned_by, "note": body.note},
    )
    await db.commit()
    return await get_invoice(invoice_id, db, user)


def _corrected_line(item) -> CorrectedLineItem:
    return CorrectedLineItem(
        sort_order=item.sort_order,
        description=item.description,
        product_code=item.product_sku,
        quantity=float(item.quantity),
        unit_price=float(item.unit_price) if item.unit_price is not None else None,
        line_total=float(item.line_total) if item.line_total is not None else None,
        unit_deposit=float(item.deposit) if item.deposit is not None else None,
        unit_discount=float(item.discount) if item.discount is not None else None,
        line_type=item.line_type,
        entry_source=item.entry_source or "extracted",
        corrected_fields=item.corrected_fields or [],
        correction_history=item.correction_history or [],
    )


async def _log_correction(db, invoice, event: str, by: str | None, note: str | None, **facts) -> None:
    """One processing-log entry per manual change: the invoice's audit trail, in order."""
    await ProcessingLogRepository(db).add(
        document_id=invoice.document_id,
        stage=PipelineStage.MANUAL_CORRECTION,
        message=f"Manual correction ({event.replace('_', ' ')}) by {by or 'unattributed'}.",
        payload={"event": event, "by": by, "note": note, **facts},
    )


async def _revalidated(db, invoice_id: uuid.UUID):
    repository = InvoiceRepository(db)
    invoice = await repository.get_detail(invoice_id)
    report = await revalidate_invoice(db, invoice)
    units = await invoice_units_by_item_code(db, invoice)
    return invoice, report, persisted_pdi_export_eligibility(invoice, units)


@router.post(
    "/invoices/{invoice_id}/items",
    response_model=APIResponse[LineItemCorrectionResult],
    status_code=status.HTTP_201_CREATED,
    summary="Add a line the photos missed",
    description=(
        "A person adds a row to THIS invoice — the same invoice, never a second one. The row is "
        "typed 'manual', every value on it is recorded as the person's, and the addition is in "
        "the row's history and the processing log. Validation then runs again on the corrected "
        "invoice. Invoice-scoped only: nothing here becomes master data (a case mapping for the "
        "product still goes through Data Review)."
    ),
    responses={404: {"description": "Invoice not found"}, 422: {"description": "A negative value"}},
)
async def add_line_item(
    invoice_id: uuid.UUID, payload: LineItemCreate, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[LineItemCorrectionResult]:
    negative = sorted(f for f in ("quantity", "unit_price", "unit_deposit", "unit_discount", "line_total")
                      if getattr(payload, f) is not None and getattr(payload, f) < 0)
    if negative:
        raise ValidationError(message=f"{', '.join(negative)} must not be negative.",
                              detail={"invoice_id": str(invoice_id), "fields": negative})
    repository = InvoiceRepository(db)
    invoice = await repository.get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    line_total = payload.line_total
    derived = False
    if line_total is None and payload.unit_price is not None:
        # The same rule normalization applies to an extracted row that
        # prints no extended total: quantity x unit price, recorded as derived.
        line_total = (payload.quantity * payload.unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        derived = True
    item = await repository.add_item(
        invoice, description=payload.description.strip(),
        product_code=(payload.product_code or "").strip() or None,
        pack_size=(payload.pack_size or "").strip() or None,
        quantity=payload.quantity, unit_price=payload.unit_price, unit_deposit=payload.unit_deposit,
        unit_discount=payload.unit_discount, line_total=line_total,
        added_by=payload.added_by, note=payload.note,
    )
    if derived:
        item.correction_history = [*(item.correction_history or []),
                                   {"field": "line_total", "old": None, "new": float(line_total), "by": None,
                                    "at": datetime.now(UTC).isoformat(),
                                    "note": "derived: quantity x unit_price (no extended total given)"}]
    await _log_correction(db, invoice, "line_item_added", payload.added_by, payload.note,
                          sort_order=item.sort_order, description=item.description,
                          product_code=item.product_sku, quantity=float(item.quantity),
                          unit_price=float(item.unit_price) if item.unit_price is not None else None)
    invoice, report, eligibility = await _revalidated(db, invoice_id)
    await db.commit()
    return APIResponse(data=LineItemCorrectionResult(
        item=_corrected_line(item), status=report.decision.value,
        composite_confidence=report.confidence.composite, failed_checks=len(report.failed_checks),
        review_reasons=report.review_reasons, pdi_export_allowed=eligibility.allowed,
        pdi_export_blocked_reason=eligibility.blocked_reason,
    ))


@router.delete(
    "/invoices/{invoice_id}/items/{sort_order}",
    response_model=APIResponse[LineItemCorrectionResult],
    summary="Void a line that is not on the invoice",
    description=(
        "The row is kept for audit and typed 'voided': it leaves the subtotal and the PDI "
        "export. Recorded with who and why; validation runs again."
    ),
    responses={404: {"description": "Invoice or line not found"}, 422: {"description": "Already voided"}},
)
async def void_line_item(
    invoice_id: uuid.UUID, sort_order: int, payload: LineItemVoid, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[LineItemCorrectionResult]:
    repository = InvoiceRepository(db)
    invoice = await repository.get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    current = next((i for i in invoice.items if i.sort_order == sort_order), None)
    if current is None:
        raise RecordNotFoundError(message="Line item not found on this invoice.",
                                  detail={"invoice_id": str(invoice_id), "sort_order": sort_order})
    if current.line_type == "voided":
        raise ValidationError(message="This line is already voided.",
                              detail={"invoice_id": str(invoice_id), "sort_order": sort_order})
    item = await repository.void_item(invoice_id, sort_order, voided_by=payload.voided_by, note=payload.note)
    await _log_correction(db, invoice, "line_item_voided", payload.voided_by, payload.note,
                          sort_order=sort_order, description=item.description, product_code=item.product_sku)
    invoice, report, eligibility = await _revalidated(db, invoice_id)
    await db.commit()
    return APIResponse(data=LineItemCorrectionResult(
        item=_corrected_line(item), status=report.decision.value,
        composite_confidence=report.confidence.composite, failed_checks=len(report.failed_checks),
        review_reasons=report.review_reasons, pdi_export_allowed=eligibility.allowed,
        pdi_export_blocked_reason=eligibility.blocked_reason,
    ))


@router.patch(
    "/invoices/{invoice_id}/date",
    response_model=APIResponse[InvoiceDateCorrectionResult],
    summary="Correct the invoice date, or record that it is unknown",
    description=(
        "Replaces the invoice date with the one a person read off the document, or sets it to "
        "null when the document genuinely does not show one. The extracted value is kept in the "
        "correction history (old, new, who, when, why) and `invoice_date` is listed in "
        "corrected_fields, so a typed date never reads as extracted data. The date is never "
        "inferred from upload, processing, file or payment dates. Validation runs again with the "
        "pipeline's own rules and the document's lifecycle state follows the new decision."
    ),
    responses={404: {"description": "Invoice not found"}},
)
async def correct_invoice_date(
    invoice_id: uuid.UUID, payload: InvoiceDateCorrection, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[InvoiceDateCorrectionResult]:
    repository = InvoiceRepository(db)
    invoice = await repository.get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    await repository.correct_invoice_date(invoice, payload.invoice_date,
                                          corrected_by=payload.corrected_by, note=payload.note)
    await _log_correction(db, invoice, "invoice_date_corrected", payload.corrected_by, payload.note,
                          changes=(invoice.correction_history or [])[-1:])
    invoice, report, eligibility = await _revalidated(db, invoice_id)
    await db.commit()
    return APIResponse(data=InvoiceDateCorrectionResult(
        invoice_date=invoice.invoice_date,
        corrected_fields=invoice.corrected_fields or [], correction_history=invoice.correction_history or [],
        status=report.decision.value, composite_confidence=report.confidence.composite,
        failed_checks=len(report.failed_checks), review_reasons=report.review_reasons,
        pdi_export_allowed=eligibility.allowed, pdi_export_blocked_reason=eligibility.blocked_reason,
    ))


@router.patch(
    "/invoices/{invoice_id}/totals",
    response_model=APIResponse[InvoiceTotalsCorrectionResult],
    summary="Correct printed header totals (grand total included)",
    description=(
        "Replaces subtotal, tax, discount, deposit total, fuel surcharge and/or grand total with "
        "figures a person read off the document. Each extracted value is kept in the invoice's "
        "correction history (old, new, who, when, why) and the field is listed in "
        "corrected_fields, so a typed figure never reads as extracted data. Validation runs "
        "again with the pipeline's own rules; no line is altered to make the totals fit."
    ),
    responses={404: {"description": "Invoice not found"}, 422: {"description": "No fields, or a negative value"}},
)
async def correct_totals(
    invoice_id: uuid.UUID, payload: InvoiceTotalsCorrection, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[InvoiceTotalsCorrectionResult]:
    updates = payload.updates()
    if not updates:
        raise ValidationError(message="Provide at least one total to correct.",
                              detail={"invoice_id": str(invoice_id)})
    negative = sorted(f for f, v in updates.items() if v < 0)
    if negative:
        raise ValidationError(message=f"{', '.join(negative)} must not be negative.",
                              detail={"invoice_id": str(invoice_id), "fields": negative})
    repository = InvoiceRepository(db)
    invoice = await repository.get_detail(invoice_id)
    if invoice is None:
        raise RecordNotFoundError(message="Invoice not found.", detail={"invoice_id": str(invoice_id)})
    await repository.correct_totals(invoice, updates, corrected_by=payload.corrected_by, note=payload.note)
    await _log_correction(db, invoice, "totals_corrected", payload.corrected_by, payload.note,
                          changes=(invoice.correction_history or [])[-len(updates):])
    invoice, report, eligibility = await _revalidated(db, invoice_id)
    await db.commit()
    money = lambda v: float(v) if v is not None else None  # noqa: E731
    return APIResponse(data=InvoiceTotalsCorrectionResult(
        subtotal=money(invoice.subtotal), tax_amount=money(invoice.tax_amount),
        discount_amount=money(invoice.discount_amount), deposit_total=money(invoice.deposit_total),
        fuel_surcharge=money(invoice.fuel_surcharge), grand_total=money(invoice.grand_total),
        corrected_fields=invoice.corrected_fields or [], correction_history=invoice.correction_history or [],
        status=report.decision.value, composite_confidence=report.confidence.composite,
        failed_checks=len(report.failed_checks), review_reasons=report.review_reasons,
        pdi_export_allowed=eligibility.allowed, pdi_export_blocked_reason=eligibility.blocked_reason,
    ))
