"""
Requires Mapping — app/api/v1/mapping_queue.py

    GET /mapping-queue            Unresolved (store, UPC) mapping gaps, across every invoice
    GET /mapping-queue/summary    Counts for the sidebar badge / page header

MANAGER/ADMIN only, router-wide — the collaborative work queue this
phase adds: a mapping gap is visible here the moment an invoice with an
unmapped UPC exists, whether or not anyone has proposed a value yet and
regardless of who uploaded the invoice or whether they are logged in.

Purely a read over app.services.mapping_queue_service, itself a read
over invoice_items + product_case_mappings — there is no separate
queue table. Proposing a value FROM this queue goes through the exact
same POST /invoices/{id}/case-mappings endpoint invoice detail already
uses; this module has no write path of its own.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_manager
from app.database.session import get_db
from app.repositories.store_repository import StoreRepository
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.processing import (
    MappingQueueOccurrence,
    MappingQueueRow,
    MappingQueueSummary,
    StoreRef,
)
from app.services.mapping_queue_service import list_unresolved_mapping_groups

router = APIRouter(
    prefix="/mapping-queue", tags=["Requires Mapping"], dependencies=[Depends(require_manager)]
)


async def _rows(db: AsyncSession, groups) -> list[MappingQueueRow]:
    store_ids = list({group.store_id for group in groups})
    stores = await StoreRepository(db).labels(store_ids)
    return [
        MappingQueueRow(
            store=StoreRef.from_store(stores[group.store_id]),
            item_code=group.item_code,
            description=group.description,
            invoice_count=group.invoice_count,
            occurrences=[
                MappingQueueOccurrence(
                    invoice_id=o.invoice_id, document_id=o.document_id,
                    invoice_number=o.invoice_number, description=o.description,
                    quantity=o.quantity, unit_price=o.unit_price, pack_size=o.pack_size,
                )
                for o in group.occurrences
            ],
            pending_proposal_id=group.pending_proposal_id,
            pending_value=group.pending_value,
            pending_proposed_by=group.pending_proposed_by,
        )
        for group in groups
        if group.store_id in stores
    ]


@router.get(
    "",
    response_model=PaginatedResponse[MappingQueueRow],
    summary="Unresolved mapping requirements, across every invoice",
    description=(
        "Every (store, UPC) with a product line on some invoice and no authoritative "
        "units-per-case mapping — grouped by product so the same master-data gap seen "
        "on several invoices is one work item, not several. Does not require a "
        "proposal to have been submitted, and does not depend on who uploaded the "
        "invoice or whether they are still logged in."
    ),
)
async def list_mapping_queue(
    store_id: uuid.UUID | None = Query(default=None, description="Limit to one store."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[MappingQueueRow]:
    groups = await list_unresolved_mapping_groups(db, store_id=store_id)
    start = (page - 1) * page_size
    rows = await _rows(db, groups[start:start + page_size])
    return PaginatedResponse(items=rows, total=len(groups), page=page, page_size=page_size)


@router.get(
    "/summary",
    response_model=APIResponse[MappingQueueSummary],
    summary="Requires Mapping counts",
    description=(
        "Precise counts over the FULL unresolved set, not just the current page: "
        "distinct products, total invoice occurrences, and distinct stores affected."
    ),
)
async def mapping_queue_summary(db: AsyncSession = Depends(get_db)) -> APIResponse[MappingQueueSummary]:
    groups = await list_unresolved_mapping_groups(db)
    return APIResponse(data=MappingQueueSummary(
        unique_products=len(groups),
        invoice_occurrences=sum(len(g.occurrences) for g in groups),
        stores=len({g.store_id for g in groups}),
    ))
