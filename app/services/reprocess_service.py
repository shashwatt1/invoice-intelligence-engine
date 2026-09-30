"""
Governed Reprocessing — app/services/reprocess_service.py

Run a document that has already been processed through the pipeline
again, under the currently active prompt and model, without disturbing
its identity.

Why this exists: the only way to re-extract used to be deleting the
invoice and uploading the file again. That is a hard delete — it discards
the document id, the invoice id, every processing log and the stored
source file. Those ids are the stable identity other records point at (a
product-data proposal carries invoice_id), so losing them loses the
thread between a review decision and the invoice it was made about.

What this does instead:

  * the document and its invoice keep their ids, and the source file and
    its hash are never touched;
  * the whole extraction — OCR, structuring, validation — runs BEFORE
    anything is written, so a failure at any stage leaves the previous
    operational state exactly as it was;
  * one transaction then replaces the operational extraction result:
    header figures, line items, status, confidence, model, raw response;
  * every entry the attempt writes is stamped with a run id and an
    attempt number, so the original run and each reprocess stay
    distinguishable in the existing processing log;
  * the superseded raw extraction is kept in that log, because the
    invoice holds only one raw_extraction_json and overwriting it would
    otherwise discard the evidence the earlier result rested on.

What it deliberately refuses: an invoice a person has corrected. Those
corrections were made against figures this would replace, and the history
would end up describing values no longer on the record. Master data —
case mappings and product-data proposals — is never touched, no mapping
is approved, and no EDI is generated: readiness is reported by the normal
gate and acted on separately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.models.document import Document
from app.models.invoice import Invoice
from app.models.processing_log import LogStatus, PipelineStage, ProcessingLog
from app.models.vendor import VENDOR_CONFIRMED, Vendor
from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.vendor_repository import VendorRepository
from app.services.document_lifecycle import document_status_for
from app.services.photo_context import PhotoOCR, combine_photos
from app.services.storage_service import get_storage_service

logger = get_logger(__name__)


@dataclass(frozen=True)
class ReprocessResult:
    document_id: uuid.UUID
    invoice_id: uuid.UUID
    attempt: int
    run_id: str
    prompt_version: str
    model: str | None
    decision: str
    document_status: str
    line_item_count: int
    review_reasons: list[str] = field(default_factory=list)


async def _next_attempt(session: AsyncSession, document_id: uuid.UUID) -> int:
    """
    Which attempt this is. Attempt 1 is the original run, whose entries
    carry no attempt marker; every reprocess stamps its own number.
    """
    logs = (await session.execute(
        select(ProcessingLog).where(ProcessingLog.document_id == document_id)
    )).scalars().all()
    recorded = [
        (log.payload or {}).get("attempt") for log in logs
        if isinstance((log.payload or {}).get("attempt"), int)
    ]
    return (max(recorded) if recorded else 1) + 1


@dataclass(frozen=True)
class VendorSettlement:
    """Which vendor a reprocessed invoice keeps, and whether the new reading disagreed."""

    vendor_id: uuid.UUID | None
    # Set when a CONFIRMED vendor was kept although the new reading names another
    # vendor (or none) — recorded for a person to review; nothing is merged.
    discrepancy: dict[str, Any] | None = None


async def settle_reprocess_vendor(session: AsyncSession, invoice: Invoice, normalized: Any) -> VendorSettlement:
    """
    A confirmed vendor is a person's decision: a new reading never replaces it.
    If the reading matches that vendor exactly it is kept (and blank contact
    fields filled, as always); if it names another vendor or none, it is still
    kept and the reading is returned as a discrepancy — no vendor is created,
    merged or re-keyed for it. An unconfirmed vendor is re-matched by the same
    exact rule as first processing. The invoice's raw vendor fields always take
    the new reading (replace_extraction); only the canonical link is protected.
    """
    vendors = VendorRepository(session)
    current = await session.get(Vendor, invoice.vendor_id) if invoice.vendor_id else None
    if current is not None and current.identity_status == VENDOR_CONFIRMED:
        observed = await vendors.find_existing(normalized)
        if observed is not None and observed.id == current.id:
            await vendors.get_or_create(normalized)
            return VendorSettlement(current.id)
        return VendorSettlement(current.id, {
            "event": "vendor_identity_discrepancy",
            "kept_vendor_id": str(current.id), "kept_vendor_label": current.label,
            "observed_vendor_id": str(observed.id) if observed else None,
            "observed_vendor_name": normalized.vendor_name,
            "observed_vendor_tax_id": normalized.vendor_tax_id,
        })
    vendor, _ = await vendors.get_or_create(normalized)
    return VendorSettlement(vendor.id if vendor else None)


def _blocking_corrections(invoice: Invoice) -> list[str]:
    """Fields a person has corrected, which a replacement would invalidate."""
    blocking = list(invoice.corrected_fields or [])
    for item in invoice.items:
        if item.correction_history:
            blocking.append(f"line_items[{item.sort_order}]")
    return blocking


async def reprocess_document(
    session: AsyncSession, document: Document, *, pipeline: Any
) -> ReprocessResult:
    """
    Re-extract a stored document under the active prompt and model.

    Raises ValidationError when the document has no invoice yet (it is
    still in the normal flow, which owns it) or when the invoice carries
    human corrections. Extraction failures propagate unchanged, before
    anything has been written.
    """
    invoices = InvoiceRepository(session)
    document_id = document.id
    invoice = await invoices.get_by_document(document_id)
    if invoice is None:
        raise ValidationError(
            message="This document has no persisted invoice to replace; let the normal pipeline finish it.",
            detail={"document_id": str(document_id), "status": document.status},
        )
    await session.refresh(invoice, ["items"])
    blocking = _blocking_corrections(invoice)
    if blocking:
        raise ValidationError(
            message=(
                "This invoice carries manual corrections; reprocessing would replace the "
                "figures they were made against. Delete and re-upload if the extraction "
                "must be redone, or correct the remaining fields by hand."
            ),
            detail={"invoice_id": str(invoice.id), "corrected": blocking},
        )

    # ---- Everything that can fail happens before anything is written ----
    storage = get_storage_service()
    pages = await DocumentRepository(session).pages(document)
    sources = (
        [(p.page_number, p.filename, p.mime_type, p.file_path) for p in pages]
        if pages else
        [(1, document.filename, document.mime_type, document.file_path)]
    )
    attempt = await _next_attempt(session, document_id)
    run_id = str(uuid.uuid4())
    stamp = {"attempt": attempt, "run_id": run_id}
    logs = ProcessingLogRepository(session)

    try:
        photos: list[PhotoOCR] = []
        for page_number, filename, mime_type, file_path in sources:
            content = await storage.read(file_path)
            result = await pipeline.extraction.extract_text(content, mime_type, filename)
            photos.append(PhotoOCR(page_number=page_number, filename=filename, result=result))
        ocr_result = combine_photos(photos)

        structuring = await pipeline.structuring.structure_invoice(ocr_result, document.filename)
        validation = pipeline.validation.validate_invoice(
            structuring.invoice, ocr_result.mean_confidence, document.filename
        )
    except Exception as exc:
        # The attempt is recorded in its own transaction so a failure is
        # auditable, then re-raised. Nothing of the invoice was touched:
        # extraction runs entirely before the replacement transaction.
        await session.rollback()
        await logs.add(
            document_id=document_id,
            stage=PipelineStage.AI_STRUCTURING,
            status=LogStatus.FAILURE,
            message=f"Reprocess attempt {attempt} failed; the previous result is unchanged.",
            payload={**stamp, "event": "reprocess_failed",
                     "error": type(exc).__name__, "detail": str(exc)[:500]},
        )
        await session.commit()
        logger.warning("invoice_reprocess_failed", document_id=str(document_id),
                       attempt=attempt, run_id=run_id, error=str(exc)[:200])
        raise
    decision = validation.report.decision

    # ---- One transaction replaces the operational state -----------------

    await logs.add(
        document_id=document_id,
        stage=PipelineStage.TEXT_EXTRACTION,
        status=LogStatus.SUCCESS,
        message=f"Reprocess attempt {attempt}: re-extracted from the stored source.",
        payload={
            **stamp,
            "event": "reprocess_started",
            "source_type": ocr_result.source_type,
            "page_count": ocr_result.page_count,
            "superseded": {
                "prompt_version": _superseded_prompt_version(await logs.for_document(document.id)),
                "extraction_model": invoice.extraction_model,
                "status": invoice.status,
                "line_item_count": len(invoice.items),
                "raw_extraction_json": invoice.raw_extraction_json,
            },
        },
        duration_ms=ocr_result.duration_ms,
    )
    await logs.add(
        document_id=document_id,
        stage=PipelineStage.AI_STRUCTURING,
        status=LogStatus.SUCCESS,
        message=f"Reprocess attempt {attempt}: structured by {structuring.metadata.model}.",
        payload={**stamp, **structuring.metadata.to_dict(),
                 "prompt_version": structuring.prompt_version,
                 "ocr_text_truncated": structuring.ocr_text_truncated},
        duration_ms=structuring.metadata.latency_ms,
    )
    await logs.add(
        document_id=document_id,
        stage=PipelineStage.VALIDATION,
        status=LogStatus.SUCCESS,
        message=f"Reprocess attempt {attempt}: validation decision {decision.value}.",
        payload={**stamp, **validation.report.to_dict()},
        duration_ms=validation.report.duration_ms,
    )

    previous_vendor_id = invoice.vendor_id
    settled = await settle_reprocess_vendor(session, invoice, validation.invoice)
    await invoices.replace_extraction(
        invoice,
        vendor_id=settled.vendor_id,
        normalized=validation.invoice,
        decision=decision,
        composite_confidence=validation.report.confidence.composite,
        extraction_model=structuring.metadata.model,
        raw_extraction_json=structuring.raw_response,
    )
    if settled.discrepancy is not None:
        observed = settled.discrepancy["observed_vendor_name"] or "no vendor"
        await logs.add(
            document_id=document_id,
            stage=PipelineStage.PERSISTENCE,
            message=(f"Vendor identity discrepancy: kept the confirmed vendor "
                     f"{settled.discrepancy['kept_vendor_label']}; this reading names {observed}. "
                     "Review the vendor before relying on this invoice's vendor."),
            payload={**stamp, **settled.discrepancy},
        )
    elif settled.vendor_id != previous_vendor_id:
        await logs.add(
            document_id=document_id,
            stage=PipelineStage.PERSISTENCE,
            message="Reprocess re-matched the unconfirmed vendor exactly, as on first processing.",
            payload={**stamp, "event": "vendor_rematched",
                     "previous_vendor_id": str(previous_vendor_id) if previous_vendor_id else None,
                     "vendor_id": str(settled.vendor_id) if settled.vendor_id else None},
        )
    final_status = document_status_for(decision)
    await DocumentRepository(session).set_status(document, final_status)
    await logs.add(
        document_id=document_id,
        stage=PipelineStage.PERSISTENCE,
        status=LogStatus.SUCCESS,
        message=f"Reprocess attempt {attempt}: extraction replaced; document {final_status}.",
        payload={**stamp, "event": "reprocess_completed", "invoice_id": str(invoice.id),
                 "line_item_count": len(validation.invoice.line_items),
                 "final_status": final_status.value},
    )
    await session.commit()

    logger.info(
        "invoice_reprocessed",
        document_id=str(document.id), invoice_id=str(invoice.id), attempt=attempt,
        run_id=run_id, prompt_version=structuring.prompt_version,
        model=structuring.metadata.model, decision=decision.value,
    )
    return ReprocessResult(
        document_id=document.id, invoice_id=invoice.id, attempt=attempt, run_id=run_id,
        prompt_version=structuring.prompt_version, model=structuring.metadata.model,
        decision=decision.value, document_status=final_status.value,
        line_item_count=len(validation.invoice.line_items),
        review_reasons=list(validation.report.review_reasons),
    )


def _superseded_prompt_version(logs: list[ProcessingLog]) -> str | None:
    """The prompt version of the result this attempt replaces, if recorded."""
    versions = [
        (log.payload or {}).get("prompt_version") for log in logs
        if log.stage == PipelineStage.AI_STRUCTURING and (log.payload or {}).get("prompt_version")
    ]
    return versions[-1] if versions else None
