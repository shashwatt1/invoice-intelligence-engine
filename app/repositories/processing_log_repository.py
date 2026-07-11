"""
Processing Log Repository — app/repositories/processing_log_repository.py

Appends audit-trail entries for pipeline stages. Payloads are JSONB and
carry stage-specific observability (OCR metrics, LLM metadata, the full
validation report, persistence identifiers, or failure details).
"""

from __future__ import annotations

import uuid
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
        """Append one stage entry (flush, no commit)."""
        entry = ProcessingLog(
            document_id=document_id,
            stage=stage,
            status=status,
            message=message,
            payload=payload,
            duration_ms=duration_ms,
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
