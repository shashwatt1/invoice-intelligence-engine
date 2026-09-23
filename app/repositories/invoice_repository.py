"""
Invoice Repository — app/repositories/invoice_repository.py

Creates the invoice header and its line items from the validation
engine's canonical NormalizedInvoice, wiring in the AI-stage artifacts
(raw extraction JSON, model, confidence) for traceability.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.schemas.normalized import NormalizedInvoice, NormalizedLineItem
from app.services.validation.report import ProcessingDecision


class InvoiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_with_items(
        self,
        *,
        document_id: uuid.UUID,
        store_id: uuid.UUID | None,
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
            store_id=store_id,
            vendor_id=vendor_id,
            invoice_number=normalized.invoice_number,
            invoice_date=normalized.invoice_date,
            due_date=normalized.due_date,
            subtotal=normalized.subtotal,
            tax_amount=normalized.tax_amount,
            discount_amount=normalized.discount_amount,
            deposit_total=normalized.deposit_total,
            fuel_surcharge=normalized.fuel_surcharge,
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
            self._session.add(self._build_item(invoice.id, item))
        await self._session.flush()
        return invoice

    @staticmethod
    def _entry(field: str, old: Any, new: Any, by: str | None, note: str | None) -> dict[str, Any]:
        def plain(v: Any) -> Any:
            if isinstance(v, Decimal):
                return float(v)
            if isinstance(v, date):
                return v.isoformat()
            return v
        return {"field": field, "old": plain(old), "new": plain(new), "by": by,
                "at": datetime.now(UTC).isoformat(), "note": note}

    async def _item(self, invoice_id: uuid.UUID, sort_order: int) -> InvoiceItem | None:
        result = await self._session.execute(
            select(InvoiceItem).where(
                InvoiceItem.invoice_id == invoice_id,
                InvoiceItem.sort_order == sort_order,
            )
        )
        return result.scalar_one_or_none()

    async def correct_item(
        self,
        invoice_id: uuid.UUID,
        sort_order: int,
        updates: dict[str, Any],
        *,
        corrected_by: str | None = None,
        note: str | None = None,
    ) -> InvoiceItem | None:
        """
        Replace values on one line item with figures a person supplied,
        recording which fields they touched and — per field — what the
        value was, what it became, who, when and why.

        Only the caller-supplied fields change; everything else on the row
        keeps its extracted value. `corrected_fields` accumulates rather
        than overwrites, so correcting a price today and a quantity
        tomorrow leaves both marked. Flushes, never commits.
        """
        item = await self._item(invoice_id, sort_order)
        if item is None:
            return None

        from app.schemas.processing import LineItemCorrection

        history = list(item.correction_history or [])
        for field, value in updates.items():
            column = LineItemCorrection.COLUMNS.get(field, field)
            history.append(self._entry(field, getattr(item, column), value, corrected_by, note))
            setattr(item, column, value)
        item.corrected_fields = sorted(set(item.corrected_fields or []) | set(updates))
        item.correction_history = history
        await self._session.flush()
        return item

    async def add_item(
        self,
        invoice: Invoice,
        *,
        description: str,
        product_code: str | None,
        pack_size: str | None,
        quantity: Decimal,
        unit_price: Decimal | None,
        unit_deposit: Decimal | None,
        unit_discount: Decimal | None,
        line_total: Decimal | None,
        added_by: str,
        note: str | None,
    ) -> InvoiceItem:
        """
        A row a person adds because the photos missed it. Appended after
        the last row, typed 'manual', every given value marked as the
        person's and the addition recorded in the row's history. Nothing
        here touches master data. Flushes, never commits.
        """
        next_order = max((i.sort_order for i in invoice.items), default=-1) + 1
        given = {k: v for k, v in (("description", description), ("product_code", product_code),
                                   ("quantity", quantity), ("unit_price", unit_price),
                                   ("unit_deposit", unit_deposit), ("unit_discount", unit_discount),
                                   ("line_total", line_total)) if v is not None}
        item = InvoiceItem(
            invoice_id=invoice.id, description=description, product_sku=product_code, pack_size=pack_size,
            quantity=quantity, unit_price=unit_price, deposit=unit_deposit, discount=unit_discount,
            line_total=line_total, line_type="product", sort_order=next_order,
            entry_source="manual", corrected_fields=sorted(given),
            correction_history=[self._entry("row", None, "added", added_by, note)],
        )
        self._session.add(item)
        invoice.items.append(item)
        await self._session.flush()
        return item

    async def void_item(
        self, invoice_id: uuid.UUID, sort_order: int, *, voided_by: str, note: str | None
    ) -> InvoiceItem | None:
        """
        A row a person says is not on the invoice. Kept for audit, typed
        'voided': out of the subtotal, out of the PDI export. Flushes only.
        """
        item = await self._item(invoice_id, sort_order)
        if item is None:
            return None
        history = list(item.correction_history or [])
        history.append(self._entry("line_type", item.line_type, "voided", voided_by, note))
        item.line_type = "voided"
        item.correction_history = history
        await self._session.flush()
        return item

    async def correct_totals(
        self, invoice: Invoice, updates: dict[str, Decimal], *, corrected_by: str, note: str | None
    ) -> Invoice:
        """
        Replace printed header figures with what a person read off the
        document. Each field's extracted value goes into the history
        before it is replaced. Flushes only.
        """
        history = list(invoice.correction_history or [])
        for field, value in updates.items():
            history.append(self._entry(field, getattr(invoice, field), value, corrected_by, note))
            setattr(invoice, field, value)
        invoice.corrected_fields = sorted(set(invoice.corrected_fields or []) | set(updates))
        invoice.correction_history = history
        await self._session.flush()
        return invoice

    async def correct_invoice_date(
        self, invoice: Invoice, value: date | None, *, corrected_by: str, note: str | None
    ) -> Invoice:
        """
        Replace the invoice date with what a person read off the document,
        or record that it is unknown (None). The extracted value goes into
        the history before it is replaced; nothing is ever inferred from
        upload, processing or file dates. Flushes only.
        """
        history = list(invoice.correction_history or [])
        history.append(self._entry("invoice_date", invoice.invoice_date, value, corrected_by, note))
        invoice.invoice_date = value
        invoice.corrected_fields = sorted(set(invoice.corrected_fields or []) | {"invoice_date"})
        invoice.correction_history = history
        await self._session.flush()
        return invoice

    @staticmethod
    def _build_item(invoice_id: uuid.UUID, item: NormalizedLineItem) -> InvoiceItem:
        """One persisted row from one canonical line item."""
        return InvoiceItem(
            invoice_id=invoice_id,
            description=item.description or "(no description)",
            product_sku=item.product_code,
            quantity=item.quantity if item.quantity is not None else Decimal("0"),
            # unit_price is NOT coerced to zero. When extraction reports null it
            # is saying "I could not read this", and a stored 0.00 would be
            # indistinguishable from a genuine zero price while also passing
            # quantity x unit_price = line_total trivially — so validation would
            # never flag it and the EDI would carry a 000000 case cost. Observed
            # on Rocco J. Testani 228245, rows 32 and 34, where the model
            # correctly returned null at confidence 0.5 and persistence
            # overwrote that with an apparently confident $0.00.
            unit_price=item.unit_price,
            line_total=item.line_total,
            tax_rate=item.tax_rate,
            pack_size=item.pack_size,
            line_type=item.line_type,
            discount=item.unit_discount,
            deposit=item.unit_deposit,
            sort_order=item.sort_order,
            source_pages=list(item.source_pages) or None,
            duplicate_candidate=(
                {"of_sort_order": item.possible_duplicate_of,
                 "reason": item.duplicate_reason, "resolution": None}
                if item.possible_duplicate_of is not None else None
            ),
        )

    async def replace_extraction(
        self,
        invoice: Invoice,
        *,
        vendor_id: uuid.UUID | None,
        normalized: NormalizedInvoice,
        decision: ProcessingDecision,
        composite_confidence: float,
        extraction_model: str | None,
        raw_extraction_json: dict[str, Any] | None,
    ) -> Invoice:
        """
        Replace the operational extraction result on an EXISTING invoice.

        The invoice keeps its id, its document and everything pointing at
        it (proposals carry invoice_id); only what the pipeline derives
        from the document is rewritten. Line items are replaced outright
        rather than merged, so a row the new extraction does not contain
        cannot linger — `delete-orphan` on the relationship removes the
        old rows in the same transaction. Flushes; the caller commits.

        Correction history is NOT cleared here: callers must refuse to
        reprocess an invoice a person has corrected, because replacing
        the values those corrections were made against would leave the
        history describing figures that are no longer on the record.
        """
        invoice.vendor_id = vendor_id
        invoice.invoice_number = normalized.invoice_number
        invoice.invoice_date = normalized.invoice_date
        invoice.due_date = normalized.due_date
        invoice.subtotal = normalized.subtotal
        invoice.tax_amount = normalized.tax_amount
        invoice.discount_amount = normalized.discount_amount
        invoice.deposit_total = normalized.deposit_total
        invoice.fuel_surcharge = normalized.fuel_surcharge
        invoice.grand_total = normalized.grand_total
        invoice.currency = normalized.currency or "USD"
        invoice.vendor_name = normalized.vendor_name
        invoice.vendor_tax_id = normalized.vendor_tax_id
        invoice.vendor_address = normalized.vendor_address
        invoice.status = decision.value
        invoice.composite_confidence = Decimal(str(composite_confidence))
        invoice.extraction_model = extraction_model
        invoice.raw_extraction_json = raw_extraction_json

        invoice.items.clear()
        await self._session.flush()
        for item in normalized.line_items:
            invoice.items.append(self._build_item(invoice.id, item))
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
