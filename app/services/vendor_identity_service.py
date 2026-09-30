"""
Vendor identity governance — app/services/vendor_identity_service.py

The only way a vendor's identity becomes confirmed: a MANAGER or ADMIN
confirms it, naming the canonical vendor and saying what the decision rests
on. Nothing here merges vendors, re-keys invoices or rewrites what an invoice
printed; the extraction pipeline keeps creating unresolved vendors exactly as
before and is never blocked by an unresolved one.

Who decided always comes from the authenticated session (the API passes it);
it is never read from a request body. Every decision appends one row to
vendor_identity_reviews with the evidence as it stood. Flushed in the
caller's transaction; the API layer commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError, ValidationError
from app.models.vendor import (
    DECISION_CONFIRM,
    DECISION_REOPEN,
    VENDOR_CONFIRMED,
    VENDOR_UNRESOLVED,
    Vendor,
    VendorIdentityReview,
)
from app.repositories.vendor_repository import VendorRepository

MAX_BASIS = 1000
MAX_NAME = 255


@dataclass
class VendorDecisionOutcome:
    vendor_id: uuid.UUID
    previous_status: str
    new_status: str
    display_name: str | None


def _require_text(value: str | None, *, field: str, what: str, limit: int) -> str:
    text = (value or "").strip()
    if not text:
        raise ValidationError(message=f"Say {what}.", detail={"field": field, "reason": "required"})
    if len(text) > limit:
        raise ValidationError(message=f"Keep {field} to {limit} characters.",
                              detail={"field": field, "reason": "too_long", "limit": limit})
    return text


async def _vendor(repository: VendorRepository, vendor_id: uuid.UUID) -> Vendor:
    vendor = await repository.get(vendor_id)
    if vendor is None:
        raise RecordNotFoundError(message="Vendor not found.", detail={"vendor_id": str(vendor_id)})
    return vendor


async def _evidence(repository: VendorRepository, vendor: Vendor) -> dict:
    """The evidence as it stood when deciding — kept with the decision."""
    names = await repository.observed_names(vendor.id)
    tax_ids = await repository.observed_tax_ids(vendor.id)
    return {
        "observed_name": vendor.name, "tax_id": vendor.tax_id, "address": vendor.address,
        "phone": vendor.phone, "email": vendor.email,
        "observed_names": [{"name": n["name"], "invoices": n["invoices"]} for n in names],
        "observed_tax_ids": tax_ids,
        "invoices": sum(n["invoices"] for n in names),
    }


async def confirm(
    session: AsyncSession,
    vendor_id: uuid.UUID,
    *,
    display_name: str | None,
    basis: str | None,
    reviewer: str,
    reviewer_user_id: uuid.UUID | None = None,
    reviewer_role: str | None = None,
) -> VendorDecisionOutcome:
    """Establish the vendor's canonical identity and name."""
    repository = VendorRepository(session)
    vendor = await _vendor(repository, vendor_id)
    name = _require_text(display_name, field="display_name", what="the canonical vendor name", limit=MAX_NAME)

    if vendor.identity_status == VENDOR_CONFIRMED:
        if (vendor.display_name or "") == name:
            # Already settled this way: a double submit must not add a second decision.
            return VendorDecisionOutcome(vendor.id, VENDOR_CONFIRMED, VENDOR_CONFIRMED, vendor.display_name)
        raise ValidationError(
            message="This vendor is already confirmed. Reopen it to change its canonical name.",
            detail={"identity_status": vendor.identity_status, "display_name": vendor.display_name},
        )
    basis_text = _require_text(basis, field="basis", what="what this confirmation rests on", limit=MAX_BASIS)

    other = await repository.confirmed_with_name(name, excluding=vendor.id)
    if other is not None:
        # Two confirmed vendors under one canonical name would be one vendor
        # recorded twice. Deciding that is a merge — a separate decision this
        # workflow does not make.
        raise ValidationError(
            message=(f"Another confirmed vendor is already named {other.display_name!r}. If these are the "
                     "same vendor, that is a merge decision, which is not made here."),
            detail={"field": "display_name", "reason": "name_taken", "vendor_id": str(other.id)},
        )

    evidence = await _evidence(repository, vendor)
    previous_status, previous_name = vendor.identity_status, vendor.display_name
    vendor.identity_status = VENDOR_CONFIRMED
    vendor.display_name = name
    await repository.add_review(VendorIdentityReview(
        vendor_id=vendor.id, decision=DECISION_CONFIRM,
        previous_status=previous_status, new_status=VENDOR_CONFIRMED,
        previous_display_name=previous_name, new_display_name=name,
        reviewer=reviewer, reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role,
        basis=basis_text, evidence_considered=evidence,
    ))
    return VendorDecisionOutcome(vendor.id, previous_status, VENDOR_CONFIRMED, name)


async def reopen(
    session: AsyncSession,
    vendor_id: uuid.UUID,
    *,
    basis: str | None,
    reviewer: str,
    reviewer_user_id: uuid.UUID | None = None,
    reviewer_role: str | None = None,
) -> VendorDecisionOutcome:
    """Return a confirmed vendor to unresolved — the confirmation stays in the history."""
    repository = VendorRepository(session)
    vendor = await _vendor(repository, vendor_id)
    if vendor.identity_status == VENDOR_UNRESOLVED:
        return VendorDecisionOutcome(vendor.id, VENDOR_UNRESOLVED, VENDOR_UNRESOLVED, vendor.display_name)
    basis_text = _require_text(basis, field="basis", what="why this vendor is reopened", limit=MAX_BASIS)

    evidence = await _evidence(repository, vendor)
    previous_name = vendor.display_name
    vendor.identity_status = VENDOR_UNRESOLVED
    vendor.display_name = None
    await repository.add_review(VendorIdentityReview(
        vendor_id=vendor.id, decision=DECISION_REOPEN,
        previous_status=VENDOR_CONFIRMED, new_status=VENDOR_UNRESOLVED,
        previous_display_name=previous_name, new_display_name=None,
        reviewer=reviewer, reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role,
        basis=basis_text, evidence_considered=evidence,
    ))
    return VendorDecisionOutcome(vendor.id, VENDOR_CONFIRMED, VENDOR_UNRESOLVED, None)
