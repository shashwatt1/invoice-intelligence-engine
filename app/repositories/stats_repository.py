"""
Stats Repository — app/repositories/stats_repository.py

Cross-aggregate read queries powering the dashboard summary. Read-only.
"""

from __future__ import annotations

from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import INACTIVE_DOCUMENT_STATUSES, Document, DocumentStatus
from app.models.invoice import Invoice
from app.models.processing_log import PipelineStage, ProcessingLog
from app.repositories.document_repository import document_is_active

TERMINAL_STATUSES = (
    DocumentStatus.COMPLETED,
    DocumentStatus.REVIEW_REQUIRED,
    DocumentStatus.FAILED,
)


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def status_breakdown(self) -> dict[str, int]:
        rows = await self._session.execute(
            # Binned documents and reference copies are out of the workflow: never counted.
            select(Document.status, func.count(Document.id))
            .where(Document.status.notin_(INACTIVE_DOCUMENT_STATUSES)).group_by(Document.status)
        )
        return dict(rows.all())

    async def average_confidence(self) -> float | None:
        value = await self._session.scalar(
            select(func.avg(Invoice.composite_confidence)).where(document_is_active(Invoice.document_id)))
        return float(value) if value is not None else None

    async def average_processing_ms(self) -> float | None:
        """Mean end-to-end wall time for terminal documents (created→updated)."""
        value = await self._session.scalar(
            select(
                func.avg(extract("epoch", Document.updated_at - Document.created_at) * 1000)
            ).where(Document.status.in_(TERMINAL_STATUSES))
        )
        return float(value) if value is not None else None

    async def llm_usage_totals(self) -> tuple[int, float]:
        """(total tokens, total estimated USD) summed from AI-stage log payloads."""
        row = (
            await self._session.execute(
                select(
                    func.coalesce(
                        func.sum(ProcessingLog.payload["total_tokens"].as_integer()), 0
                    ),
                    func.coalesce(
                        func.sum(ProcessingLog.payload["estimated_cost_usd"].as_float()),
                        0.0,
                    ),
                ).where(
                    ProcessingLog.stage == PipelineStage.AI_STRUCTURING,
                    ProcessingLog.payload.isnot(None),
                )
            )
        ).one()
        return int(row[0]), float(row[1])
