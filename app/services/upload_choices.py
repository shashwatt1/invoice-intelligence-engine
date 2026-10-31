"""
What the operator chose at upload besides the store — app/services/upload_choices.py

The vendor the invoice is from (a vendor in the list, or a request for one that is not) and the day the goods were
delivered. Checked before any file is touched, like the store: an unknown vendor or an impossible date refuses the upload and
nothing is stored.

The delivery date is a plain calendar date (YYYY-MM-DD) exactly as the person typed it — never the browser's "today", which is
the mistake the zero-start dialog once made. The latest accepted day is UTC today plus one, so a delivery on a US evening
(already "tomorrow" in UTC) is never refused as the future.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.models.user import UserRole
from app.models.vendor import Vendor
from app.models.vendor_request import REQUEST_APPROVED, REQUEST_PENDING, VendorRequest
from app.repositories.vendor_repository import resolve_vendor

EARLIEST_DELIVERY = date(2000, 1, 1)


@dataclass(frozen=True)
class UploadChoices:
    vendor_id: uuid.UUID | None = None
    vendor_request_id: uuid.UUID | None = None
    delivery_date: date | None = None


def parse_delivery_date(value: str | date | None, *, now: datetime | None = None) -> date | None:
    """The delivery date, or None when none was given. Raises ValidationError for a malformed or impossible one."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = value if isinstance(value, date) else date.fromisoformat(value.strip())
    except ValueError:
        raise ValidationError(message="The delivery date must be a calendar date like 2026-10-22.",
                              detail={"field": "delivery_date", "reason": "invalid"}) from None
    latest = ((now or datetime.now(UTC)).date()) + timedelta(days=1)
    if parsed > latest:
        raise ValidationError(message="The delivery date cannot be in the future.",
                              detail={"field": "delivery_date", "reason": "future_date", "latest": latest.isoformat()})
    if parsed < EARLIEST_DELIVERY:
        raise ValidationError(message="The delivery date is too far in the past.",
                              detail={"field": "delivery_date", "reason": "too_old"})
    return parsed


def _uuid(value: str | None, field: str) -> uuid.UUID | None:
    if value is None or not value.strip():
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        raise ValidationError(message=f"{field} is not a valid id.", detail={"field": field, "reason": "invalid"}) from None


async def resolve_upload_choices(session: AsyncSession, role: str, *, vendor_id: str | None,
                                 vendor_request_id: str | None, delivery_date: str | None) -> UploadChoices:
    vendor, request_id = _uuid(vendor_id, "vendor_id"), _uuid(vendor_request_id, "vendor_request_id")
    when = parse_delivery_date(delivery_date)
    if vendor is not None and request_id is not None:
        raise ValidationError(message="Choose a vendor from the list or ask for a new one, not both.",
                              detail={"field": "vendor_id", "reason": "both"})
    if get_settings().upload_requires_vendor_and_delivery and role != UserRole.ADMIN.value:
        if vendor is None and request_id is None:
            raise ValidationError(message="Choose the vendor this invoice is from before processing it.",
                                  detail={"field": "vendor_id", "reason": "required_for_role", "role": role})
        if when is None:
            raise ValidationError(message="Enter the delivery date (the day the goods arrived) before processing the invoice.",
                                  detail={"field": "delivery_date", "reason": "required_for_role", "role": role})
    if vendor is not None:
        # A vendor merged away since the picker was loaded is the vendor it was merged into.
        chosen = await resolve_vendor(session, await session.get(Vendor, vendor))
        if chosen is None:
            raise ValidationError(message="That vendor is not in the vendor list.",
                                  detail={"field": "vendor_id", "reason": "unknown_vendor", "value": str(vendor)})
        vendor = chosen.id
    if request_id is not None:
        row = await session.get(VendorRequest, request_id)
        if row is None or row.status not in (REQUEST_PENDING, REQUEST_APPROVED):
            raise ValidationError(message="That vendor request is not open.",
                                  detail={"field": "vendor_request_id", "reason": "request_not_open"})
        if row.status == REQUEST_APPROVED and row.vendor_id is not None:      # approved meanwhile: it is the vendor now
            approved = await resolve_vendor(session, await session.get(Vendor, row.vendor_id))
            return UploadChoices(vendor_id=approved.id if approved else row.vendor_id, delivery_date=when)
    return UploadChoices(vendor_id=vendor, vendor_request_id=request_id, delivery_date=when)
