"""
Stores — app/api/v1/stores.py

The stores this system currently knows about, derived from the data
rather than configured: any store that has an invoice, reference
pricing, a catalogue, an authoritative mapping or a proposal. The
process form uses this so an operator picks a store the system can
actually serve; it is not an authorisation boundary.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.invoice import Invoice
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_data_proposal import ProductDataProposal
from app.models.product_reference import ProductPricing
from app.models.store_product_reference import StoreProductReference
from app.schemas.base import APIResponse
from app.schemas.processing import StoreSummary

router = APIRouter(tags=["Stores"])


@router.get(
    "/stores",
    response_model=APIResponse[list[StoreSummary]],
    summary="Stores the system has data for",
)
async def list_stores(db: AsyncSession = Depends(get_db)) -> APIResponse[list[StoreSummary]]:
    known = union(
        select(Invoice.store_number),
        select(ProductPricing.store_number),
        select(StoreProductReference.store_number),
        select(ProductCaseMapping.store_number),
        select(ProductDataProposal.store_number),
    ).subquery()
    stores = sorted((await db.execute(select(known.c.store_number))).scalars().all())

    async def count(model, column):
        rows = await db.execute(select(column, func.count()).group_by(column))
        return dict(rows.all())

    invoices = await count(Invoice, Invoice.store_number)
    pricing = await count(ProductPricing, ProductPricing.store_number)
    catalogue = await count(StoreProductReference, StoreProductReference.store_number)
    mappings = await count(ProductCaseMapping, ProductCaseMapping.store_number)
    pending = dict((await db.execute(
        select(ProductDataProposal.store_number, func.count())
        .where(ProductDataProposal.status == "PENDING")
        .group_by(ProductDataProposal.store_number)
    )).all())
    return APIResponse(data=[
        StoreSummary(
            store_number=s, invoices=invoices.get(s, 0), pricing_rows=pricing.get(s, 0),
            catalogue_rows=catalogue.get(s, 0), case_mappings=mappings.get(s, 0),
            pending_proposals=pending.get(s, 0),
        )
        for s in stores
    ])
