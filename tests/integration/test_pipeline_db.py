"""
tests/integration/test_pipeline_db.py — Processing pipeline against real Postgres.

The database is the real dependency under test; OCR and LLM stages are
injected fakes (deterministic, offline). Covers the happy path, review
routing, vendor upsert reuse, duplicate rejection, stage failure with
FAILED marking, and persistence rollback atomicity.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.exceptions import (
    AIStructuringError,
    DatabaseError,
    DuplicateDocumentError,
    OCRExtractionError,
)
from app.models import (
    Document,
    DocumentStatus,
    Invoice,
    InvoiceItem,
    LogStatus,
    PipelineStage,
    ProcessingLog,
    Vendor,
)
from app.schemas.extraction import ExtractedLineItem, ExtractedVendor
from app.services.pipeline_service import InvoiceProcessingPipeline
from app.services.validation.report import ProcessingDecision
from tests.integration.conftest import requires_db
from tests.integration.fakes import FakeExtraction, FakeStructuring, extracted_invoice

pytestmark = requires_db


def make_pipeline(extraction=None, structuring=None) -> InvoiceProcessingPipeline:
    return InvoiceProcessingPipeline(
        extraction_service=extraction or FakeExtraction(),
        structuring_service=structuring or FakeStructuring(),
    )


def upload_kwargs(seed: str = "a") -> dict:
    return {
        "file_content": b"%PDF-fake " + seed.encode(),
        "filename": f"invoice-{seed}.pdf",
        "mime_type": "application/pdf",
        "file_size_bytes": 2048,
        "file_path": f"/uploads/invoice-{seed}.pdf",
        "file_hash": ("0" * 63) + seed,
    }


async def one(session, query):
    return (await session.execute(query)).scalar_one()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_full_pipeline_persists_everything(self, db_session):
        result = await make_pipeline().process(db_session, **upload_kwargs())

        assert result.decision is ProcessingDecision.VALIDATED
        assert result.document_status is DocumentStatus.COMPLETED
        assert result.composite_confidence >= 0.85

        document = await one(db_session, select(Document))
        assert document.status == DocumentStatus.COMPLETED
        assert document.raw_ocr_text.startswith("INVOICE INV-001")
        assert document.source_type == "digital_pdf"

        invoice = await one(db_session, select(Invoice))
        assert invoice.invoice_number == "INV-001"
        assert str(invoice.grand_total) == "18.90"
        assert invoice.status == "VALIDATED"
        assert invoice.extraction_model == "gpt-4o-mini"
        assert invoice.raw_extraction_json["id"] == "chatcmpl-fake"
        assert float(invoice.composite_confidence) == result.composite_confidence

        item = await one(db_session, select(InvoiceItem))
        assert item.description == "Blue Widget"
        assert str(item.unit_price) == "9.4500"  # derived from 18.90 / 2
        assert str(item.line_total) == "18.90"

        vendor = await one(db_session, select(Vendor))
        assert vendor.name == "Acme Corp"
        assert vendor.tax_id == "GB123"
        assert invoice.vendor_id == vendor.id

    async def test_processing_log_records_every_stage(self, db_session):
        result = await make_pipeline().process(db_session, **upload_kwargs())

        logs = (await db_session.execute(
            select(ProcessingLog).order_by(ProcessingLog.created_at, ProcessingLog.id)
        )).scalars().all()

        stages = [log.stage for log in logs]
        assert stages == [
            PipelineStage.UPLOAD,
            PipelineStage.TEXT_EXTRACTION,
            PipelineStage.AI_STRUCTURING,
            PipelineStage.VALIDATION,
            PipelineStage.PERSISTENCE,
        ]
        assert all(log.status == LogStatus.SUCCESS for log in logs)

        by_stage = {log.stage: log for log in logs}
        assert by_stage[PipelineStage.AI_STRUCTURING].payload["total_tokens"] == 1450
        assert by_stage[PipelineStage.AI_STRUCTURING].payload["prompt_version"] == "v1"
        assert by_stage[PipelineStage.VALIDATION].payload["decision"] == "VALIDATED"
        assert by_stage[PipelineStage.PERSISTENCE].payload["invoice_id"] == str(result.invoice_id)


# ---------------------------------------------------------------------------
# Review routing & vendor upsert
# ---------------------------------------------------------------------------


class TestRoutingAndUpsert:
    async def test_review_invoice_is_persisted_with_review_status(self, db_session):
        # Broken math → validation fails → REVIEW_REQUIRED, still persisted
        bad = extracted_invoice(
            line_items=[ExtractedLineItem(description="W", quantity=2.0,
                                          unit_price=5.0, line_total=18.9)]
        )
        result = await make_pipeline(structuring=FakeStructuring(bad)).process(
            db_session, **upload_kwargs()
        )

        assert result.decision is ProcessingDecision.REVIEW_REQUIRED
        assert result.document_status is DocumentStatus.REVIEW_REQUIRED
        invoice = await one(db_session, select(Invoice))
        assert invoice.status == "REVIEW_REQUIRED"  # persisted for the review workflow

    async def test_same_vendor_is_reused_across_invoices(self, db_session):
        pipeline = make_pipeline()
        first = await pipeline.process(db_session, **upload_kwargs("a"))

        second_invoice = extracted_invoice(invoice_number="INV-002")
        second = await make_pipeline(structuring=FakeStructuring(second_invoice)).process(
            db_session, **upload_kwargs("b")
        )

        assert first.vendor_created is True
        assert second.vendor_created is False
        assert first.vendor_id == second.vendor_id
        assert await one(db_session, select(func.count(Vendor.id))) == 1
        assert await one(db_session, select(func.count(Invoice.id))) == 2

    async def test_duplicate_upload_is_rejected_before_processing(self, db_session):
        pipeline = make_pipeline()
        first = await pipeline.process(db_session, **upload_kwargs("a"))

        with pytest.raises(DuplicateDocumentError) as exc_info:
            await pipeline.process(db_session, **upload_kwargs("a"))

        assert exc_info.value.detail["existing_document_id"] == str(first.document_id)
        assert await one(db_session, select(func.count(Document.id))) == 1


# ---------------------------------------------------------------------------
# Failure handling & rollback
# ---------------------------------------------------------------------------


class TestFailureHandling:
    async def test_ocr_failure_marks_document_failed(self, db_session):
        pipeline = make_pipeline(
            extraction=FakeExtraction(error=OCRExtractionError(message="unreadable scan"))
        )
        with pytest.raises(OCRExtractionError):
            await pipeline.process(db_session, **upload_kwargs())

        document = await one(db_session, select(Document))
        assert document.status == DocumentStatus.FAILED

        failure = await one(
            db_session, select(ProcessingLog).where(ProcessingLog.status == LogStatus.FAILURE)
        )
        assert failure.stage == PipelineStage.TEXT_EXTRACTION
        assert failure.payload["error_code"] == "ERR_OCR_FAILED"
        assert await one(db_session, select(func.count(Invoice.id))) == 0

    async def test_ai_failure_marks_document_failed_after_ocr_saved(self, db_session):
        pipeline = make_pipeline(
            structuring=FakeStructuring(error=AIStructuringError(message="model unavailable"))
        )
        with pytest.raises(AIStructuringError):
            await pipeline.process(db_session, **upload_kwargs())

        document = await one(db_session, select(Document))
        assert document.status == DocumentStatus.FAILED
        assert document.raw_ocr_text is not None  # earlier stage output retained

        failure = await one(
            db_session, select(ProcessingLog).where(ProcessingLog.status == LogStatus.FAILURE)
        )
        assert failure.stage == PipelineStage.AI_STRUCTURING

    async def test_unexpected_stage_error_still_reaches_failed_state(self, db_session):
        # A non-domain exception (e.g. provider misconfiguration raising
        # ValueError) must never leave the document stuck mid-pipeline.
        pipeline = make_pipeline(
            structuring=FakeStructuring(error=ValueError("OPENAI_API_KEY is not configured"))
        )
        with pytest.raises(AIStructuringError, match="Unexpected structuring failure"):
            await pipeline.process(db_session, **upload_kwargs())

        document = await one(db_session, select(Document))
        assert document.status == DocumentStatus.FAILED
        failure = await one(
            db_session, select(ProcessingLog).where(ProcessingLog.status == LogStatus.FAILURE)
        )
        assert failure.stage == PipelineStage.AI_STRUCTURING

    async def test_persistence_failure_rolls_back_atomically(self, db_session):
        # Second line item exceeds NUMERIC(12,4) quantity precision → DB error
        # inside the persistence transaction, after the invoice header flush.
        poison = extracted_invoice(
            line_items=[
                ExtractedLineItem(description="ok", quantity=1.0, unit_price=5.0, line_total=5.0),
                ExtractedLineItem(description="poison", quantity=10 ** 12,
                                  unit_price=1.0, line_total=5.0),
            ],
            subtotal=None, grand_total=5.0,
        )
        pipeline = make_pipeline(structuring=FakeStructuring(poison))

        with pytest.raises(DatabaseError):
            await pipeline.process(db_session, **upload_kwargs())

        # Nothing from the persistence transaction survived — no invoice,
        # no items, no vendor; document is FAILED with a failure log.
        assert await one(db_session, select(func.count(Invoice.id))) == 0
        assert await one(db_session, select(func.count(InvoiceItem.id))) == 0
        assert await one(db_session, select(func.count(Vendor.id))) == 0

        document = await one(db_session, select(Document))
        assert document.status == DocumentStatus.FAILED
        failure = await one(
            db_session, select(ProcessingLog).where(ProcessingLog.status == LogStatus.FAILURE)
        )
        assert failure.stage == PipelineStage.PERSISTENCE

    async def test_status_progression_is_visible_mid_pipeline(self, db_engine, db_session):
        """Stage commits make progress observable from a second session."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        observer_factory = async_sessionmaker(bind=db_engine, class_=AsyncSession)
        seen: list[str] = []

        class ObservingStructuring(FakeStructuring):
            async def structure_invoice(self, ocr_result, filename=""):
                async with observer_factory() as observer:
                    status = await observer.scalar(select(Document.status))
                    seen.append(status)
                return await super().structure_invoice(ocr_result, filename)

        await make_pipeline(structuring=ObservingStructuring()).process(
            db_session, **upload_kwargs()
        )
        assert seen == [DocumentStatus.AI_PROCESSING]


