"""
Invoice Processing Pipeline — app/services/pipeline_service.py

The end-to-end orchestrator tying every stage together:

    duplicate check → Document(UPLOADED) → OCR_IN_PROGRESS → extract
    → OCR_COMPLETED → AI_PROCESSING → structure → validate
    → VALIDATED / REVIEW_REQUIRED → persist → COMPLETED

Design decisions:
- Two-level transaction strategy:
    * Each stage transition is committed immediately, so document status
      is always live (the Processing Jobs screen reads real progress)
      and a crashed run leaves an accurate audit trail.
    * The business write (vendor + invoice + items + final status) is
      one atomic transaction inside PersistenceService.
- On any stage failure: roll back the in-flight transaction, mark the
  document FAILED with a FAILURE log entry in a fresh transaction, and
  re-raise the domain exception — Sprint 1's global handlers render it.
- Every stage appends a ProcessingLog entry whose JSONB payload carries
  that stage's observability (OCR metrics, full LLM call metadata, the
  complete validation report, persistence identifiers).
- Stage services are injectable for tests; defaults resolve lazily so
  constructing the pipeline requires no OCR/LLM credentials.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    DatabaseError,
    DuplicateDocumentError,
    InvoiceBaseException,
)
from app.core.logging import get_logger
from app.models.document import Document, DocumentStatus
from app.models.processing_log import LogStatus, PipelineStage
from app.repositories.document_repository import DocumentRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.services.extraction_service import ExtractionService
from app.services.persistence_service import PersistenceService
from app.services.structuring_service import StructuringService
from app.services.validation.report import ProcessingDecision
from app.services.validation.service import ValidationService

logger = get_logger(__name__)


@dataclass
class PipelineResult:
    """Everything the API layer needs to describe a completed run."""

    document_id: uuid.UUID
    document_status: DocumentStatus
    decision: ProcessingDecision
    invoice_id: uuid.UUID | None
    vendor_id: uuid.UUID | None
    vendor_created: bool
    composite_confidence: float
    source_type: str
    prompt_version: str
    validation_report: dict[str, Any] = field(default_factory=dict)
    llm_metadata: dict[str, Any] = field(default_factory=dict)


class InvoiceProcessingPipeline:
    """
    Orchestrates one document through the full processing lifecycle.

    Usage:
        pipeline = InvoiceProcessingPipeline()
        result = await pipeline.process(session, file_content=..., ...)
    """

    def __init__(
        self,
        extraction_service: ExtractionService | None = None,
        structuring_service: StructuringService | None = None,
        validation_service: ValidationService | None = None,
        persistence_service: PersistenceService | None = None,
    ) -> None:
        self._extraction = extraction_service
        self._structuring = structuring_service
        self._validation = validation_service
        self._persistence = persistence_service or PersistenceService()

    # Lazy defaults so the pipeline can be constructed without credentials.
    @property
    def extraction(self) -> ExtractionService:
        if self._extraction is None:
            self._extraction = ExtractionService()
        return self._extraction

    @property
    def structuring(self) -> StructuringService:
        if self._structuring is None:
            self._structuring = StructuringService()
        return self._structuring

    @property
    def validation(self) -> ValidationService:
        if self._validation is None:
            self._validation = ValidationService()
        return self._validation

    async def process(
        self,
        session: AsyncSession,
        *,
        file_content: bytes,
        filename: str,
        mime_type: str,
        file_size_bytes: int,
        file_path: str,
        file_hash: str,
    ) -> PipelineResult:
        """
        Process one uploaded document end-to-end.

        Raises:
            DuplicateDocumentError: Same content hash already processed.
            OCRExtractionError / AIStructuringError / DatabaseError:
                Stage failures, after the document is marked FAILED.
        """
        documents = DocumentRepository(session)
        logs = ProcessingLogRepository(session)

        # ---- Duplicate check (before any row is created) -----------------
        existing = await documents.get_by_hash(file_hash)
        if existing is not None:
            raise DuplicateDocumentError(
                detail={
                    "existing_document_id": str(existing.id),
                    "existing_status": existing.status,
                    "file_hash": file_hash,
                }
            )

        # ---- Document creation -------------------------------------------
        document = await documents.create(
            filename=filename,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            file_path=file_path,
            file_hash=file_hash,
        )
        await logs.add(
            document_id=document.id,
            stage=PipelineStage.UPLOAD,
            message="Document received and stored.",
            payload={
                "filename": filename,
                "mime_type": mime_type,
                "file_size_bytes": file_size_bytes,
                "file_hash": file_hash,
            },
        )
        await session.commit()
        logger.info("pipeline_document_created", document_id=str(document.id), filename=filename)

        # ---- Text extraction ----------------------------------------------
        await documents.set_status(document, DocumentStatus.OCR_IN_PROGRESS)
        await session.commit()
        try:
            ocr_result = await self.extraction.extract_text(file_content, mime_type, filename)
        except InvoiceBaseException as exc:
            await self._fail(session, document, PipelineStage.TEXT_EXTRACTION, exc)
            raise

        await documents.store_extraction(
            document, raw_ocr_text=ocr_result.full_text, source_type=ocr_result.source_type
        )
        await logs.add(
            document_id=document.id,
            stage=PipelineStage.TEXT_EXTRACTION,
            message=f"Text extracted via {ocr_result.source_type}.",
            payload={
                "source_type": ocr_result.source_type,
                "page_count": ocr_result.page_count,
                "mean_confidence": round(ocr_result.mean_confidence, 4),
                "text_chars": len(ocr_result.full_text),
            },
            duration_ms=ocr_result.duration_ms,
        )
        await session.commit()

        # ---- AI structuring -------------------------------------------------
        await documents.set_status(document, DocumentStatus.AI_PROCESSING)
        await session.commit()
        try:
            structuring = await self.structuring.structure_invoice(ocr_result, filename)
        except InvoiceBaseException as exc:
            await self._fail(session, document, PipelineStage.AI_STRUCTURING, exc)
            raise

        llm_payload = structuring.metadata.to_dict() | {
            "prompt_version": structuring.prompt_version,
            "ocr_text_truncated": structuring.ocr_text_truncated,
        }
        await logs.add(
            document_id=document.id,
            stage=PipelineStage.AI_STRUCTURING,
            message=f"Structured by {structuring.metadata.model}.",
            payload=llm_payload,
            duration_ms=structuring.metadata.latency_ms,
        )
        await session.commit()

        # ---- Validation -----------------------------------------------------
        validation = self.validation.validate_invoice(
            structuring.invoice, ocr_result.mean_confidence, filename
        )
        decision = validation.report.decision
        await documents.set_status(
            document,
            DocumentStatus.VALIDATED
            if decision is ProcessingDecision.VALIDATED
            else DocumentStatus.REVIEW_REQUIRED,
        )
        await logs.add(
            document_id=document.id,
            stage=PipelineStage.VALIDATION,
            message=f"Validation decision: {decision.value}.",
            payload=validation.report.to_dict(),
            duration_ms=validation.report.duration_ms,
        )
        await session.commit()

        # ---- Persistence (atomic) -------------------------------------------
        try:
            invoice, vendor, vendor_created = await self._persistence.persist_invoice(
                session, document=document, structuring=structuring, validation=validation
            )
        except InvoiceBaseException as exc:
            await self._fail(session, document, PipelineStage.PERSISTENCE, exc)
            raise
        except Exception as exc:
            wrapped = DatabaseError(
                message="Failed to persist the extracted invoice.",
                detail={"error": str(exc)[:500]},
            )
            wrapped.__cause__ = exc
            await self._fail(session, document, PipelineStage.PERSISTENCE, wrapped)
            raise wrapped from exc

        logger.info(
            "pipeline_complete",
            document_id=str(document.id),
            decision=decision.value,
            final_status=document.status,
            invoice_id=str(invoice.id),
        )
        return PipelineResult(
            document_id=document.id,
            document_status=DocumentStatus(document.status),
            decision=decision,
            invoice_id=invoice.id,
            vendor_id=vendor.id if vendor else None,
            vendor_created=vendor_created,
            composite_confidence=validation.report.confidence.composite,
            source_type=ocr_result.source_type,
            prompt_version=structuring.prompt_version,
            validation_report=validation.report.to_dict(),
            llm_metadata=llm_payload,
        )

    async def _fail(
        self,
        session: AsyncSession,
        document: Document,
        stage: PipelineStage,
        exc: InvoiceBaseException,
    ) -> None:
        """
        Mark the document FAILED after a stage error.

        Rolls back whatever the failed stage left in the session, then
        records the failure in a fresh transaction. Never raises — the
        original stage exception must be the one that propagates.
        """
        try:
            await session.rollback()
            documents = DocumentRepository(session)
            logs = ProcessingLogRepository(session)
            await documents.set_status(document, DocumentStatus.FAILED)
            await logs.add(
                document_id=document.id,
                stage=stage,
                status=LogStatus.FAILURE,
                message=exc.message,
                payload={"error_code": exc.error_code, "detail": exc.detail},
            )
            await session.commit()
            logger.warning(
                "pipeline_stage_failed",
                document_id=str(document.id),
                stage=stage.value,
                error_code=exc.error_code,
            )
        except Exception:  # pragma: no cover — best-effort failure marking
            logger.exception(
                "pipeline_failure_marking_failed",
                document_id=str(document.id),
                stage=stage.value,
            )
