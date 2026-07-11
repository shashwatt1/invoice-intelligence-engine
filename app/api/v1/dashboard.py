"""
Dashboard Endpoints — app/api/v1/dashboard.py

    GET /dashboard/summary   Executive metrics + recent activity + health
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.mappers import to_history_row
from app.database.session import get_db
from app.models.document import DocumentStatus
from app.repositories.document_repository import DocumentRepository
from app.repositories.stats_repository import StatsRepository
from app.schemas.base import APIResponse
from app.schemas.processing import DashboardData

router = APIRouter(tags=["Dashboard"])


@router.get(
    "/dashboard/summary",
    response_model=APIResponse[DashboardData],
    summary="Executive dashboard summary",
    description=(
        "Processing volumes, success rate, average confidence and duration, "
        "LLM usage totals, status breakdown, and recent activity."
    ),
)
async def dashboard_summary(
    recent_limit: int = Query(default=8, ge=1, le=25),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[DashboardData]:
    stats = StatsRepository(db)

    breakdown = await stats.status_breakdown()
    completed = breakdown.get(DocumentStatus.COMPLETED, 0)
    review = breakdown.get(DocumentStatus.REVIEW_REQUIRED, 0)
    failed = breakdown.get(DocumentStatus.FAILED, 0)
    total = sum(breakdown.values())
    terminal = completed + review + failed

    recent_rows, _ = await DocumentRepository(db).search_history(
        page=1, page_size=recent_limit
    )
    total_tokens, total_cost = await stats.llm_usage_totals()

    data = DashboardData(
        total_documents=total,
        completed=completed,
        review_required=review,
        failed=failed,
        in_progress=total - terminal,
        success_rate=(completed / terminal) if terminal else None,
        average_confidence=await stats.average_confidence(),
        average_processing_ms=await stats.average_processing_ms(),
        total_tokens=total_tokens,
        total_estimated_cost_usd=round(total_cost, 6),
        status_breakdown=breakdown,
        recent=[to_history_row(document, invoice) for document, invoice in recent_rows],
    )
    return APIResponse(data=data)
