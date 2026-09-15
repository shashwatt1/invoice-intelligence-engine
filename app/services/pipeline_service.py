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
    AIStructuringError,
    DatabaseError,
    DuplicateDocumentError,
    InvoiceBaseException,
    OCRExtractionError,
)
from app.core.logging import get_logger
from app.models.document import Document, DocumentStatus
from app.models.processing_log import LogStatus, PipelineStage
from app.repositories.document_repository import DocumentRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.services.extraction_service import ExtractionService
from app.services.ocr.base import OCRResult
from app.services.persistence_service import PersistenceService
from app.services.store_identification_service import candidate_ids, identify_store
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
    awaiting_store_confirmation: bool = False
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
        store_id: uuid.UUID | None = None,
    ) -> PipelineResult:
        """
        Process one uploaded document end-to-end (intake + all stages).

        `store_id` is the store the operator says the invoice is for, if
        they said. There is no default: after text extraction the document
        is matched against the store master, and unless the operator's
        choice is consistent with what the document says, the run pauses
        for a person to confirm (see run_stages).

        Raises:
            DuplicateDocumentError: Same content hash already processed.
            OCRExtractionError / AIStructuringError / DatabaseError:
                Stage failures, after the document is marked FAILED.
        """
        document = await self.intake(
            session,
            filename=filename,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            file_path=file_path,
            file_hash=file_hash,
            store_id=store_id,
        )
        return await self.run_stages(
            session,
            document,
            file_content=file_content,
            mime_type=mime_type,
            filename=filename,
            store_id=store_id,
        )

    async def intake(
        self,
        session: AsyncSession,
        *,
        filename: str,
        mime_type: str,
        file_size_bytes: int,
        file_path: str,
        file_hash: str,
        store_id: uuid.UUID | None = None,
    ) -> Document:
        """
        Synchronous intake: duplicate check + Document(UPLOADED) + UPLOAD log.

        Split from run_stages() so the API can return 202 with a document_id
        immediately and execute the remaining stages in the background while
        clients poll the document status.

        Raises:
            DuplicateDocumentError: Same content hash already processed.
        """
        documents = DocumentRepository(session)
        logs = ProcessingLogRepository(session)

        existing = await documents.get_by_hash(file_hash)
        if existing is not None:
            raise DuplicateDocumentError(
                detail={
                    "existing_document_id": str(existing.id),
                    "existing_status": existing.status,
                    "file_hash": file_hash,
                }
            )

        document = await documents.create(
            filename=filename,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            file_path=file_path,
            file_hash=file_hash,
        )
        document.store_id = store_id
        await logs.add(
            document_id=document.id,
            stage=PipelineStage.UPLOAD,
            message="Document received and stored.",
            payload={
                "filename": filename,
                "mime_type": mime_type,
                "file_size_bytes": file_size_bytes,
                "file_hash": file_hash,
                "store_id": str(store_id) if store_id else None,
            },
        )
        await session.commit()
        logger.info("pipeline_document_created", document_id=str(document.id), filename=filename,
                    store_id=str(store_id) if store_id else None)
        return document

    async def run_stages(
        self,
        session: AsyncSession,
        document: Document,
        *,
        file_content: bytes,
        mime_type: str,
        filename: str,
        store_id: uuid.UUID | None = None,
    ) -> PipelineResult:
        """
        Run extraction, then store identification; continue through
        structuring → validation → persistence only once the store is
        settled. See process() for the failure contract.

        The store is settled when the operator chose one AND the document
        does not name a different one. Otherwise the run pauses in
        STORE_CONFIRMATION_REQUIRED with the candidates recorded, and
        resume_after_store_confirmation() finishes it later. The pipeline
        never picks a store itself.
        """
        documents = DocumentRepository(session)
        logs = ProcessingLogRepository(session)

        # ---- Text extraction ----------------------------------------------
        await documents.set_status(document, DocumentStatus.OCR_IN_PROGRESS)
        await session.commit()
        try:
            ocr_result = await self.extraction.extract_text(file_content, mime_type, filename)
        except InvoiceBaseException as exc:
            await self._fail(session, document, PipelineStage.TEXT_EXTRACTION, exc)
            raise
        except Exception as exc:
            # Non-domain errors (e.g. provider misconfiguration) must still
            # drive the document to a terminal FAILED state.
            wrapped = OCRExtractionError(
                message=f"Unexpected extraction failure: {exc}",
                detail={"error": str(exc)[:500]},
            )
            await self._fail(session, document, PipelineStage.TEXT_EXTRACTION, wrapped)
            raise wrapped from exc

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

        # ---- Store identification -------------------------------------------
        settled = await self._settle_store(session, document, ocr_result.full_text, store_id)
        if settled is None:
            return self._paused(document, ocr_result.source_type)

        return await self._run_from_structuring(
            session, document, ocr_result=ocr_result, filename=filename, store_id=settled
        )

    async def resume_after_store_confirmation(
        self, session: AsyncSession, document: Document
    ) -> PipelineResult:
        """
        Finish a run that paused for store confirmation. The text was
        extracted and stored earlier; nothing is re-extracted.
        """
        if not document.store_id:
            raise ValueError("The document has no confirmed store; nothing to resume.")
        if document.raw_ocr_text is None:
            raise ValueError("The document has no extracted text to resume from.")
        logs = await ProcessingLogRepository(session).for_document(document.id)
        extraction = next((log for log in logs if log.stage == PipelineStage.TEXT_EXTRACTION), None)
        payload = (extraction.payload if extraction else None) or {}
        ocr_result = OCRResult(
            full_text=document.raw_ocr_text,
            source_type=document.source_type or "ocr",
            page_count=int(payload.get("page_count") or 0),
            mean_confidence=float(payload.get("mean_confidence") or 1.0),
        )
        return await self._run_from_structuring(
            session, document, ocr_result=ocr_result, filename=document.filename,
            store_id=document.store_id,
        )

    async def _settle_store(
        self, session: AsyncSession, document: Document, text: str, chosen: uuid.UUID | None
    ) -> uuid.UUID | None:
        """
        The store to proceed with, or None to pause for a person.

        Records what identification found on the document either way,
        so the operator sees the evidence whichever way it went.
        """
        documents = DocumentRepository(session)
        logs = ProcessingLogRepository(session)
        candidates = await identify_store(session, text)
        found = candidate_ids(candidates)
        document.store_candidates = [c.to_dict() for c in candidates]

        if chosen is not None and (not found or found == {str(chosen)}):
            document.store_id = chosen
            outcome = "operator choice confirmed" if found else "operator choice; document names no store"
            proceed = True
        elif chosen is not None:
            outcome = "operator choice conflicts with what the document names"
            proceed = False
        elif len(found) == 1:
            outcome = "one store matched; awaiting confirmation"
            proceed = False
        elif found:
            outcome = "several stores matched; awaiting selection"
            proceed = False
        else:
            outcome = "no store matched; awaiting manual identification"
            proceed = False

        await logs.add(
            document_id=document.id,
            stage=PipelineStage.STORE_IDENTIFICATION,
            message=f"Store identification: {outcome}.",
            payload={
                "operator_store_id": str(chosen) if chosen else None,
                "candidates": document.store_candidates,
                "outcome": outcome,
                "proceeded": proceed,
            },
        )
        if not proceed:
            await documents.set_status(document, DocumentStatus.STORE_CONFIRMATION_REQUIRED)
        await session.commit()
        logger.info("store_identification", document_id=str(document.id), outcome=outcome,
                    candidates=len(candidates))
        return chosen if proceed else None

    @staticmethod
    def _paused(document: Document, source_type: str) -> PipelineResult:
        return PipelineResult(
            document_id=document.id,
            document_status=DocumentStatus(document.status),
            decision=ProcessingDecision.REVIEW_REQUIRED,
            invoice_id=None, vendor_id=None, vendor_created=False,
            composite_confidence=0.0, source_type=source_type, prompt_version="",
            awaiting_store_confirmation=True,
        )

    async def _run_from_structuring(
        self,
        session: AsyncSession,
        document: Document,
        *,
        ocr_result: OCRResult,
        filename: str,
        store_id: uuid.UUID,
    ) -> PipelineResult:
        documents = DocumentRepository(session)
        logs = ProcessingLogRepository(session)

        # ---- AI structuring -------------------------------------------------
        await documents.set_status(document, DocumentStatus.AI_PROCESSING)
        await session.commit()
        try:
            structuring = await self.structuring.structure_invoice(ocr_result, filename)
        except InvoiceBaseException as exc:
            await self._fail(session, document, PipelineStage.AI_STRUCTURING, exc)
            raise
        except Exception as exc:
            wrapped = AIStructuringError(
                message=f"Unexpected structuring failure: {exc}",
                detail={"error": str(exc)[:500]},
            )
            await self._fail(session, document, PipelineStage.AI_STRUCTURING, wrapped)
            raise wrapped from exc

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
                session, document=document, store_id=store_id,
                structuring=structuring, validation=validation,
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
