"""
Processing Log Repository — app/repositories/processing_log_repository.py

Appends audit-trail entries for pipeline stages. Payloads are JSONB and
carry stage-specific observability (OCR metrics, LLM metadata, the full
validation report, persistence identifiers, or failure details).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.processing_log import LogStatus, PipelineStage, ProcessingLog


class ProcessingLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        document_id: uuid.UUID,
        stage: PipelineStage,
        status: LogStatus = LogStatus.SUCCESS,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> ProcessingLog:
        """
        Append one stage entry (flush, no commit).

        `created_at` is set here rather than left to the column's
        server_default. In Postgres that default is now(), which is
        TRANSACTION start time, so every entry written in one transaction
        shared a timestamp to the microsecond — and `for_document`, which
        orders by (created_at, id), then fell back to a random UUID. The
        audit trail could show a rule's correction before the manual
        correction that triggered it. A per-insert timestamp keeps the log
        in the order the stages actually happened.
        """
        entry = ProcessingLog(
            document_id=document_id,
            stage=stage,
            status=status,
            message=message,
            payload=payload,
            duration_ms=duration_ms,
            created_at=datetime.now(UTC),
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def for_document(self, document_id: uuid.UUID) -> list[ProcessingLog]:
        """All log entries for a document in chronological order."""
        result = await self._session.execute(
            select(ProcessingLog)
            .where(ProcessingLog.document_id == document_id)
            .order_by(ProcessingLog.created_at, ProcessingLog.id)
        )
        return list(result.scalars())
