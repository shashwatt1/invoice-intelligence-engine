"""
Vendor Master API — app/api/v1/vendors.py

GET  /vendors                     — the Vendor Master list (any authenticated account)
GET  /vendors/{vendor_id}         — one vendor with its evidence and decision history
POST /vendors/{vendor_id}/confirm — confirm the canonical identity (MANAGER/ADMIN)
POST /vendors/{vendor_id}/reopen  — return a confirmed vendor to unresolved (MANAGER/ADMIN)

Reading is open to any authenticated account, like the Product Master:
vendor information is already visible on the invoices a USER can see.
Deciding is MANAGER/ADMIN, enforced here, and who decided is always the
authenticated session. No endpoint merges vendors, re-keys invoices or
rewrites what an invoice printed.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_authenticated_user, require_manager
from app.core.exceptions import RecordNotFoundError
from app.database.session import get_db
from app.models.user import User
from app.models.vendor import VENDOR_IDENTITY_STATUSES, Vendor
from app.repositories.store_repository import StoreRepository
from app.repositories.vendor_repository import VendorRepository
from app.schemas.base import APIResponse, PaginatedResponse
from app.schemas.processing import StoreRef
from app.schemas.vendor import (
    ObservedName,
    ObservedTaxId,
    VendorConfirmRequest,
    VendorDecision,
    VendorDetail,
    VendorDiscrepancy,
    VendorIdentityReviewEntry,
    VendorInvoiceRef,
    VendorReopenRequest,
    VendorRow,
)
from app.services import vendor_identity_service

router = APIRouter(prefix="/vendors", tags=["Vendor Master"], dependencies=[Depends(require_authenticated_user)])


def _row(vendor: Vendor, stats: dict) -> VendorRow:
    return VendorRow(
        id=vendor.id, label=vendor.label, name=vendor.name, display_name=vendor.display_name,
        identity_status=vendor.identity_status, tax_id=vendor.tax_id,
        invoices=stats.get("invoices", 0), observed_names=stats.get("observed_names", 0),
        last_seen_at=stats.get("last_seen_at"),
    )


@router.get("", response_model=PaginatedResponse[VendorRow], summary="The Vendor Master")
async def list_vendors(
    identity_status: str | None = Query(default=None, description="'unresolved' or 'confirmed'."),
    search: str | None = Query(default=None, max_length=64,
                               description="Canonical name, observed invoice wording or tax id."),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[VendorRow]:
    status = identity_status if identity_status in VENDOR_IDENTITY_STATUSES else None
    text = (search or "").strip() or None
    repository = VendorRepository(db)
    vendors = await repository.list_page(page=page, page_size=page_size, identity_status=status, search=text)
    total = await repository.count(identity_status=status, search=text)
    stats = await repository.invoice_stats([v.id for v in vendors])
    return PaginatedResponse(items=[_row(v, stats.get(v.id, {})) for v in vendors],
                             total=total, page=page, page_size=page_size)


@router.get("/{vendor_id}", response_model=APIResponse[VendorDetail], summary="One vendor, with its evidence")
async def get_vendor(vendor_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> APIResponse[VendorDetail]:
    repository = VendorRepository(db)
    vendor = await repository.get(vendor_id)
    if vendor is None:
        raise RecordNotFoundError(message="Vendor not found.", detail={"vendor_id": str(vendor_id)})
    stats = (await repository.invoice_stats([vendor.id])).get(vendor.id, {})
    invoices = await repository.recent_invoices(vendor.id)
    stores = await StoreRepository(db).labels([i.store_id for i in invoices if i.store_id])
    row = _row(vendor, stats)
    return APIResponse(data=VendorDetail(
        **row.model_dump(),
        address=vendor.address, phone=vendor.phone, email=vendor.email,
        observed_name_list=[ObservedName(**n) for n in await repository.observed_names(vendor.id)],
        observed_tax_ids=[ObservedTaxId(**t) for t in await repository.observed_tax_ids(vendor.id)],
        recent_invoices=[
            VendorInvoiceRef(
                invoice_id=i.id, document_id=i.document_id, invoice_number=i.invoice_number,
                invoice_date=i.invoice_date, observed_vendor_name=i.vendor_name, grand_total=i.grand_total,
                store=StoreRef.from_store(stores[i.store_id]) if i.store_id in stores else None,
            )
            for i in invoices
        ],
        history=[
            VendorIdentityReviewEntry(
                decision=h.decision, previous_status=h.previous_status, new_status=h.new_status,
                previous_display_name=h.previous_display_name, new_display_name=h.new_display_name,
                reviewer=h.reviewer, reviewer_role=h.reviewer_role, basis=h.basis, decided_at=h.created_at,
            )
            for h in await repository.history(vendor.id)
        ],
        discrepancies=[VendorDiscrepancy(**d) for d in await repository.discrepancies(vendor.id)],
    ))


@router.post("/{vendor_id}/confirm", response_model=APIResponse[VendorDecision],
             summary="Confirm a vendor's canonical identity (MANAGER/ADMIN)")
async def confirm_vendor(
    vendor_id: uuid.UUID,
    payload: VendorConfirmRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[VendorDecision]:
    outcome = await vendor_identity_service.confirm(
        db, vendor_id, display_name=payload.display_name, basis=payload.basis,
        reviewer=user.username, reviewer_user_id=user.id, reviewer_role=user.role,
    )
    await db.commit()
    return APIResponse(data=VendorDecision(**vars(outcome)))


@router.post("/{vendor_id}/reopen", response_model=APIResponse[VendorDecision],
             summary="Return a confirmed vendor to unresolved (MANAGER/ADMIN)")
async def reopen_vendor(
    vendor_id: uuid.UUID,
    payload: VendorReopenRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
) -> APIResponse[VendorDecision]:
    outcome = await vendor_identity_service.reopen(
        db, vendor_id, basis=payload.basis,
        reviewer=user.username, reviewer_user_id=user.id, reviewer_role=user.role,
    )
    await db.commit()
    return APIResponse(data=VendorDecision(**vars(outcome)))
