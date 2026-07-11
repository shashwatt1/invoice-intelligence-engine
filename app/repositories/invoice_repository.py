"""
Invoice Repository — app/repositories/invoice_repository.py

Creates the invoice header and its line items from the validation
engine's canonical NormalizedInvoice, wiring in the AI-stage artifacts
(raw extraction JSON, model, confidence) for traceability.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.schemas.normalized import NormalizedInvoice
from app.services.validation.report import ProcessingDecision


class InvoiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_with_items(
        self,
        *,
        document_id: uuid.UUID,
        vendor_id: uuid.UUID | None,
        normalized: NormalizedInvoice,
        decision: ProcessingDecision,
        composite_confidence: float,
        extraction_model: str | None,
        raw_extraction_json: dict[str, Any] | None,
    ) -> Invoice:
        """Persist the invoice header and all line items (flush, no commit)."""
        invoice = Invoice(
            document_id=document_id,
            vendor_id=vendor_id,
            invoice_number=normalized.invoice_number,
            invoice_date=normalized.invoice_date,
            due_date=normalized.due_date,
            subtotal=normalized.subtotal,
            tax_amount=normalized.tax_amount,
            discount_amount=normalized.discount_amount,
            grand_total=normalized.grand_total,
            currency=normalized.currency or "USD",
            vendor_name=normalized.vendor_name,
            vendor_tax_id=normalized.vendor_tax_id,
            vendor_address=normalized.vendor_address,
            status=decision.value,
            composite_confidence=Decimal(str(composite_confidence)),
            extraction_model=extraction_model,
            raw_extraction_json=raw_extraction_json,
        )
        self._session.add(invoice)
        await self._session.flush()

        for item in normalized.line_items:
            self._session.add(
                InvoiceItem(
                    invoice_id=invoice.id,
                    description=item.description or "(no description)",
                    quantity=item.quantity if item.quantity is not None else Decimal("0"),
                    unit_price=item.unit_price if item.unit_price is not None else Decimal("0"),
                    line_total=item.line_total if item.line_total is not None else Decimal("0"),
                    tax_rate=item.tax_rate,
                    sort_order=item.sort_order,
                )
            )
        await self._session.flush()
        return invoice

    async def get(self, invoice_id: uuid.UUID) -> Invoice | None:
        return await self._session.get(Invoice, invoice_id)

    async def get_detail(self, invoice_id: uuid.UUID) -> Invoice | None:
        """Invoice with items, vendor, and document eagerly loaded."""
        result = await self._session.execute(
            select(Invoice)
            .where(Invoice.id == invoice_id)
            .options(
                selectinload(Invoice.items),
                selectinload(Invoice.vendor),
                selectinload(Invoice.document),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_document(self, document_id: uuid.UUID) -> Invoice | None:
        result = await self._session.execute(
            select(Invoice).where(Invoice.document_id == document_id).limit(1)
        )
        return result.scalar_one_or_none()
