"""
Choosing a vendor at upload — app/services/vendor_request_service.py

* An ADMIN adds a vendor that is not listed directly: it is created and confirmed at once, so it shows in the picker straight
  away, with the audit row the Vendor Master already keeps.
* A USER or MANAGER asks for one (`request_vendor`): the request is logged (who, when, the name) and nothing enters the Vendor
  Master. The invoice uploaded against it is processed all the way (corrections, exports, Retailed, the inventory receipt) and
  reads "vendor pending approval"; it has no vendor until an ADMIN decides, so no vendor-wise view counts it in the meantime.
* Only an ADMIN approves or rejects. Approving creates the vendor (or adopts the unconfirmed row extraction once recorded, or
  links the request to the vendor it duplicates) and points every waiting invoice at it. Rejecting leaves those invoices to
  have a vendor chosen.

A name that matches a confirmed vendor (see vendor_matching) is never created again: the caller is told which vendor it is.
A match with a vendor that was only ever extracted (unconfirmed, so not offered in the picker) does not block a request; the
ADMIN's approval adopts that row instead of creating a second one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError, RecordNotFoundError, ValidationError
from app.models.document import Document
from app.models.invoice import SOURCE_OPERATOR, SOURCE_REQUESTED, Invoice
from app.models.processing_log import PipelineStage
from app.models.store import Store
from app.models.user import User, UserRole
from app.models.vendor import VENDOR_CONFIRMED, Vendor
from app.models.vendor_request import (
    REQUEST_APPROVED,
    REQUEST_PENDING,
    REQUEST_REJECTED,
    VendorRequest,
)
from app.repositories.processing_log_repository import ProcessingLogRepository
from app.repositories.vendor_repository import resolve_vendor
from app.services import vendor_identity_service
from app.services.vendor_matching import names_match, normalize_vendor_name


def is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN.value


def _admin_only(user: User, what: str) -> None:
    if not is_admin(user):
        raise PermissionDeniedError(message=f"Only an administrator can {what}. Ask for the vendor instead; an administrator "
                                            "approves it.",
                                    detail={"required_role": UserRole.ADMIN.value, "actual_role": user.role})


def clean_name(name: str | None) -> str:
    text = " ".join((name or "").split())
    if len(text) < 2:
        raise ValidationError(message="Enter the vendor's name.", detail={"field": "name", "reason": "required"})
    if len(text) > 255:
        raise ValidationError(message="Keep the vendor name to 255 characters.", detail={"field": "name", "reason": "too_long"})
    return text


async def existing_vendor_named(session: AsyncSession, name: str, *, confirmed_only: bool = False) -> Vendor | None:
    """A vendor already recorded under this name (or a spelling that matches it), confirmed ones first."""
    query = select(Vendor).where(Vendor.merged_into_id.is_(None))
    if confirmed_only:
        query = query.where(Vendor.identity_status == VENDOR_CONFIRMED)
    vendors = sorted((await session.execute(query)).scalars().all(), key=lambda v: v.identity_status != VENDOR_CONFIRMED)
    for vendor in vendors:
        if names_match(name, vendor.display_name) or names_match(name, vendor.name):
            return vendor
    return None


def _already_listed(vendor: Vendor) -> ValidationError:
    label = vendor.display_name or vendor.name
    return ValidationError(
        message=f"{label!r} is already in the vendor list. Choose it instead of adding it again.",
        detail={"field": "name", "reason": "already_listed", "vendor_id": str(vendor.id), "vendor": label})


async def _create_or_adopt(session: AsyncSession, user: User, name: str, basis: str) -> Vendor:
    """
    The confirmed vendor for `name`: refused when a confirmed one already holds it; an unconfirmed row extraction recorded
    for the same name is confirmed rather than duplicated; otherwise a new vendor is created and confirmed.
    """
    found = await existing_vendor_named(session, name)
    if found is not None and found.identity_status == VENDOR_CONFIRMED:
        raise _already_listed(found)
    vendor = found
    if vendor is None:
        vendor = Vendor(name=name)
        session.add(vendor)
        await session.flush()
    await vendor_identity_service.confirm(session, vendor.id, display_name=name, basis=basis, reviewer=user.username,
                                          reviewer_user_id=user.id, reviewer_role=user.role)
    return vendor


async def add_vendor(session: AsyncSession, user: User, name: str) -> Vendor:
    """ADMIN: create (or adopt and confirm) a vendor now. Flushes; the caller commits."""
    _admin_only(user, "add a vendor directly")
    text = clean_name(name)
    return await _create_or_adopt(session, user, text,
                                  f"Added to the vendor list by ADMIN {user.username} when processing an invoice that named no "
                                  "listed vendor.")


async def request_vendor(session: AsyncSession, user: User, name: str) -> VendorRequest:
    """
    Ask for a vendor to be added (any account; an ADMIN would normally add it directly). Joins the open request for the same
    name. Refused only when a confirmed vendor already holds the name. Flushes; the caller commits.
    """
    text = clean_name(name)
    found = await existing_vendor_named(session, text, confirmed_only=True)
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


async def pending_requests(session: AsyncSession) -> list[VendorRequest]:
    """The open requests: what the picker offers beside the confirmed vendors."""
    return list((await session.execute(select(VendorRequest).where(VendorRequest.status == REQUEST_PENDING)
                                       .order_by(VendorRequest.requested_name))).scalars().all())


async def waiting_invoices(session: AsyncSession, request_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """For each request, the invoices processed against it that still wait for its decision (what a decision affects)."""
    from app.services.inventory_service import posted_receipt

    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    if not request_ids:
        return out
    rows = (await session.execute(
        select(Document.vendor_request_id, Invoice.id, Invoice.invoice_number, Invoice.retailed_at, Store.display_name)
        .join(Invoice, Invoice.document_id == Document.id).outerjoin(Store, Store.id == Invoice.store_id)
        .where(Document.vendor_request_id.in_(request_ids), Invoice.vendor_source == SOURCE_REQUESTED)
        .order_by(Invoice.created_at))).all()
    for rid, invoice_id, number, retailed_at, store in rows:
        out.setdefault(rid, []).append({
            "invoice_id": invoice_id, "invoice_number": number, "store": store, "retailed": retailed_at is not None,
            "received": await posted_receipt(session, invoice_id) is not None})
    return out


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
    ADMIN: approve. Creates the vendor (or adopts the unconfirmed row extraction recorded for it), or links to
    `existing_vendor_id` when the request turns out to be a vendor already known, then points every waiting invoice at it.
    Returns (request, vendor, invoice ids pointed at it).
    """
    _admin_only(user, "approve a vendor request")
    request = await _locked_pending(session, request_id)
    basis = f"Requested by {request.requested_by}; approved by ADMIN {user.username}."
    if existing_vendor_id is not None:
        vendor = await resolve_vendor(session, await session.get(Vendor, existing_vendor_id))
        if vendor is None:
            raise RecordNotFoundError(message="Vendor not found.", detail={"vendor_id": str(existing_vendor_id)})
        if vendor.identity_status != VENDOR_CONFIRMED:
            await vendor_identity_service.confirm(session, vendor.id, display_name=vendor.display_name or vendor.name,
                                                  basis=f"{basis} The request was found to be this vendor.",
                                                  reviewer=user.username, reviewer_user_id=user.id, reviewer_role=user.role)
    else:
        vendor = await _create_or_adopt(session, user, clean_name(request.requested_name), basis)
    request.status = REQUEST_APPROVED
    request.vendor_id = vendor.id
    request.decided_by, request.decided_by_user_id, request.decided_at = user.username, user.id, datetime.now(UTC)
    request.decision_note = (note or "").strip() or None
    pointed = await _point_waiting_invoices(session, request, vendor, user)
    await session.flush()
    return request, vendor, pointed


