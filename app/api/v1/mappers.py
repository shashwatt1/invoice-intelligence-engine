"""
ORM → API Schema Mappers — app/api/v1/mappers.py

Small pure functions converting ORM rows into API payload models.
Kept out of the endpoint modules so list/detail/dashboard endpoints
share one mapping and cannot drift apart.
"""

from __future__ import annotations

from app.models.document import Document
from app.models.invoice import Invoice
from app.models.processing_log import ProcessingLog
from app.schemas.processing import HistoryRow, StageEntry


def to_history_row(document: Document, invoice: Invoice | None) -> HistoryRow:
    return HistoryRow(
        document_id=document.id,
        invoice_id=invoice.id if invoice else None,
        filename=document.filename,
        status=document.status,
        vendor_name=invoice.vendor_name if invoice else None,
        invoice_number=invoice.invoice_number if invoice else None,
        invoice_date=invoice.invoice_date if invoice else None,
        grand_total=float(invoice.grand_total)
        if invoice and invoice.grand_total is not None
        else None,
        currency=invoice.currency if invoice else None,
        composite_confidence=float(invoice.composite_confidence)
        if invoice and invoice.composite_confidence is not None
        else None,
        source_type=document.source_type,
        created_at=document.created_at,
    )


def to_stage_entry(log: ProcessingLog, include_payload: bool = False) -> StageEntry:
    return StageEntry(
        stage=log.stage,
        status=log.status,
        message=log.message,
        duration_ms=log.duration_ms,
        created_at=log.created_at,
        payload=log.payload if include_payload else None,
    )
