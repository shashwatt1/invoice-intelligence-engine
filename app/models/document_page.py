"""
DocumentPage Model — app/models/document_page.py

One photograph (or file) of a multi-photo invoice intake.

A person photographing a long invoice takes several overlapping shots.
Those shots are ONE document — one intake, one OCR context, one LLM
extraction, one invoice. A Document is that intake; a DocumentPage is
one of its photos, in the order the operator gave them, carrying its own
file, its own OCR text and its own OCR confidence so every extracted
line can say which photo(s) it was read from.

Design decisions
----------------
- A single-file upload is a Document with one page. Documents that
  predate this table have no page rows; the Document's own
  filename/file_path/file_hash still describe their one file.
- The Document's file_hash for a multi-photo intake is the hash of the
  ordered page hashes, so the existing duplicate check keeps working;
  each page's own hash is also stored so a photo already used in another
  intake is refused.
- raw_ocr_text here is the page's OWN text. The Document's raw_ocr_text
  is the combined, page-separated context the model received.
- Deleting the document deletes its pages (FK cascade), like its logs.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DocumentPage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One photo of an invoice intake, in operator order."""

    __tablename__ = "document_pages"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="1-based, in the order the operator uploaded the photos."
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    raw_ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True, doc="This page's own text.")
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mean_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    document = relationship("Document", back_populates="pages")

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_pages_document_page"),
        Index("idx_document_pages_file_hash", "file_hash"),
    )

    def __repr__(self) -> str:
        return f"<DocumentPage {self.document_id} p{self.page_number} {self.filename!r}>"