async def reject_request(session: AsyncSession, request_id: uuid.UUID, user: User, *, note: str | None) -> VendorRequest:
    """ADMIN: reject with a reason. The invoices that waited keep no vendor and need one chosen from the list."""
    _admin_only(user, "reject a vendor request")
    text = (note or "").strip()
    if not text:
        raise ValidationError(message="Say why the request is rejected.", detail={"field": "note", "reason": "required"})
    request = await _locked_pending(session, request_id)
    request.status = REQUEST_REJECTED
    request.decided_by, request.decided_by_user_id, request.decided_at = user.username, user.id, datetime.now(UTC)
    request.decision_note = text
    logs = ProcessingLogRepository(session)
    rows = (await session.execute(
        select(Document.id, Invoice.id).join(Invoice, Invoice.document_id == Document.id)
        .where(Document.vendor_request_id == request.id, Invoice.vendor_source == SOURCE_REQUESTED))).all()
    for document_id, invoice_id in rows:
        await session.execute(select(Invoice.id).where(Invoice.id == invoice_id).with_for_update())
        invoice = await session.get(Invoice, invoice_id, populate_existing=True)
        invoice.vendor_source = None                          # undeclared again: a vendor has to be chosen from the list
        await logs.add(document_id=document_id, stage=PipelineStage.MANUAL_CORRECTION,
                       message=f"Vendor request {request.requested_name!r} rejected by {user.username}: choose a vendor.",
                       payload={"event": "vendor_request_rejected", "request_id": str(request.id), "by": user.username,
                                "note": text, "invoice_id": str(invoice_id)})
    await session.flush()
    return request


