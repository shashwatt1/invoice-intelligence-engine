"""
Persistence Service — app/services/persistence_service.py

The transactional write that begins immediately after validation:

    vendor upsert → invoice + line items → final document status
    → PERSISTENCE log → COMMIT (one atomic transaction)

Design decisions:
- Everything in this service is a single transaction: a partially
  persisted invoice (header without items, invoice without status
  update) can never be observed. The caller is responsible for making
  sure prior stage work was committed before entering.
- Final document status derives from the validation decision:
  VALIDATED → COMPLETED (fully automatic processing), REVIEW_REQUIRED
  stays REVIEW_REQUIRED — the invoice is still persisted so reviewers
  can inspect and correct it (the enterprise review workflow).
- The invoice row stores the AI-stage artifacts (raw extraction JSON,
  model, composite confidence) for traceability and reprocessing.
"""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.document import Document, DocumentStatus
from app.models.invoice import Invoice
from app.models.processing_log import PipelineStage
from app.models.vendor import Vendor
from app.repositories.document_repository import DocumentRepository
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.vendor_repository import VendorRepository
from app.services.structuring_service import InvoiceStructuringResult
from app.services.validation.report import ProcessingDecision, ValidationResult

logger = get_logger(__name__)


class PersistenceService:
    """Atomic persistence of a validated (or review-routed) invoice."""

    async def persist_invoice(
        self,
        session: AsyncSession,
        *,
        document: Document,
        structuring: InvoiceStructuringResult,
        validation: ValidationResult,
    ) -> tuple[Invoice, Vendor | None, bool]:
        """
        Persist vendor, invoice, and line items in one transaction.

        Returns:
            (invoice, vendor, vendor_created). vendor is None when no
            vendor name was extracted.

        Raises:
            Any database error — the caller must roll back and mark the
            document FAILED; nothing is committed on failure.
        """
        start = time.monotonic()
        documents = DocumentRepository(session)
        vendors = VendorRepository(session)
        invoices = InvoiceRepository(session)
        logs = ProcessingLogRepository(session)

        decision = validation.report.decision
        vendor, vendor_created = await vendors.get_or_create(validation.invoice)

        invoice = await invoices.create_with_items(
            document_id=document.id,
            vendor_id=vendor.id if vendor else None,
            normalized=validation.invoice,
            decision=decision,
            composite_confidence=validation.report.confidence.composite,
            extraction_model=structuring.metadata.model,
            raw_extraction_json=structuring.raw_response,
        )

        final_status = (
            DocumentStatus.COMPLETED
            if decision is ProcessingDecision.VALIDATED
            else DocumentStatus.REVIEW_REQUIRED
        )
        await documents.set_status(document, final_status)

        duration_ms = int((time.monotonic() - start) * 1000)
        await logs.add(
            document_id=document.id,
            stage=PipelineStage.PERSISTENCE,
            message=f"Invoice persisted; document {final_status}.",
            payload={
                "invoice_id": str(invoice.id),
                "vendor_id": str(vendor.id) if vendor else None,
                "vendor_created": vendor_created,
                "line_item_count": len(validation.invoice.line_items),
                "final_status": final_status.value,
            },
            duration_ms=duration_ms,
        )

        await session.commit()

        logger.info(
            "invoice_persisted",
            document_id=str(document.id),
            invoice_id=str(invoice.id),
            vendor_id=str(vendor.id) if vendor else None,
            vendor_created=vendor_created,
            final_status=final_status.value,
            duration_ms=duration_ms,
        )
        return invoice, vendor, vendor_created
