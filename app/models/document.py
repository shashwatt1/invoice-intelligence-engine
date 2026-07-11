"""
Document Model — app/models/document.py

Represents an uploaded file before it becomes an invoice.
A document enters the system at upload time with status UPLOADED.
It progresses through OCR_COMPLETE → AI_STRUCTURED → VALIDATED → COMPLETE,
or may end up in FAILED_OCR / FAILED_AI / REVIEW states.

Design decisions:
- Separate from the Invoice table. A document exists the moment a file
  is uploaded. An invoice only exists after successful AI extraction.
  This avoids dozens of nullable columns on a single mega-table.
- `file_hash` is indexed per-organization for duplicate detection
  (per requirements.md §8.2).
- Status is stored as a VARCHAR, not an enum, to allow adding new
  statuses without a migration (design.md recommendation).
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DocumentStatus(StrEnum):
    """
    Document lifecycle states (stored as VARCHAR — no migration needed
    to evolve). Progression:

        UPLOADED → OCR_IN_PROGRESS → OCR_COMPLETED → AI_PROCESSING
                 → VALIDATED | REVIEW_REQUIRED → COMPLETED
        Any stage → FAILED (ProcessingLog records which stage and why)
    """

    UPLOADED = "UPLOADED"
    OCR_IN_PROGRESS = "OCR_IN_PROGRESS"
    OCR_COMPLETED = "OCR_COMPLETED"
    AI_PROCESSING = "AI_PROCESSING"
    VALIDATED = "VALIDATED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Tracks an uploaded file through the processing pipeline.

    Lifecycle:
        UPLOADED → OCR_COMPLETE → AI_STRUCTURED → VALIDATED → COMPLETE
                 → FAILED_OCR / FAILED_AI / REVIEW (terminal or retry states)
    """

    __tablename__ = "documents"

    # Original upload metadata
    filename: Mapped[str] = mapped_column(
        String(500), nullable=False, doc="Original filename as provided by the client."
    )
    mime_type: Mapped[str] = mapped_column(
        String(100), nullable=False, doc="MIME type of the uploaded file."
    )
    file_size_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Size of the uploaded file in bytes."
    )
    file_path: Mapped[str] = mapped_column(
        Text, nullable=False, doc="Storage path (local or cloud URL) to the raw file."
    )
    file_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="SHA-256 hex digest for duplicate detection."
    )

    # Processing state
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'UPLOADED'"),
        doc=(
            "Current pipeline state: UPLOADED, OCR_COMPLETE, AI_STRUCTURED, "
            "VALIDATED, COMPLETE, FAILED_OCR, FAILED_AI, REVIEW."
        ),
    )

    # OCR / extraction results (populated by Milestone 2)
    raw_ocr_text: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Raw text extracted by OCR or pdfplumber."
    )
    source_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True, doc="Extraction method: 'digital_pdf' or 'ocr'."
    )

    # Relationship to processing logs
    processing_logs = relationship(
        "ProcessingLog", back_populates="document", cascade="all, delete-orphan"
    )

    # Relationship to invoice (one document → zero or one invoice)
    invoice = relationship(
        "Invoice", back_populates="document", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_documents_file_hash", "file_hash"),
        Index("idx_documents_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.filename!r} status={self.status!r}>"