# ---------------------------------------------------------------------------
# Vendor edge cases
# ---------------------------------------------------------------------------


class TestVendorUpsert:
    async def test_vendor_without_tax_id_matches_by_name(self, db_session):
        no_tax = extracted_invoice(vendor=ExtractedVendor(name="Cash Vendor"))
        r1 = await make_pipeline(structuring=FakeStructuring(no_tax)).process(
            db_session, **upload_kwargs("a")
        )
        no_tax_2 = extracted_invoice(
            vendor=ExtractedVendor(name="Cash Vendor"), invoice_number="INV-002"
        )
        r2 = await make_pipeline(structuring=FakeStructuring(no_tax_2)).process(
            db_session, **upload_kwargs("b")
        )
        assert r1.vendor_id == r2.vendor_id
        assert await one(db_session, select(func.count(Vendor.id))) == 1

    async def test_invoice_without_vendor_persists_with_null_vendor(self, db_session):
        # Missing vendor fails validation → REVIEW_REQUIRED, but still persisted
        no_vendor = extracted_invoice(vendor=ExtractedVendor(name=None))
        result = await make_pipeline(structuring=FakeStructuring(no_vendor)).process(
            db_session, **upload_kwargs()
        )

        assert result.decision is ProcessingDecision.REVIEW_REQUIRED
        assert result.vendor_id is None
        invoice = await one(db_session, select(Invoice))
        assert invoice.vendor_id is None
        assert await one(db_session, select(func.count(Vendor.id))) == 0
