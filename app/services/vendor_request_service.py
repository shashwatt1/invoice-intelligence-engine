"""
Choosing a vendor at upload — app/services/vendor_request_service.py

* A MANAGER or ADMIN adds a vendor that is not listed directly: it is created and confirmed at once, so it shows in the
  picker straight away, with the audit row the Vendor Master already keeps.
* Anyone else asks for one (`request_vendor`): nothing enters the Vendor Master; the request waits for a MANAGER or ADMIN,
  and the invoice uploaded against it reads "vendor pending approval".
* Approving a request creates the vendor (or links the request to the vendor it was found to duplicate) and points every
  invoice that waited at it; rejecting leaves those invoices to have a vendor chosen before they can be marked Retailed.

A name that matches an existing vendor (see vendor_matching) is never created again: the caller is told which vendor it
is, so the picker's list stays free of the duplicates that extraction alone produced.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError, RecordNotFoundError, ValidationError
from app.models.document import Document
from app.models.invoice import SOURCE_OPERATOR, SOURCE_REQUESTED, Invoice
from app.models.user import ROLE_RANK, User, UserRole
from app.models.vendor import VENDOR_CONFIRMED, Vendor
from app.models.vendor_request import (
    REQUEST_APPROVED,
    REQUEST_PENDING,
    REQUEST_REJECTED,
    VendorRequest,
)
from app.repositories.vendor_repository import resolve_vendor
from app.services import vendor_identity_service
from app.services.vendor_matching import names_match, normalize_vendor_name


def is_manager(user: User) -> bool:
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK[UserRole.MANAGER.value]


def clean_name(name: str | None) -> str:
    text = " ".join((name or "").split())
    if len(text) < 2:
        raise ValidationError(message="Enter the vendor's name.", detail={"field": "name", "reason": "required"})
    if len(text) > 255:
        raise ValidationError(message="Keep the vendor name to 255 characters.", detail={"field": "name", "reason": "too_long"})
    return text


async def existing_vendor_named(session: AsyncSession, name: str) -> Vendor | None:
    """A vendor already listed under this name (or a spelling that matches it), if any."""
    for vendor in (await session.execute(select(Vendor).where(Vendor.merged_into_id.is_(None)))).scalars().all():
        if names_match(name, vendor.display_name) or names_match(name, vendor.name):
            return vendor
    return None


def _already_listed(vendor: Vendor) -> ValidationError:
    label = vendor.display_name or vendor.name
    return ValidationError(
        message=f"{label!r} is already in the vendor list. Choose it instead of adding it again.",
        detail={"field": "name", "reason": "already_listed", "vendor_id": str(vendor.id), "vendor": label})


async def add_vendor(session: AsyncSession, user: User, name: str) -> Vendor:
    """MANAGER/ADMIN: create and confirm a vendor now. Flushes; the caller commits."""
    if not is_manager(user):
        raise PermissionDeniedError(message="Only a manager or administrator can add a vendor directly. Request one instead.",
                                    detail={"required_role": UserRole.MANAGER.value, "actual_role": user.role})
    text = clean_name(name)
    found = await existing_vendor_named(session, text)
    if found is not None:
        raise _already_listed(found)
    vendor = Vendor(name=text)
    session.add(vendor)
    await session.flush()
    await vendor_identity_service.confirm(
        session, vendor.id, display_name=text,
        basis=f"Added to the vendor list by {user.role} {user.username} when uploading an invoice that named no listed vendor.",
        reviewer=user.username, reviewer_user_id=user.id, reviewer_role=user.role)
    return vendor


async def request_vendor(session: AsyncSession, user: User, name: str) -> VendorRequest:
    """Ask for a vendor to be added. Joins the open request for the same name. Flushes; the caller commits."""
    text = clean_name(name)
    found = await existing_vendor_named(session, text)
    if found is not None:
        raise _already_listed(found)
    normalized = normalize_vendor_name(text)
    open_request = (await session.execute(select(VendorRequest).where(
        VendorRequest.normalized_name == normalized, VendorRequest.status == REQUEST_PENDING))).scalar_one_or_none()
    if open_request is not None:
        return open_request
    request = VendorRequest(requested_name=text, normalized_name=normalized, requested_by=user.username,
                            requested_by_user_id=user.id)
    try:
        async with session.begin_nested():
            session.add(request)
            await session.flush()
    except IntegrityError:                                  # lost a race with the same name: join the winner
        return (await session.execute(select(VendorRequest).where(
            VendorRequest.normalized_name == normalized, VendorRequest.status == REQUEST_PENDING))).scalar_one()
    return request


async def waiting_invoice_counts(session: AsyncSession, request_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not request_ids:
        return {}
    rows = (await session.execute(
        select(Document.vendor_request_id, func.count(Invoice.id)).join(Invoice, Invoice.document_id == Document.id)
        .where(Document.vendor_request_id.in_(request_ids), Invoice.vendor_source == SOURCE_REQUESTED)
        .group_by(Document.vendor_request_id))).all()
    return {rid: int(n) for rid, n in rows}


async def _locked_pending(session: AsyncSession, request_id: uuid.UUID) -> VendorRequest:
    row = (await session.execute(select(VendorRequest).where(VendorRequest.id == request_id).with_for_update())).scalar_one_or_none()
    if row is None:
        raise RecordNotFoundError(message="Vendor request not found.", detail={"request_id": str(request_id)})
    if row.status != REQUEST_PENDING:
        raise ValidationError(message="This request has already been decided.",
                              detail={"request_id": str(request_id), "status": row.status})
    return row


async def approve_request(session: AsyncSession, request_id: uuid.UUID, user: User, *,
                          existing_vendor_id: uuid.UUID | None = None, note: str | None = None
                          ) -> tuple[VendorRequest, Vendor, list[uuid.UUID]]:
    """
    MANAGER/ADMIN: approve. Creates the vendor, or links to `existing_vendor_id` when the request duplicates one, then points
    every waiting invoice at it and revalidates those invoices. Returns (request, vendor, invoice ids repointed).
    """
    if not is_manager(user):
        raise PermissionDeniedError(message="Only a manager or administrator can decide a vendor request.",
                                    detail={"required_role": UserRole.MANAGER.value, "actual_role": user.role})
    request = await _locked_pending(session, request_id)
    if existing_vendor_id is not None:
        vendor = await resolve_vendor(session, await session.get(Vendor, existing_vendor_id))
        if vendor is None:
            raise RecordNotFoundError(message="Vendor not found.", detail={"vendor_id": str(existing_vendor_id)})
    else:
        found = await existing_vendor_named(session, request.requested_name)
        if found is None:
            vendor = await add_vendor(session, user, request.requested_name)
        elif found.identity_status == VENDOR_CONFIRMED:
            raise _already_listed(found)
        else:
            # The invoice uploaded against this request was read, and the vendor it printed was recorded as an unconfirmed
            # row. That is the vendor being asked for: confirm it rather than refusing it as a duplicate of itself.
            vendor = found
            await vendor_identity_service.confirm(
                session, vendor.id, display_name=clean_name(request.requested_name),
                basis=(f"Requested by {request.requested_by} and approved by {user.role} {user.username}; the vendor was "
                       "already recorded, unconfirmed, from an invoice that printed it."),
                reviewer=user.username, reviewer_user_id=user.id, reviewer_role=user.role)
    request.status = REQUEST_APPROVED
    request.vendor_id = vendor.id
    request.decided_by, request.decided_by_user_id, request.decided_at = user.username, user.id, datetime.now(UTC)
    request.decision_note = (note or "").strip() or None
    repointed = await _point_waiting_invoices(session, request, vendor)
    await session.flush()
    return request, vendor, repointed


async def reject_request(session: AsyncSession, request_id: uuid.UUID, user: User, *, note: str | None) -> VendorRequest:
    if not is_manager(user):
        raise PermissionDeniedError(message="Only a manager or administrator can decide a vendor request.",
                                    detail={"required_role": UserRole.MANAGER.value, "actual_role": user.role})
    text = (note or "").strip()
    if not text:
        raise ValidationError(message="Say why the request is rejected.", detail={"field": "note", "reason": "required"})
    request = await _locked_pending(session, request_id)
    request.status = REQUEST_REJECTED
    request.decided_by, request.decided_by_user_id, request.decided_at = user.username, user.id, datetime.now(UTC)
    request.decision_note = text
    await session.flush()
    return request


async def _point_waiting_invoices(session: AsyncSession, request: VendorRequest, vendor: Vendor) -> list[uuid.UUID]:
    """Every invoice uploaded against the request now has the approved vendor (chosen by a person) and is revalidated."""
    from app.repositories.invoice_repository import InvoiceRepository
    from app.services.revalidation_service import revalidate_invoice

    documents = (await session.execute(select(Document).where(Document.vendor_request_id == request.id))).scalars().all()
    repointed: list[uuid.UUID] = []
    for document in documents:
        document.chosen_vendor_id = vendor.id
        invoice_id = (await session.execute(select(Invoice.id).where(Invoice.document_id == document.id))).scalar_one_or_none()
        if invoice_id is None:
            continue                                         # still being processed: persistence will see chosen_vendor_id
        await session.execute(select(Invoice.id).where(Invoice.id == invoice_id).with_for_update())
        invoice = await InvoiceRepository(session).get_detail(invoice_id, fresh=True)
        invoice.vendor_id, invoice.vendor_source = vendor.id, SOURCE_OPERATOR
        await session.flush()
        await revalidate_invoice(session, invoice)
        repointed.append(invoice_id)
    return repointed
