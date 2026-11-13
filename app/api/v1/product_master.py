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
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_authenticated_user, require_manager
from app.core.exceptions import RecordNotFoundError
from app.core.logging import get_logger
from app.database.session import get_db
from app.models.product_master import (
    COMMERCIAL_CONFLICT,
    DESC_CANONICAL,
    STATE_REVIEW_REQUIRED,
    MasterProduct,
)
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_item_crosswalk import CROSSWALK_CONFIRMED, VendorItemCrosswalk
from app.repositories.master_commercial_repository import MasterCommercialRepository
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.processing import SourceIdentityRef
from app.schemas.product_master import (
    CommercialBulkApprovalRequest,
    CommercialBulkApprovalResult,
    CommercialCandidateDetail,
    CommercialCandidateRow,
    CommercialProposalRequest,
    CommercialReopenRequest,
    CommercialReviewDecision,
    CommercialReviewRequest,
    CommercialReviewSummary,
    CrossStoreDifference,
    DifferenceResolutionRequest,
    DifferenceResolutionResult,
    EvidenceView,
    IdentityUnresolvedRow,
    LegacyMappingRef,
    ProductDescriptionView,
    ProductNameEditRequest,
    ProductNameEditResult,
    ProductNameVariant,
    ReviewHistoryEntry,
    StoreValueOut,
    VendorItemLinkOut,
    VendorItemRetireRequest,
)
from app.services import master_commercial_review_service as review_service
from app.services import proposal_service, vendor_item_crosswalk
from app.services.product_master import shared_decisions
from app.services.product_master.canonical_names import MANUAL_SOURCE, set_canonical_name
from app.services.product_master.commercial_resolution import SHARED_SCOPE_LABEL
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
         descriptions: list | None = None,
         facts: tuple[int, str | None] = (0, None)) -> CommercialCandidateRow:
    review_version, last_decision = facts
    legacy_units = {row.units_per_case for row in legacy}
    evidence = mapping.evidence or {}
    name = resolve_display_name(descriptions or [])
    source_identity = SourceIdentityRef.from_store(store) if store is not None else None
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
        store_id=store.id if store is not None else None,
        # A source identity is named by its source identifier, never "Store <code>".
        store_label=SHARED_SCOPE_LABEL if store is None else store.display_name or (
            source_identity.label if source_identity
            else f"Store {mapping.evidence.get('source_store_identifier', '')}".strip()),
        store_identity_status=store.identity_status if store is not None else "shared",
        store_kind=store.kind if store is not None else "shared",
        store_source_identity=source_identity,
        override_reason=getattr(mapping, "override_reason", None),
        source_system=getattr(mapping, "source_system", None),
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
        proposed_at=mapping.proposed_at,
        review_version=review_version,
        last_decision=last_decision,
        bulk_eligible=review_service.bulk_eligible(mapping, last_decision),
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
    facts = await repository.review_facts([mapping.id for mapping, _, _ in rows])
    return PaginatedResponse(
        items=[
            _row(mapping, product, store, legacy.get(mapping.pdi_item_code, []),
                 descriptions.get(product.id, []), facts.get(mapping.id, (0, None)))
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
                       descriptions, (len(history), history[0].decision if history else None)),
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
        expected_review_version=payload.expected_review_version,
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
        expected_review_version=payload.expected_review_version,
    )
    await db.commit()
    return APIResponse(data=CommercialReviewDecision(**vars(outcome)))


