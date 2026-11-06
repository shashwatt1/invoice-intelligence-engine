"""
Vendor requests — app/api/v1/vendor_requests.py

POST /vendor-requests                  ask for a vendor that is not in the list (any authenticated account)
GET  /vendor-requests                  the requests, open ones by default, with the invoices waiting on each (ADMIN)
POST /vendor-requests/{id}/approve     approve: create the vendor, or point at the one it duplicates (ADMIN)
POST /vendor-requests/{id}/reject      reject with a reason (ADMIN)

A USER or MANAGER asks; only an ADMIN decides. An ADMIN adds a vendor directly with POST /vendors and never needs a request.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin, require_authenticated_user
from app.database.session import get_db
from app.models.user import User
from app.models.vendor_request import REQUEST_PENDING, VendorRequest
from app.schemas.base import APIResponse
from app.schemas.vendor_choice import (
    VendorRequestCreate,
    VendorRequestDecision,
    VendorRequestOut,
    WaitingInvoice,
)
from app.services import vendor_request_service

router = APIRouter(prefix="/vendor-requests", tags=["Vendor Master"])


def _out(row: VendorRequest, waiting: list[dict] | None = None) -> VendorRequestOut:
    waiting = waiting or []
    return VendorRequestOut(
        id=row.id, requested_name=row.requested_name, status=row.status, requested_by=row.requested_by,
        created_at=row.created_at, decided_by=row.decided_by, decided_at=row.decided_at,
        decision_note=row.decision_note, vendor_id=row.vendor_id, waiting_invoices=len(waiting),
        waiting=[WaitingInvoice(**w) for w in waiting])


@router.post("", response_model=APIResponse[VendorRequestOut], status_code=201,
             summary="Ask for a vendor that is not in the list")
async def create_request(body: VendorRequestCreate, db: AsyncSession = Depends(get_db),
                         user: User = Depends(require_authenticated_user)) -> APIResponse[VendorRequestOut]:
    row = await vendor_request_service.request_vendor(db, user, body.name)
    out = _out(row)
    await db.commit()
    return APIResponse(data=out)


@router.get("", response_model=APIResponse[list[VendorRequestOut]], summary="Vendor requests (ADMIN)")
async def list_requests(status: str | None = Query(default=REQUEST_PENDING), db: AsyncSession = Depends(get_db),
                        user: User = Depends(require_admin)) -> APIResponse[list[VendorRequestOut]]:
    query = select(VendorRequest).order_by(VendorRequest.created_at.desc())
    if status:
        query = query.where(VendorRequest.status == status.upper())
    rows = (await db.execute(query)).scalars().all()
    waiting = await vendor_request_service.waiting_invoices(db, [r.id for r in rows])
    return APIResponse(data=[_out(r, waiting.get(r.id)) for r in rows])


@router.post("/{request_id}/approve", response_model=APIResponse[VendorRequestOut], summary="Approve a vendor request")
async def approve(request_id: uuid.UUID, body: VendorRequestDecision, db: AsyncSession = Depends(get_db),
                  user: User = Depends(require_admin)) -> APIResponse[VendorRequestOut]:
    row, _vendor, repointed = await vendor_request_service.approve_request(
        db, request_id, user, existing_vendor_id=body.existing_vendor_id, note=body.note)
    out = _out(row)
    await db.commit()
    return APIResponse(data=out)


@router.post("/{request_id}/reject", response_model=APIResponse[VendorRequestOut], summary="Reject a vendor request")
async def reject(request_id: uuid.UUID, body: VendorRequestDecision, db: AsyncSession = Depends(get_db),
                 user: User = Depends(require_admin)) -> APIResponse[VendorRequestOut]:
    row = await vendor_request_service.reject_request(db, request_id, user, note=body.note)
    out = _out(row)
    await db.commit()
    return APIResponse(data=out)
