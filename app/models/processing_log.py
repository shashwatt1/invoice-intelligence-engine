"""
ProcessingLog Model — app/models/processing_log.py

Records every stage transition in the document processing pipeline.

Design decisions:
- Named `ProcessingLog` (not `AuditLog`) because these entries track
  automated pipeline stages, not human edits. User-level audit logs
  will be a separate table in a future sprint.
- `stage` captures what happened: UPLOADED, OCR_STARTED, OCR_COMPLETE,
  AI_STRUCTURING_STARTED, AI_STRUCTURED, VALIDATED, FAILED_OCR, etc.
- `payload` is a JSONB column for stage-specific metadata (e.g. OCR
  duration_ms, token counts, error details). Flexible enough for any
  stage without needing extra columns.
- `duration_ms` tracks how long each stage took — essential for
  performance monitoring and cost analysis.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, UUIDPrimaryKeyMixin


class PipelineStage(StrEnum):
    """Pipeline stages recorded in the processing log."""

    UPLOAD = "UPLOAD"
    TEXT_EXTRACTION = "TEXT_EXTRACTION"
    AI_STRUCTURING = "AI_STRUCTURING"
    VALIDATION = "VALIDATION"
    PERSISTENCE = "PERSISTENCE"


class LogStatus(StrEnum):
    """Outcome of a logged pipeline stage."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class ProcessingLog(Base, UUIDPrimaryKeyMixin):
    """
    Audit trail entry for a single pipeline stage.

    One document can have many processing logs — one per stage it passes through.
    """

    __tablename__ = "processing_logs"

    # Foreign key to parent document
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        doc="The document being processed.",
    )

    # Stage information
    stage: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        doc=(
            "Pipeline stage: UPLOADED, OCR_STARTED, OCR_COMPLETE, "
            "AI_STRUCTURING_STARTED, AI_STRUCTURED, VALIDATION_STARTED, "
            "VALIDATED, PERSISTED, FAILED_OCR, FAILED_AI, FAILED_VALIDATION."
        ),
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'SUCCESS'"),
        doc="Outcome of this stage: SUCCESS, FAILURE, SKIPPED.",
    )

    # Stage metadata
    message: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="Human-readable description or error message."
    )
    payload: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, doc="Stage-specific metadata (tokens, duration, errors, etc.)."
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, doc="How long this stage took in milliseconds."
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        doc="When this log entry was recorded.",
    )

    # Relationships
    document = relationship("Document", back_populates="processing_logs")

    __table_args__ = (
        Index("idx_processing_logs_document_id", "document_id"),
        Index("idx_processing_logs_stage", "stage"),
        Index("idx_processing_logs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ProcessingLog id={self.id} document_id={self.document_id} "
            f"stage={self.stage!r} status={self.status!r}>"
        )