async def _point_waiting_invoices(session: AsyncSession, request: VendorRequest, vendor: Vendor,
                                  user: User) -> list[uuid.UUID]:
    """
    Every invoice processed against the request gets the approved vendor, as a person's choice. One still in progress is
    revalidated (the printed vendor is compared with it). One already marked Retailed, or received into inventory, only gets the
    vendor: revalidation may rewrite stored lines, which must not happen to an attested or received invoice, and neither the
    attestation nor the receipt covers the vendor.
    """
    from app.repositories.invoice_repository import InvoiceRepository
    from app.services.inventory_service import posted_receipt
    from app.services.revalidation_service import revalidate_invoice

    logs = ProcessingLogRepository(session)
    documents = (await session.execute(select(Document).where(Document.vendor_request_id == request.id))).scalars().all()
    pointed: list[uuid.UUID] = []
    for document in documents:
        document.chosen_vendor_id = vendor.id
        invoice_id = (await session.execute(select(Invoice.id).where(Invoice.document_id == document.id))).scalar_one_or_none()
        if invoice_id is None:
            continue                                         # still being processed: persistence will see chosen_vendor_id
        await session.execute(select(Invoice.id).where(Invoice.id == invoice_id).with_for_update())
        invoice = await InvoiceRepository(session).get_detail(invoice_id, fresh=True)
        if invoice.vendor_source != SOURCE_REQUESTED:
            continue                                         # a vendor was chosen for it meanwhile
        invoice.vendor_id, invoice.vendor_source = vendor.id, SOURCE_OPERATOR
        settled = invoice.retailed_at is not None or await posted_receipt(session, invoice_id) is not None
        await session.flush()
        if not settled:
            await revalidate_invoice(session, invoice)
        await logs.add(document_id=document.id, stage=PipelineStage.MANUAL_CORRECTION,
                       message=f"Vendor request approved by {user.username}: the invoice's vendor is {vendor.label}.",
                       payload={"event": "vendor_request_approved", "request_id": str(request.id), "vendor_id": str(vendor.id),
                                "by": user.username, "invoice_id": str(invoice_id), "revalidated": not settled})
        pointed.append(invoice_id)
    return pointed
