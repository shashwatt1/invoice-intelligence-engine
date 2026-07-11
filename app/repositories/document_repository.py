"""
Document Repository — app/repositories/document_repository.py

All database read/write operations for the Document aggregate.

Per the package convention (app/repositories/__init__.py): repositories
accept an AsyncSession and flush, but never commit — transaction
boundaries belong to the service layer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus


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
