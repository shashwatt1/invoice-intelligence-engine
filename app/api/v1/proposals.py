"""
Master-data review API — app/api/v1/proposals.py

A read-and-decide surface over product_data_proposals for the review UI.

Every business rule lives in app.services.proposal_service: this module
lists, shows, and forwards decisions. It has no path to
product_case_mappings of its own — approve() is the only writer, and it
is the same function the review CLI calls.

No authentication yet. `reviewed_by` is recorded as given, the same
contract as the CLI's --by; it is a name on the record, not a proof of
identity, and the UI says so.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError, ValidationError
from app.database.session import get_db
from app.models.product_data_proposal import (
    STATUS_PENDING,
    VALID_SOURCES,
    VALID_STATUSES,
    ProductDataProposal,
)
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import (
    ProductDataProposalRepository,
    ProposalImmutableError,
)
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.processing import (
    ProductHistory,
    ProposalDecision,
    ProposalDecisionResult,
    ProposalDetail,
    ProposalRow,
    ResultingMapping,
)
from app.services import proposal_service
from app.services.export_service import normalize_item_code

router = APIRouter(tags=["Master data review"])


def _row(p: ProductDataProposal) -> ProposalRow:
    return ProposalRow.model_validate(p, from_attributes=True)


async def _detail(db: AsyncSession, p: ProductDataProposal) -> ProposalDetail:
    mapping = await ProductCaseMappingRepository(db).get(p.entity_key)
    resulting = None
    if mapping is not None and mapping.approved_proposal_id == p.id:
        resulting = ResultingMapping.model_validate(mapping, from_attributes=True)
    detail = ProposalDetail.model_validate(p, from_attributes=True)
    detail.resulting_mapping = resulting
    detail.current_master_value = mapping.units_per_case if mapping else None
    return detail


@router.get(
    "/proposals",
    response_model=PaginatedResponse[ProposalRow],
    summary="The master-data review queue",
    description=(
        "Every proposal, newest first, PENDING by default. Filter by status, source, "
        "item code, or invoice. This is the persistent, immutable review history — "
        "a decision is never edited, a changed value is a new proposal."
    ),
)
async def list_proposals(
    status: str | None = Query(default=STATUS_PENDING),
    source: str | None = Query(default=None),
    item_code: str | None = Query(default=None),
    invoice_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ProposalRow]:
    if status and status != "ALL" and status not in VALID_STATUSES:
        raise ValidationError(message=f"status must be one of {sorted(VALID_STATUSES)} or ALL.")
    if source and source not in VALID_SOURCES:
        raise ValidationError(message=f"source must be one of {sorted(VALID_SOURCES)}.")

    rows = await ProductDataProposalRepository(db).list(
        status=None if status in (None, "ALL") else status,
        source=source,
        entity_key=normalize_item_code(item_code) if item_code else None,
        invoice_id=invoice_id,
    )
    rows = list(reversed(rows))                      # newest first for a queue
    start = (page - 1) * page_size
    return PaginatedResponse(
        items=[_row(p) for p in rows[start:start + page_size]],
        total=len(rows), page=page, page_size=page_size,
    )


@router.get(
    "/proposals/{proposal_id}",
    response_model=APIResponse[ProposalDetail],
    summary="One proposal with its full evidence",
)
async def get_proposal(
    proposal_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> APIResponse[ProposalDetail]:
    p = await ProductDataProposalRepository(db).get(proposal_id)
    if p is None:
        raise RecordNotFoundError(message="Proposal not found.", detail={"id": str(proposal_id)})
    return APIResponse(data=await _detail(db, p))


async def _decide(db: AsyncSession, proposal_id: uuid.UUID, body: ProposalDecision, approve: bool):
    p = await ProductDataProposalRepository(db).get(proposal_id)
    if p is None:
        raise RecordNotFoundError(message="Proposal not found.", detail={"id": str(proposal_id)})
    try:
        if approve:
            result = await proposal_service.approve(db, p, reviewed_by=body.reviewed_by, note=body.note)
            applied = result.applied_to
        else:
            await proposal_service.reject(db, p, reviewed_by=body.reviewed_by, note=body.note)
            applied = None
    except ProposalImmutableError as exc:
        raise ValidationError(message=str(exc), detail={"id": str(proposal_id), "status": p.status}) from exc
    await db.commit()
    return APIResponse(data=ProposalDecisionResult(proposal=await _detail(db, p), applied_to=applied))


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=APIResponse[ProposalDecisionResult],
    summary="Approve a proposal — the ONLY way reusable master data is written",
    description=(
        "Freezes the proposal as APPROVED and writes the authoritative row in one "
        "transaction, exactly as scripts/review_proposals.py does. A reviewed proposal "
        "cannot be decided again; a changed value is a new proposal."
    ),
    responses={404: {"description": "Not found"}, 422: {"description": "Already reviewed"}},
)
async def approve_proposal(
    proposal_id: uuid.UUID, body: ProposalDecision, db: AsyncSession = Depends(get_db)
) -> APIResponse[ProposalDecisionResult]:
    return await _decide(db, proposal_id, body, approve=True)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=APIResponse[ProposalDecisionResult],
    summary="Reject a proposal — master data is untouched",
    responses={404: {"description": "Not found"}, 422: {"description": "Already reviewed"}},
)
async def reject_proposal(
    proposal_id: uuid.UUID, body: ProposalDecision, db: AsyncSession = Depends(get_db)
) -> APIResponse[ProposalDecisionResult]:
    return await _decide(db, proposal_id, body, approve=False)


@router.get(
    "/products/{item_code}/history",
    response_model=APIResponse[ProductHistory],
    summary="What happened to this product's reusable data",
    description=(
        "The current authoritative mapping (if any) and every proposal ever made for "
        "this item code, oldest first — the audit trail, built from the immutable "
        "proposal rows rather than a separate log."
    ),
)
async def product_history(
    item_code: str, db: AsyncSession = Depends(get_db)
) -> APIResponse[ProductHistory]:
    code = normalize_item_code(item_code)
    if not code:
        raise ValidationError(message="Item code contains no usable digits.")
    mapping = await ProductCaseMappingRepository(db).get(code)
    proposals = await ProductDataProposalRepository(db).list(entity_key=code)
    return APIResponse(data=ProductHistory(
        item_code=code,
        current_mapping=ResultingMapping.model_validate(mapping, from_attributes=True) if mapping else None,
        proposals=[await _detail(db, p) for p in proposals],
    ))