@router.post(
    "/commercial/{mapping_id}/reopen",
    response_model=APIResponse[CommercialReviewDecision],
    summary="Reopen a decided candidate for reconsideration (MANAGER/ADMIN)",
    description=(
        "Never an undo: the decision being reconsidered stays in the history, and a REOPEN event "
        "records who reopened it and why. The candidate returns to REVIEW_REQUIRED with the "
        "interpretation the evidence gave before that decision."
    ),
    responses={409: {"description": "Changed since the reviewer opened it"},
               422: {"description": "Not decided, or no reason given"}},
)
async def reopen_candidate(
    mapping_id: uuid.UUID,
    payload: CommercialReopenRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[CommercialReviewDecision]:
    outcome = await review_service.reopen(
        db, mapping_id, reviewer=user.username, reason=payload.reason,
        reviewer_user_id=user.id, reviewer_role=user.role,
        expected_review_version=payload.expected_review_version,
    )
    await db.commit()
    return APIResponse(data=CommercialReviewDecision(**vars(outcome)))


@router.post(
    "/commercial/bulk-approve",
    response_model=APIResponse[CommercialBulkApprovalResult],
    summary="Approve several READY_FOR_REVIEW candidates (MANAGER/ADMIN)",
    description=(
        "A convenience over the same governed approval: every selected candidate is approved and "
        "recorded individually (one review event each, with the reviewer, role, basis and evidence). "
        "All or nothing — if any candidate changed since it was selected (409) or needs an individual "
        "decision (422: conflict, pending proposal, no multiplier, reopened), nothing is approved."
    ),
    responses={409: {"description": "The queue changed since the selection"},
               422: {"description": "A selected candidate needs an individual decision"}},
)
async def bulk_approve_candidates(
    payload: CommercialBulkApprovalRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[CommercialBulkApprovalResult]:
    outcomes = await review_service.bulk_approve(
        db,
        [review_service.BulkApprovalItem(i.mapping_id, i.expected_review_version) for i in payload.items],
        reviewer=user.username, note=payload.note,
        reviewer_user_id=user.id, reviewer_role=user.role,
    )
    await db.commit()
    return APIResponse(data=CommercialBulkApprovalResult(
        approved=len(outcomes), decisions=[CommercialReviewDecision(**vars(o)) for o in outcomes],
    ))


@router.put(
    "/products/{product_id}/name",
    response_model=APIResponse[ProductNameEditResult],
    summary="Edit a product's name (MANAGER/ADMIN)",
    description=(
        "Makes the given name the product's canonical name, as shown in PM Approvals, Product Master, invoice "
        "detail and inventory. The previous canonical name is kept as a historical alias, never deleted; who "
        "edited is recorded with the name. The PDI export is unaffected (it writes the invoice line's wording)."
    ),
    responses={404: {"description": "Product not found"}, 422: {"description": "Empty, too long or invalid name"}},
)
async def edit_product_name(
    product_id: uuid.UUID, body: ProductNameEditRequest, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[ProductNameEditResult]:
    product = await db.get(MasterProduct, product_id)
    if product is None:
        raise RecordNotFoundError(message="Product not found.", detail={"product_id": str(product_id)})
    change = await set_canonical_name(db, product, body.name, source_system=MANUAL_SOURCE,
                                      source_file=f"edited by {user.username} ({user.role})",
                                      source_sheet="PM Approvals", demote="all_others")
    await db.commit()
    get_logger(__name__).info("product_name_edited", product_id=str(product.id), actor=user.username,
                              changed=change.changed, demoted=change.demoted)
    return APIResponse(data=ProductNameEditResult(product_id=product.id, name=product.canonical_description,
                                                  previous_name=change.previous, changed=change.changed))


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


# ---- shared values: cross-store differences (0038) ---------------------------------------

def _store_value(v) -> StoreValueOut:
    return StoreValueOut(store_id=v.store_id, store=v.store_label, units=v.units, source=v.source,
                         override_reason=v.override_reason)


@router.get(
    "/differences",
    response_model=APIResponse[list[CrossStoreDifference]],
    summary="Items whose approved units-per-case disagree between stores or with the shared value",
    description=(
        "Every place where approved data conflicts — for example one store holding 18 and another 1 for the "
        "same C-18 case. Nothing is chosen automatically: when Product Master resolution is on, an affected "
        "line is a CONFLICT until one of these is settled. Deliberate store overrides are listed beside, "
        "not counted as a disagreement."
    ),
)
async def list_differences(db: AsyncSession = Depends(get_db)) -> APIResponse[list[CrossStoreDifference]]:
    found = await shared_decisions.cross_store_differences(db)
    return APIResponse(data=[
        CrossStoreDifference(item_code=d.item_code, shared_units=d.shared_units, distinct_values=d.distinct_values,
                             stores=[_store_value(v) for v in d.stores],
                             overrides=[_store_value(v) for v in d.overrides])
        for d in found
    ])


@router.post(
    "/differences/{item_code}/resolve",
    response_model=APIResponse[DifferenceResolutionResult],
    summary="Settle a cross-store difference: one shared value, explicit store exceptions",
    description=(
        "Sets `units` as the shared value. Each store in `keep_store_ids` keeps its own value as a recorded "
        "store override; every other differing store adopts the shared value through an approved, audited "
        "proposal. Refused (nothing written) when a different shared value is already approved, or a "
        "differing store's value is an approved Product Master mapping that must be reopened first."
    ),
    responses={422: {"description": "Refused; nothing was written"}},
)
async def resolve_difference(
    item_code: str, body: DifferenceResolutionRequest, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[DifferenceResolutionResult]:
    result = await proposal_service.resolve_difference(
        db, item_code=item_code, units=body.units, keep_store_ids=set(body.keep_store_ids), note=body.note,
        reviewed_by=user.username, reviewer_user_id=user.id, reviewer_role=user.role,
    )
    await db.commit()
    return APIResponse(data=DifferenceResolutionResult(
        item_code=result.item_code, units=result.units, shared_mapping_id=uuid.UUID(result.shared_mapping_id),
        adopted_store_ids=list(result.adopted_store_ids), kept_store_ids=list(result.kept_store_ids),
    ))


# ---- vendor item crosswalk (0038) -----------------------------------------------------------

@router.get(
    "/vendor-items",
    response_model=APIResponse[list[VendorItemLinkOut]],
    summary="Confirmed (and retired) vendor item number -> product links",
)
async def list_vendor_items(
    vendor_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=64),
    include_retired: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[list[VendorItemLinkOut]]:
    statement = select(VendorItemCrosswalk, Vendor).join(Vendor, Vendor.id == VendorItemCrosswalk.vendor_id)
    if vendor_id:
        statement = statement.where(VendorItemCrosswalk.vendor_id == vendor_id)
    if not include_retired:
        statement = statement.where(VendorItemCrosswalk.state == CROSSWALK_CONFIRMED)
    if search:
        term = f"%{search.strip()}%"
        statement = statement.where(or_(VendorItemCrosswalk.vendor_item_code.ilike(term),
                                        VendorItemCrosswalk.item_code.ilike(term)))
    rows = (await db.execute(statement.order_by(Vendor.name, VendorItemCrosswalk.vendor_item_code).limit(500))).all()
    return APIResponse(data=[
        VendorItemLinkOut(
            id=link.id, vendor_id=link.vendor_id, vendor=vendor.label if hasattr(vendor, "label") else vendor.name,
            vendor_item_code=link.vendor_item_code, vendor_item_code_raw=link.vendor_item_code_raw,
            item_code=link.item_code, upc=link.upc, state=link.state, confirmed_by=link.confirmed_by,
            confirmed_at=link.confirmed_at, retired_at=link.retired_at, retired_by=link.retired_by,
            retire_reason=link.retire_reason)
        for link, vendor in rows
    ])


@router.post(
    "/vendor-items/{link_id}/retire",
    response_model=APIResponse[VendorItemLinkOut],
    summary="Stop applying a wrong vendor item link (corrections it already made stay recorded on their lines)",
)
async def retire_vendor_item(
    link_id: uuid.UUID, body: VendorItemRetireRequest, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[VendorItemLinkOut]:
    link = await vendor_item_crosswalk.retire(db, link_id, reason=body.reason, retired_by=user.username)
    vendor = await db.get(Vendor, link.vendor_id)
    await db.commit()
    return APIResponse(data=VendorItemLinkOut(
        id=link.id, vendor_id=link.vendor_id, vendor=vendor.name if vendor else None,
        vendor_item_code=link.vendor_item_code, vendor_item_code_raw=link.vendor_item_code_raw,
        item_code=link.item_code, upc=link.upc, state=link.state, confirmed_by=link.confirmed_by,
        confirmed_at=link.confirmed_at, retired_at=link.retired_at, retired_by=link.retired_by,
        retire_reason=link.retire_reason))
