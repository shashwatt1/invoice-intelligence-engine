"""
Product Master review — app/api/v1/product_master.py

    GET   /product-master/commercial            the review queue
    GET   /product-master/commercial/summary    counts for the page header
    GET   /product-master/commercial/{id}       one candidate, with evidence and history
    POST  /product-master/commercial/{id}/approve
    POST  /product-master/commercial/{id}/reject
    GET   /product-master/identity-unresolved   read-only identity queue

MANAGER/ADMIN only, router-wide. USER has no reason to reach master-data
governance and is refused here regardless of what the frontend renders.

Approving a candidate is a master-data decision and nothing else. The EDI
writer still reads `product_case_mappings`, which this module never writes;
the legacy rows it returns are context for the reviewer, clearly labelled
as the current EDI authority.

This module has no relationship to the legacy proposal endpoints. It does
not call them, extend them or change them.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_authenticated_user, require_manager
from app.database.session import get_db
from app.models.product_master import COMMERCIAL_CONFLICT, DESC_CANONICAL, STATE_REVIEW_REQUIRED
from app.models.user import User
from app.repositories.master_commercial_repository import MasterCommercialRepository
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.product_master import (
    CommercialCandidateDetail,
    CommercialCandidateRow,
    CommercialProposalRequest,
    CommercialReviewDecision,
    CommercialReviewRequest,
    CommercialReviewSummary,
    EvidenceView,
    IdentityUnresolvedRow,
    LegacyMappingRef,
    ProductDescriptionView,
    ProductNameVariant,
    ReviewHistoryEntry,
)
from app.services import master_commercial_review_service as review_service
from app.services.product_master.display_name import NAME_AMBIGUOUS, resolve_display_name

# Viewing and proposing are open to any authenticated account; approving
# and rejecting are MANAGER/ADMIN and are guarded per-route below. The
# router-wide dependency is therefore authentication, not authorisation.
router = APIRouter(
    prefix="/product-master", tags=["Product Master"],
    dependencies=[Depends(require_authenticated_user)],
)

# Evidence that may never determine a commercial multiplier. Sent to the
# client so the workbench states it rather than implying it.
INADMISSIBLE_EVIDENCE = [
    "physical pack composition",
    "package notation",
    "description text",
    "frequency of source rows",
]


def _row(mapping, product, store, legacy: list,
         descriptions: list | None = None) -> CommercialCandidateRow:
    legacy_units = {row.units_per_case for row in legacy}
    evidence = mapping.evidence or {}
    name = resolve_display_name(descriptions or [])
    return CommercialCandidateRow(
        id=mapping.id,
        product_id=product.id,
        product_name=name.label if name.basis == NAME_AMBIGUOUS else name.name,
        product_name_basis=name.basis,
        product_name_source=name.source_class,
        product_name_reference=name.source_reference,
        product_name_variant_count=name.variant_count,
        product_name_variants=[
            ProductNameVariant(description=w.description, source_class=w.source_class,
                               references=list(w.references))
            for w in name.wordings
        ] if name.basis == NAME_AMBIGUOUS else [],
        canonical_identifier=product.canonical_upc,
        pdi_item_code=mapping.pdi_item_code,
        store_id=store.id,
        store_label=store.display_name or f"Store {mapping.evidence.get('source_store_identifier', '')}".strip(),
        store_identity_status=store.identity_status,
        commercial_unit_basis=mapping.commercial_unit_basis,
        units_accounted_for=mapping.units_accounted_for,
        case_cost=float(mapping.case_cost) if mapping.case_cost is not None else None,
        cost_basis=mapping.cost_basis,
        approval_state=mapping.approval_state,
        evidence_state=mapping.approval_state,
        is_conflict=mapping.commercial_unit_basis == COMMERCIAL_CONFLICT,
        requires_resolution=review_service.requires_resolution(mapping),
        review_status=review_service.review_status(mapping),
        legacy_agreement=review_service.legacy_agreement(
            mapping.units_accounted_for, legacy_units),
        # The reason the evidence produced no answer, in the words the
        # decision was recorded with.
        conflict_explanation=evidence.get("notes"),
        reviewed_by=mapping.reviewed_by,
        reviewed_at=mapping.reviewed_at,
        proposed_units_accounted_for=mapping.proposed_units_accounted_for,
        proposed_by=mapping.proposed_by,
        proposed_note=mapping.proposed_note,
        legacy_mappings=[
            LegacyMappingRef(
                item_code=row.item_code, units_per_case=row.units_per_case,
                description=row.description, source=row.source,
            )
            for row in legacy
        ],
    )


@router.get(
    "/commercial",
    response_model=PaginatedResponse[CommercialCandidateRow],
    summary="Product Master commercial review queue",
)
async def list_commercial_candidates(
    approval_state: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
    commercial_unit_basis: str | None = Query(default=None),
    conflicts_only: bool = Query(default=False),
    store_id: uuid.UUID | None = Query(default=None),
    cost_basis: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> PaginatedResponse[CommercialCandidateRow]:
    repository = MasterCommercialRepository(db)
    filters = {
        "approval_state": approval_state,
        "review_status": review_status,
        "commercial_unit_basis": commercial_unit_basis,
        "conflicts_only": conflicts_only,
        "store_id": store_id,
        "cost_basis": cost_basis,
        "search": search,
    }
    rows = await repository.list_candidates(page=page, page_size=page_size, **filters)
    total = await repository.count_candidates(**filters)
    legacy = await repository.legacy_mappings_for(
        [mapping.pdi_item_code for mapping, _, _ in rows if mapping.pdi_item_code]
    )
    descriptions = await repository.descriptions_for([product.id for _, product, _ in rows])
    return PaginatedResponse(
        items=[
            _row(mapping, product, store, legacy.get(mapping.pdi_item_code, []),
                 descriptions.get(product.id, []))
            for mapping, product, store in rows
        ],
        total=total, page=page, page_size=page_size,
    )


@router.get(
    "/commercial/summary",
    response_model=APIResponse[CommercialReviewSummary],
    summary="Counts for the review header",
)
async def commercial_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> APIResponse[CommercialReviewSummary]:
    totals = await MasterCommercialRepository(db).summary()
    return APIResponse(data=CommercialReviewSummary(
        review_required=totals.get(STATE_REVIEW_REQUIRED, 0),
        pending=totals.get("PENDING", 0),
        approved=totals.get("APPROVED", 0),
        rejected=totals.get("REJECTED", 0),
        conflicts=totals.get("conflicts", 0),
        total=sum(v for k, v in totals.items() if not k.startswith(("basis:", "conflicts"))),
    ))


@router.get(
    "/commercial/{mapping_id}",
    response_model=APIResponse[CommercialCandidateDetail],
    summary="One candidate, with its evidence and decision history",
)
async def get_commercial_candidate(
    mapping_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> APIResponse[CommercialCandidateDetail]:
    repository = MasterCommercialRepository(db)
    found = await repository.get(mapping_id)
    if found is None:
        from app.core.exceptions import RecordNotFoundError

        raise RecordNotFoundError(
            message="Commercial candidate not found.", detail={"mapping_id": str(mapping_id)},
        )
    mapping, product, store = found
    legacy = await repository.legacy_mappings_for(
        [mapping.pdi_item_code] if mapping.pdi_item_code else []
    )
    history = await repository.history_for(mapping_id)
    evidence = mapping.evidence or {}
    descriptions = (await repository.descriptions_for([product.id])).get(product.id, [])

    return APIResponse(data=CommercialCandidateDetail(
        candidate=_row(mapping, product, store, legacy.get(mapping.pdi_item_code, []),
                       descriptions),
        evidence=EvidenceView(
            notes=evidence.get("notes"),
            source_statements=evidence.get("source_statements", []),
            governed_units_observed=evidence.get("governed_units_observed", []),
            supporting_rows=evidence.get("supporting_rows", []),
            source_file=mapping.source_file,
            source_sheet=mapping.source_sheet,
            source_row=mapping.source_row,
            source_snapshot_rows=(mapping.source_snapshot or {}).get("source_rows", []),
            admissible_evidence=[
                "distributor items/case column",
                "distributor unit-cost formula divisor",
                "existing governed mapping (corroboration or dissent only)",
            ],
            inadmissible_evidence=INADMISSIBLE_EVIDENCE,
            descriptions=[
                ProductDescriptionView(
                    role=row.role, description=row.description,
                    source_system=row.source_system, source_file=row.source_file,
                    source_sheet=row.source_sheet, source_row=row.source_row,
                )
                for row in sorted(
                    descriptions,
                    key=lambda d: (d.role != DESC_CANONICAL, d.source_sheet or "",
                                   d.source_row or 0, d.description),
                )
            ],
        ),
        history=[
            ReviewHistoryEntry(
                decision=entry.decision,
                previous_approval_state=entry.previous_approval_state,
                new_approval_state=entry.new_approval_state,
                previous_commercial_unit_basis=entry.previous_commercial_unit_basis,
                new_commercial_unit_basis=entry.new_commercial_unit_basis,
                previous_units_accounted_for=entry.previous_units_accounted_for,
                new_units_accounted_for=entry.new_units_accounted_for,
                reviewer=entry.reviewer, reviewer_role=entry.reviewer_role,
                note=entry.note, decided_at=entry.created_at,
            )
            for entry in history
        ],
    ))


@router.post(
    "/commercial/{mapping_id}/propose",
    response_model=APIResponse[CommercialReviewDecision],
    summary="Propose a units-per-case value (any authenticated account)",
)
async def propose_candidate(
    mapping_id: uuid.UUID,
    payload: CommercialProposalRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> APIResponse[CommercialReviewDecision]:
    """
    Record a suggested multiplier. Never authoritative: the candidate keeps
    the value the evidence produced, the proposal sits beside it, and a
    MANAGER or ADMIN decides through the same approval service.
    """
    outcome = await review_service.propose(
        db, mapping_id,
        units_accounted_for=payload.units_accounted_for,
        proposer=user.username,
        note=payload.note,
        proposer_user_id=user.id,
        proposer_role=user.role,
    )
    await db.commit()
    return APIResponse(data=CommercialReviewDecision(**vars(outcome)))


@router.post(
    "/commercial/{mapping_id}/approve",
    response_model=APIResponse[CommercialReviewDecision],
    summary="Approve a commercial candidate (master data only — does not change EDI)",
)
async def approve_candidate(
    mapping_id: uuid.UUID,
    payload: CommercialReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[CommercialReviewDecision]:
    outcome = await review_service.approve(
        db, mapping_id,
        reviewer=user.username,
        note=payload.note,
        commercial_unit_basis=payload.commercial_unit_basis,
        units_accounted_for=payload.units_accounted_for,
        reviewer_user_id=user.id,
        reviewer_role=user.role,
    )
    await db.commit()
    return APIResponse(data=CommercialReviewDecision(**vars(outcome)))


@router.post(
    "/commercial/{mapping_id}/reject",
    response_model=APIResponse[CommercialReviewDecision],
    summary="Reject a commercial candidate (kept for audit, never deleted)",
)
async def reject_candidate(
    mapping_id: uuid.UUID,
    payload: CommercialReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[CommercialReviewDecision]:
    outcome = await review_service.reject(
        db, mapping_id, reviewer=user.username, note=payload.note,
        reviewer_user_id=user.id, reviewer_role=user.role,
    )
    await db.commit()
    return APIResponse(data=CommercialReviewDecision(**vars(outcome)))


@router.get(
    "/identity-unresolved",
    response_model=APIResponse[list[IdentityUnresolvedRow]],
    summary="Reference rows whose product identity never resolved — read only",
)
async def identity_unresolved(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> APIResponse[list[IdentityUnresolvedRow]]:
    """
    Kept out of the commercial queue on purpose: these rows have no
    canonical product, so there is nothing to approve. Read-only — no
    product is created from here.
    """
    from app.services.product_master.identity_queue import load_unresolved_candidates

    return APIResponse(data=[
        IdentityUnresolvedRow(**row) for row in await load_unresolved_candidates(db)
    ])
