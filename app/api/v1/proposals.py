"""
Master-data review API — app/api/v1/proposals.py

A read-and-decide surface over product_data_proposals for the review UI.

Every business rule lives in app.services.proposal_service: this module
lists, shows, and forwards decisions. It has no path to
product_case_mappings of its own — approve() is the only writer, and it
is the same function the review CLI calls.

The bulk endpoints are the review table's fast path: the same approve()
per row, one reviewer name typed once, all-or-nothing like the CLI's
approve-batch. `revise` is how a reviewer corrects a pending value from
the table without editing history — a new proposal, the old one frozen.

Decisions (approve/reject/bulk/revise) and full-evidence detail are
MANAGER or ADMIN only per the role matrix — that's the proposal
GOVERNANCE surface. USER may only list proposals, and only its own
(filtered by proposed_by == its authenticated username): a data-team USER
submits proposals via POST /invoices/{id}/case-mappings and needs to
see their status, never anyone else's evidence and never a decision
path. `reviewed_by` is still recorded as free text, the same contract
as the CLI's --by (a name on the record, not a second identity check);
the authenticated session is what gates access, this field is only
attribution.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_authenticated_user, require_manager
from app.core.exceptions import RecordNotFoundError, ValidationError
from app.database.session import get_db
from app.models.invoice import Invoice
from app.models.product_data_proposal import (
    ENTITY_CASE_MAPPING,
    FIELD_UNITS_PER_CASE,
    STATUS_PENDING,
    VALID_SOURCES,
    VALID_STATUSES,
    ProductDataProposal,
)
from app.models.user import User, UserRole
from app.repositories.invoice_repository import InvoiceRepository
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import (
    ProductDataProposalRepository,
    ProposalImmutableError,
)
from app.repositories.store_repository import StoreRepository
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.processing import (
    BulkDecisionOutcome,
    BulkDecisionResult,
    BulkProposalDecision,
    ProductHistory,
    ProposalDecision,
    ProposalDecisionResult,
    ProposalDetail,
    ProposalRevision,
    ProposalRevisionResult,
    ProposalRow,
    ProposalSourceInvoice,
    ResultingMapping,
    StoreRef,
)
from app.services import proposal_service
from app.services.export_service import normalize_item_code
from app.services.product_master.commercial_resolution import resolve_store_codes, store_value_block
from app.services.proposal_service import BatchRefusedError

router = APIRouter(tags=["Master data review"])


async def _store_ref(db: AsyncSession, store_id: uuid.UUID, cache: dict | None = None) -> StoreRef:
    if cache is not None and store_id in cache:
        return cache[store_id]
    store = await StoreRepository(db).get(store_id)
    ref = StoreRef.from_store(store)
    if cache is not None:
        cache[store_id] = ref
    return ref


async def _source_invoice(db: AsyncSession, invoice_id: uuid.UUID | None, cache: dict) -> Invoice | None:
    """The invoice a proposal was raised on, when it still exists."""
    if invoice_id is None:
        return None
    key = ("invoice-row", invoice_id)
    if key not in cache:
        cache[key] = await InvoiceRepository(db).get(invoice_id)
    return cache[key]


def _proposed_units(p: ProductDataProposal) -> int | None:
    try:
        return int(p.proposed_value)
    except (TypeError, ValueError):
        return None


async def _product_master_blocks(db: AsyncSession, proposals: list[ProductDataProposal]) -> dict[uuid.UUID, str]:
    """
    Store-level case-mapping proposals that must not be approved because the
    Product Master governs or disputes the same item (store_value_block).
    Empty while Product Master resolution is off.
    """
    by_store: dict[uuid.UUID, list[ProductDataProposal]] = {}
    for p in proposals:
        if p.entity_type == ENTITY_CASE_MAPPING and p.field == FIELD_UNITS_PER_CASE and p.status == STATUS_PENDING:
            by_store.setdefault(p.store_id, []).append(p)
    blocks: dict[uuid.UUID, str] = {}
    for store_id, group in by_store.items():
        resolution = await resolve_store_codes(db, store_id, [p.entity_key for p in group])
        for p in group:
            reason = store_value_block(resolution.lines.get(p.entity_key), _proposed_units(p))
            if reason:
                blocks[p.id] = reason
    return blocks


async def _invoice_exists(db: AsyncSession, invoice_id: uuid.UUID | None, cache: dict) -> bool:
    """Whether the invoice a proposal was raised on is still there (deleted invoices leave their history)."""
    if invoice_id is None:
        return True
    key = ("invoice", invoice_id)
    if key not in cache:
        cache[key] = await InvoiceRepository(db).get(invoice_id) is not None
    return cache[key]


def _product_name(p: ProductDataProposal) -> str | None:
    evidence = p.evidence or {}
    return evidence.get("invoice_description") or evidence.get("reference_description") or evidence.get("description")


_COMPUTED_ROW_FIELDS = ("store", "invoice_deleted", "description", "invoice_number", "invoice_date",
                        "product_master_block")


async def _row(db: AsyncSession, p: ProductDataProposal, cache: dict, block: str | None = None) -> ProposalRow:
    source = await _source_invoice(db, p.invoice_id, cache)
    return ProposalRow(store=await _store_ref(db, p.store_id, cache),
                       invoice_deleted=not await _invoice_exists(db, p.invoice_id, cache),
                       description=_product_name(p),
                       invoice_number=source.invoice_number if source else None,
                       invoice_date=source.invoice_date if source else None,
                       product_master_block=block,
                       **{k: getattr(p, k) for k in ProposalRow.model_fields if k not in _COMPUTED_ROW_FIELDS})


async def _detail(db: AsyncSession, p: ProductDataProposal) -> ProposalDetail:
    store = await _store_ref(db, p.store_id)
    mapping = await ProductCaseMappingRepository(db).get(p.store_id, p.entity_key)
    resulting = None
    if mapping is not None and mapping.approved_proposal_id == p.id:
        resulting = ResultingMapping(
            store=store, item_code=mapping.item_code, units_per_case=mapping.units_per_case,
            source=mapping.source, approved_proposal_id=mapping.approved_proposal_id,
            updated_at=mapping.updated_at,
        )
    source = await _source_invoice(db, p.invoice_id, {})
    detail = ProposalDetail(store=store,
                            invoice_deleted=not await _invoice_exists(db, p.invoice_id, {}),
                            description=_product_name(p),
                            invoice_number=source.invoice_number if source else None,
                            invoice_date=source.invoice_date if source else None,
                            product_master_block=(await _product_master_blocks(db, [p])).get(p.id),
                            **{k: getattr(p, k) for k in ProposalDetail.model_fields
                               if k not in ("store", "resulting_mapping", "current_master_value",
                                            "invoice_deleted", "description", "invoice_number",
                                            "invoice_date", "product_master_block")})
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
        "a decision is never edited, a changed value is a new proposal.\n\n"
        "USER sees only proposals it submitted itself (matched by its authenticated "
        "username); MANAGER and ADMIN see the whole queue."
    ),
)
async def list_proposals(
    status: str | None = Query(default=STATUS_PENDING),
    source: str | None = Query(default=None),
    store_id: uuid.UUID | None = Query(default=None),
    item_code: str | None = Query(default=None),
    invoice_id: uuid.UUID | None = Query(default=None),
    invoice_number: str | None = Query(default=None, max_length=64,
                                       description="The source invoice's printed number (every invoice carrying it)."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> PaginatedResponse[ProposalRow]:
    if status and status != "ALL" and status not in VALID_STATUSES:
        raise ValidationError(message=f"status must be one of {sorted(VALID_STATUSES)} or ALL.")
    if source and source not in VALID_SOURCES:
        raise ValidationError(message=f"source must be one of {sorted(VALID_SOURCES)}.")

    numbered = None
    if invoice_number and invoice_number.strip():
        numbered = list((await db.execute(
            select(Invoice.id).where(Invoice.invoice_number == invoice_number.strip())
        )).scalars())
    rows = await ProductDataProposalRepository(db).list(
        status=None if status in (None, "ALL") else status,
        source=source,
        entity_key=normalize_item_code(item_code) if item_code else None,
        invoice_id=invoice_id,
        store_id=store_id,
        invoice_ids=numbered,
    )
    if user.role == UserRole.USER.value:
        rows = [r for r in rows if r.proposed_by == user.username]
    rows = list(reversed(rows))                      # newest first for a queue
    start = (page - 1) * page_size
    cache: dict = {}
    shown = rows[start:start + page_size]
    blocks = await _product_master_blocks(db, shown)
    return PaginatedResponse(
        items=[await _row(db, p, cache, blocks.get(p.id)) for p in shown],
        total=len(rows), page=page, page_size=page_size,
    )


@router.get(
    "/proposals/source-invoices",
    response_model=APIResponse[list[ProposalSourceInvoice]],
    summary="Invoices that raised proposals — newest first, optionally for one store",
    description=(
        "Powers the review queue's Source invoice picker: each invoice proposals came from, "
        "with its store and how many of its proposals are pending. The store is the "
        "invoice's own (physical) store — where the proposal came from, not where a "
        "resulting mapping is governed."
    ),
)
async def list_source_invoices(
    store_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
) -> APIResponse[list[ProposalSourceInvoice]]:
    query = (
        select(Invoice, func.count(ProductDataProposal.id),
               func.count(ProductDataProposal.id).filter(ProductDataProposal.status == STATUS_PENDING))
        .join(ProductDataProposal, ProductDataProposal.invoice_id == Invoice.id)
        .group_by(Invoice.id)
        .order_by(Invoice.created_at.desc())
    )
    if store_id is not None:
        query = query.where(Invoice.store_id == store_id)
    if user.role == UserRole.USER.value:
        query = query.where(ProductDataProposal.proposed_by == user.username)
    cache: dict = {}
    out = []
    for invoice, total, pending in (await db.execute(query.limit(100))).all():
        out.append(ProposalSourceInvoice(
            invoice_id=invoice.id, invoice_number=invoice.invoice_number, invoice_date=invoice.invoice_date,
            store=await _store_ref(db, invoice.store_id, cache) if invoice.store_id else None,
            proposals=total, pending=pending,
        ))
    return APIResponse(data=out)


@router.get(
    "/proposals/{proposal_id}",
    response_model=APIResponse[ProposalDetail],
    summary="One proposal with its full evidence",
    dependencies=[Depends(require_manager)],
)
async def get_proposal(
    proposal_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> APIResponse[ProposalDetail]:
    p = await ProductDataProposalRepository(db).get(proposal_id)
    if p is None:
        raise RecordNotFoundError(message="Proposal not found.", detail={"id": str(proposal_id)})
    return APIResponse(data=await _detail(db, p))


async def _decide(db: AsyncSession, proposal_id: uuid.UUID, body: ProposalDecision, approve: bool, user: User):
    p = await ProductDataProposalRepository(db).get(proposal_id)
    if p is None:
        raise RecordNotFoundError(message="Proposal not found.", detail={"id": str(proposal_id)})
    if approve:
        block = (await _product_master_blocks(db, [p])).get(p.id)
        if block:
            raise ValidationError(message=block, detail={"id": str(proposal_id), "reason": "product_master_governs"})
    try:
        if approve:
            result = await proposal_service.approve(db, p, reviewed_by=user.username, note=body.note)
            applied = result.applied_to
        else:
            await proposal_service.reject(db, p, reviewed_by=user.username, note=body.note)
            applied = None
    except ProposalImmutableError as exc:
        raise ValidationError(message=str(exc), detail={"id": str(proposal_id), "status": p.status}) from exc
    await db.commit()
    return APIResponse(data=ProposalDecisionResult(proposal=await _detail(db, p), applied_to=applied))


async def _decide_bulk(db: AsyncSession, body: BulkProposalDecision, approve: bool, user: User):
    repo = ProductDataProposalRepository(db)
    ids = list(dict.fromkeys(body.proposal_ids))          # de-duplicate, keep order
    found = [await repo.get(i) for i in ids]
    missing = {str(i): "not found" for i, p in zip(ids, found, strict=True) if p is None}
    if missing:
        raise ValidationError(
            message=f"{len(missing)} proposal(s) not found; nothing was decided.",
            detail={"failures": missing},
        )
    if approve:
        blocked = await _product_master_blocks(db, [p for p in found if p is not None])
        if blocked:
            raise ValidationError(
                message=f"{len(blocked)} proposal(s) need an individual Product Master decision; nothing was decided.",
                detail={"failures": {str(i): reason for i, reason in blocked.items()}},
            )
    try:
        outcomes = await proposal_service.decide_many(
            db, [p for p in found if p is not None],
            approve_them=approve, reviewed_by=user.username, note=body.note,
        )
    except BatchRefusedError as exc:
        await db.rollback()
        raise ValidationError(
            message=f"{len(exc.failures)} proposal(s) already decided; nothing was changed.",
            detail={"failures": exc.failures},
        ) from exc
    await db.commit()
    return APIResponse(data=BulkDecisionResult(
        reviewed_by=user.username,
        decided=[BulkDecisionOutcome(id=o.proposal.id, entity_key=o.proposal.entity_key,
                                     status=o.proposal.status, applied_to=o.applied_to)
                 for o in outcomes],
    ))


@router.post(
    "/proposals/bulk-approve",
    response_model=APIResponse[BulkDecisionResult],
    summary="Approve several proposals in one transaction",
    description=(
        "Each proposal goes through the same approve() as a single decision and "
        "writes its own authoritative row. All-or-nothing: if any selected proposal "
        "is missing or already decided, nothing is written and `detail.failures` "
        "names each offending id."
    ),
    responses={422: {"description": "A selected proposal is missing or already decided"}},
)
async def bulk_approve_proposals(
    body: BulkProposalDecision, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager),
) -> APIResponse[BulkDecisionResult]:
    return await _decide_bulk(db, body, approve=True, user=user)


@router.post(
    "/proposals/bulk-reject",
    response_model=APIResponse[BulkDecisionResult],
    summary="Reject several proposals in one transaction — master data untouched",
    responses={422: {"description": "A selected proposal is missing or already decided"}},
)
async def bulk_reject_proposals(
    body: BulkProposalDecision, db: AsyncSession = Depends(get_db), user: User = Depends(require_manager),
) -> APIResponse[BulkDecisionResult]:
    return await _decide_bulk(db, body, approve=False, user=user)


@router.post(
    "/proposals/{proposal_id}/revise",
    response_model=APIResponse[ProposalRevisionResult],
    summary="Correct a pending proposal's value — a new proposal, the old one frozen",
    description=(
        "A proposal is immutable, so a correction is a NEW pending proposal "
        "(source operator_entered, evidence annotated with `revised_from`) and "
        "the original is frozen as REJECTED with a note naming its successor, in "
        "one transaction. Master data is not touched; the revision must still be "
        "approved."
    ),
    responses={404: {"description": "Not found"},
               422: {"description": "Already reviewed, or the value is unchanged"}},
)
async def revise_proposal(
    proposal_id: uuid.UUID, body: ProposalRevision, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[ProposalRevisionResult]:
    p = await ProductDataProposalRepository(db).get(proposal_id)
    if p is None:
        raise RecordNotFoundError(message="Proposal not found.", detail={"id": str(proposal_id)})
    try:
        revised = await proposal_service.revise(
            db, p, proposed_value=body.proposed_value, proposed_by=user.username, note=body.note,
        )
    except (ProposalImmutableError, ValueError) as exc:
        raise ValidationError(message=str(exc), detail={"id": str(proposal_id), "status": p.status}) from exc
    await db.commit()
    return APIResponse(data=ProposalRevisionResult(
        proposal=await _detail(db, revised), superseded=await _detail(db, p),
    ))


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
    proposal_id: uuid.UUID, body: ProposalDecision, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[ProposalDecisionResult]:
    return await _decide(db, proposal_id, body, approve=True, user=user)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=APIResponse[ProposalDecisionResult],
    summary="Reject a proposal — master data is untouched",
    responses={404: {"description": "Not found"}, 422: {"description": "Already reviewed"}},
)
async def reject_proposal(
    proposal_id: uuid.UUID, body: ProposalDecision, db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[ProposalDecisionResult]:
    return await _decide(db, proposal_id, body, approve=False, user=user)


@router.get(
    "/products/{item_code}/history",
    response_model=APIResponse[ProductHistory],
    summary="What happened to this product's reusable data, in one store",
    description=(
        "The current authoritative mapping (if any) and every proposal ever made for "
        "this item code IN THIS STORE, oldest first — the audit trail, built from the "
        "immutable proposal rows rather than a separate log. A UPC's history is "
        "store-specific: another store's decisions about the same barcode are not "
        "this store's history."
    ),
    dependencies=[Depends(require_manager)],
)
async def product_history(
    item_code: str,
    store_id: uuid.UUID = Query(..., description="The store whose history this is. Required."),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[ProductHistory]:
    code = normalize_item_code(item_code)
    if not code:
        raise ValidationError(message="Item code contains no usable digits.")
    store = await StoreRepository(db).get(store_id)
    if store is None:
        raise RecordNotFoundError(message="Store not found.", detail={"store_id": str(store_id)})
    ref = StoreRef.from_store(store)
    mapping = await ProductCaseMappingRepository(db).get(store_id, code)
    proposals = await ProductDataProposalRepository(db).list(entity_key=code, store_id=store_id)
    return APIResponse(data=ProductHistory(
        store=ref,
        item_code=code,
        current_mapping=ResultingMapping(
            store=ref, item_code=mapping.item_code, units_per_case=mapping.units_per_case,
            source=mapping.source, approved_proposal_id=mapping.approved_proposal_id,
            updated_at=mapping.updated_at,
        ) if mapping else None,
        proposals=[await _detail(db, p) for p in proposals],
    ))
