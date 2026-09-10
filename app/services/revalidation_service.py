"""
Revalidation Service — app/services/revalidation_service.py

Re-runs validation over an invoice as it is stored, after a person has
corrected a line item. No OCR, no LLM call, no reprocessing: the
deterministic checks are simply applied again to the corrected values.

Why this exists
---------------
Extraction can leave a handful of values unassociated — Google Vision
interleaves the description and price columns in parts of a receipt, and
the model correctly reports what it cannot place as null rather than
guessing. A person supplies those few figures, and the invoice then has
to be judged again. Reprocessing the document would re-run OCR and the
model, cost money, and could change values the person did not touch.

What it reuses, deliberately
----------------------------
The same check functions and the same confidence scorer the pipeline
uses, in the same order. A correction cannot be judged by a laxer
standard than the original extraction, so there is no second rule set to
drift apart from the first.

Normalization is skipped because it converts an LLM payload into typed
values, and the persisted row is already typed. Reconciliation IS
re-run: it is idempotent on already-reconciled values (a unit price that
is already net no longer satisfies the gross-to-net identities, so no
rule fires a second time) and re-running keeps the deposit-explains-the-
difference results in the report.

The OCR and AI confidence components are carried forward from the
previous validation log — a human correcting a figure does not change
how well the scanner read the page or how sure the model was. Only the
validation component is recomputed, which is exactly what the correction
affects.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.processing_log import LogStatus, PipelineStage
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.schemas.normalized import NormalizedInvoice, NormalizedLineItem
from app.services.validation.checks import (
    check_date_order,
    check_grand_total_math,
    check_line_item_math,
    check_required_fields,
    check_subtotal,
    check_tax_consistency,
)
from app.services.validation.confidence import compute_confidence
from app.services.validation.reconciliation import reconcile_invoice
from app.services.validation.report import (
    CheckStatus,
    ProcessingDecision,
    ValidationReport,
)
from app.services.validation.service import ValidationService

logger = structlog.get_logger(__name__)


def normalized_from_persisted(invoice: Invoice) -> NormalizedInvoice:
    """The stored invoice expressed as the schema the checks consume."""
    return NormalizedInvoice(
        vendor_name=invoice.vendor.name if invoice.vendor else invoice.vendor_name,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        subtotal=invoice.subtotal,
        tax_amount=invoice.tax_amount,
        discount_amount=invoice.discount_amount,
        deposit_total=getattr(invoice, "deposit_total", None),
        fuel_surcharge=getattr(invoice, "fuel_surcharge", None),
        grand_total=invoice.grand_total,
        line_items=tuple(
            NormalizedLineItem(
                description=item.description,
                product_code=item.product_sku,
                pack_size=item.pack_size,
                quantity=item.quantity,
                unit_price=item.unit_price,
                unit_discount=item.discount,
                unit_deposit=item.deposit,
                line_total=item.line_total,
                tax_rate=item.tax_rate,
                sort_order=item.sort_order,
            )
            for item in sorted(invoice.items, key=lambda i: i.sort_order)
        ),
    )


def _previous_confidence_parts(
    payload: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    """OCR and AI confidence from the last validation report, if any."""
    confidence = (payload or {}).get("confidence") or {}
    return confidence.get("ocr_confidence"), confidence.get("ai_confidence")


def build_report(
    invoice: Invoice,
    *,
    ocr_confidence: float | None,
    ai_confidence: float | None,
    tolerance: Decimal | None = None,
    threshold: float | None = None,
) -> ValidationReport:
    """Re-judge the stored invoice with the pipeline's own rules."""
    service = ValidationService()
    tolerance = tolerance if tolerance is not None else service._tolerance
    threshold = threshold if threshold is not None else service._threshold

    start = time.monotonic()
    normalized = normalized_from_persisted(invoice)
    normalized, checks = reconcile_invoice(normalized, tolerance)
    checks += check_required_fields(normalized)
    checks += check_line_item_math(normalized, tolerance)
    checks += check_subtotal(normalized, tolerance)
    checks += check_grand_total_math(normalized, tolerance)
    checks += check_tax_consistency(normalized, tolerance)
    checks += check_date_order(normalized)

    confidence = compute_confidence(ocr_confidence, ai_confidence, checks)
    failed = [c for c in checks if c.status is CheckStatus.FAILED]
    review_reasons = [f"{c.name}: {c.message or 'check failed'}" for c in failed]
    if confidence.composite < threshold:
        review_reasons.append(
            f"Composite confidence {confidence.composite:.4f} is below "
            f"the {threshold} review threshold."
        )

    return ValidationReport(
        checks=checks,
        confidence=confidence,
        decision=(
            ProcessingDecision.VALIDATED
            if not review_reasons
            else ProcessingDecision.REVIEW_REQUIRED
        ),
        review_reasons=review_reasons,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


async def revalidate_invoice(session: AsyncSession, invoice: Invoice) -> ValidationReport:
    """
    Re-judge a corrected invoice and record the result.

    Updates the invoice's status and composite confidence, and appends a
    VALIDATION log entry. The detail endpoint reads the latest entry per
    stage, so the new report replaces the old one in the UI without any
    change there. Flushes; the caller owns the transaction.
    """
    logs = ProcessingLogRepository(session)
    previous = [
        log.payload
        for log in await logs.for_document(invoice.document_id)
        if log.stage == PipelineStage.VALIDATION and log.payload
    ]
    ocr_confidence, ai_confidence = _previous_confidence_parts(
        previous[-1] if previous else None
    )

    report = build_report(
        invoice, ocr_confidence=ocr_confidence, ai_confidence=ai_confidence
    )

    invoice.status = report.decision.value
    invoice.composite_confidence = Decimal(str(report.confidence.composite))
    await session.flush()

    await logs.add(
        document_id=invoice.document_id,
        stage=PipelineStage.VALIDATION,
        status=LogStatus.SUCCESS,
        message="Revalidated after a manual line-item correction.",
        payload=report.to_dict(),
        duration_ms=report.duration_ms,
    )

    logger.info(
        "invoice_revalidated",
        invoice_id=str(invoice.id),
        decision=report.decision.value,
        composite_confidence=report.confidence.composite,
        failed=len(report.failed_checks),
    )
    return report
