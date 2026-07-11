"""
Document Repository — app/repositories/document_repository.py

All database read/write operations for the Document aggregate.

Per the package convention (app/repositories/__init__.py): repositories
accept an AsyncSession and flush, but never commit — transaction
boundaries belong to the service layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.models.invoice import Invoice

# Whitelisted sort keys → ORDER BY expressions (never interpolate user input)
_HISTORY_SORT_COLUMNS = {
    "created_at": Document.created_at,
    "filename": Document.filename,
    "status": Document.status,
    "vendor": Invoice.vendor_name,
    "invoice_number": Invoice.invoice_number,
    "invoice_date": Invoice.invoice_date,
    "grand_total": Invoice.grand_total,
    "confidence": Invoice.composite_confidence,
}


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        filename: str,
        mime_type: str,
        file_size_bytes: int,
        file_path: str,
        file_hash: str,
    ) -> Document:
        """Create a new document in UPLOADED state."""
        document = Document(
            filename=filename,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            file_path=file_path,
            file_hash=file_hash,
            status=DocumentStatus.UPLOADED,
        )
        self._session.add(document)
        await self._session.flush()
        return document

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def get_by_hash(self, file_hash: str) -> Document | None:
        """Find an existing document with the same content hash (duplicate check)."""
        result = await self._session.execute(
            select(Document).where(Document.file_hash == file_hash).limit(1)
        )
        return result.scalar_one_or_none()

    async def set_status(self, document: Document, status: DocumentStatus) -> None:
        document.status = status
        await self._session.flush()

    async def store_extraction(
        self, document: Document, *, raw_ocr_text: str, source_type: str
    ) -> None:
        """Persist the text-extraction stage results onto the document."""
        document.raw_ocr_text = raw_ocr_text
        document.source_type = source_type
        document.status = DocumentStatus.OCR_COMPLETED
        await self._session.flush()

    async def search_history(
        self,
        *,
        search: str | None = None,
        status: DocumentStatus | None = None,
        sort_by: str = "created_at",
        descending: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[tuple[Document, Invoice | None]], int]:
        """
        Processing history: documents LEFT JOINed to their invoice, so
        failed documents (which never produce an invoice) still appear.
        """
        query = select(Document, Invoice).outerjoin(Invoice, Invoice.document_id == Document.id)

        if search:
            pattern = f"%{search.strip()}%"
            query = query.where(
                or_(
                    Document.filename.ilike(pattern),
                    Invoice.invoice_number.ilike(pattern),
                    Invoice.vendor_name.ilike(pattern),
                )
            )
        if status is not None:
            query = query.where(Document.status == status)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self._session.execute(count_query)).scalar_one()

        sort_column = _HISTORY_SORT_COLUMNS.get(sort_by, Document.created_at)
        order = sort_column.desc() if descending else sort_column.asc()
        query = (
            query.order_by(order, Document.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self._session.execute(query)).all()
        return [(document, invoice) for document, invoice in rows], total
