"""
Product Master commercial review — app/services/master_commercial_review_service.py

The governed transition a reviewer performs on a commercial candidate:

    REVIEW_REQUIRED ──approve──▶ APPROVED
                    ──reject───▶ REJECTED

Approval is master-data governance and nothing more. `product_case_mappings`
remains the only source the EDI writer reads, so approving a row here
changes no export, no eligibility and no invoice. That is deliberate: the
Product Master becomes authoritative in a later phase, by an explicit
decision, not as a side effect of a reviewer clicking approve.

Two rules the service enforces rather than trusting the UI to:

  * a CONFLICT candidate cannot be approved as-is. The evidence produced no
    multiplier, so a reviewer has to supply the interpretation explicitly —
    a basis and, where that basis requires one, a number.
  * cost status is independent. A mapping whose sources disagreed on cost
    can still be approved on sound units evidence; the unresolved cost
    stays visible rather than blocking or being silently filled in.

Every decision appends a `MasterCommercialReview` row carrying the values
the mapping held before. Evidence is never overwritten.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError, ValidationError
from app.models.product_master import (
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    COMMERCIAL_CONFLICT,
    COMMERCIAL_UNIT_IS_SELLING_UNIT,
    COMMERCIAL_UNKNOWN,
    DECISION_APPROVE,
    DECISION_PROPOSE,
    DECISION_REJECT,
    MAX_UNITS_ACCOUNTED_FOR,
    MIN_UNITS_ACCOUNTED_FOR,
    STATE_APPROVED,
    STATE_PENDING,
    STATE_REJECTED,
    STATE_REVIEW_REQUIRED,
    MasterCommercialReview,
)
from app.repositories.master_commercial_repository import MasterCommercialRepository

# A basis a reviewer may settle a candidate on. UNKNOWN and CONFLICT are
# states of unresolved evidence, not conclusions a reviewer can record.
RESOLVABLE_BASES = frozenset({
    COMMERCIAL_CASE_IS_SELLING_UNIT, COMMERCIAL_UNIT_IS_SELLING_UNIT,
})


@dataclass
class ReviewOutcome:
    mapping_id: uuid.UUID
    previous_state: str
    new_state: str
    commercial_unit_basis: str
    units_accounted_for: int | None


def _validate_resolution(basis: str, units: int | None) -> tuple[str, int | None]:
    """
    Check the interpretation a reviewer supplied for a candidate.

    The pairing matters: a case that IS the selling unit accounts for
    exactly one, and a case that breaks into units must say how many.
    """
    if basis not in RESOLVABLE_BASES:
        raise ValidationError(
            message=(
                f"{basis!r} is not a decision. Choose whether the PDI item prices the "
                "case or one contained unit."
            ),
            detail={"field": "commercial_unit_basis", "allowed": sorted(RESOLVABLE_BASES)},
        )
    if basis == COMMERCIAL_CASE_IS_SELLING_UNIT:
        if units not in (None, 1):
            raise ValidationError(
                message=(
                    "A case that is itself the selling unit accounts for 1. Choose "
                    "'contained unit is the selling unit' to record a larger multiplier."
                ),
                detail={"field": "units_accounted_for", "expected": 1, "received": units},
            )
        return basis, 1
    if units is None:
        raise ValidationError(
            message=(
                "Say how many sellable units the case accounts for. PDI multiplies "
                "Item Retail by this value."
            ),
            detail={"field": "units_accounted_for", "reason": "required"},
        )
    if not MIN_UNITS_ACCOUNTED_FOR <= units <= MAX_UNITS_ACCOUNTED_FOR:
        raise ValidationError(
            message=f"Units must be between {MIN_UNITS_ACCOUNTED_FOR} and "
                    f"{MAX_UNITS_ACCOUNTED_FOR}.",
            detail={"field": "units_accounted_for", "received": units},
        )
    if units == 1:
        raise ValidationError(
            message=(
                "A multiplier of 1 means the case is the selling unit. Choose that "
                "basis instead."
            ),
            detail={"field": "units_accounted_for", "received": 1},
        )
    return basis, units


async def approve(
    session: AsyncSession,
    mapping_id: uuid.UUID,
    *,
    reviewer: str,
    note: str | None = None,
    commercial_unit_basis: str | None = None,
    units_accounted_for: int | None = None,
) -> ReviewOutcome:
    """
    Approve one candidate, resolving its interpretation if it had none.

    Flushed in the caller's transaction; the API layer commits.
    """
    repository = MasterCommercialRepository(session)
    row = await repository.get(mapping_id)
    if row is None:
        raise RecordNotFoundError(
            message="Commercial candidate not found.", detail={"mapping_id": str(mapping_id)},
        )
    mapping = row[0]

    if mapping.approval_state == STATE_APPROVED:
        # Already settled. Re-approving is a no-op rather than a second
        # history entry, so a double-submit cannot rewrite the decision.
        return ReviewOutcome(
            mapping.id, STATE_APPROVED, STATE_APPROVED,
            mapping.commercial_unit_basis, mapping.units_accounted_for,
        )
    if mapping.approval_state == STATE_REJECTED:
        raise ValidationError(
            message="This candidate was rejected. Reopen it before approving.",
            detail={"approval_state": mapping.approval_state},
        )

    # A pending proposal supplies the number when the reviewer does not
    # override it — approving a proposal is what promotes it.
    if (mapping.approval_state == STATE_PENDING
            and commercial_unit_basis is None
            and units_accounted_for is None
            and mapping.proposed_units_accounted_for is not None):
        units_accounted_for = mapping.proposed_units_accounted_for
        commercial_unit_basis = (
            COMMERCIAL_CASE_IS_SELLING_UNIT if units_accounted_for == 1
            else COMMERCIAL_UNIT_IS_SELLING_UNIT
        )

    previous_state = mapping.approval_state
    previous_basis = mapping.commercial_unit_basis
    previous_units = mapping.units_accounted_for

    needs_resolution = mapping.commercial_unit_basis in {COMMERCIAL_CONFLICT, COMMERCIAL_UNKNOWN}
    if needs_resolution:
        if commercial_unit_basis is None:
            raise ValidationError(
                message=(
                    "The evidence did not settle this candidate. Choose the commercial "
                    "interpretation the evidence supports, or reject it."
                ),
                detail={"commercial_unit_basis": mapping.commercial_unit_basis,
                        "reason": "resolution_required"},
            )
        basis, units = _validate_resolution(commercial_unit_basis, units_accounted_for)
    elif commercial_unit_basis is not None:
        # A reviewer may also correct a candidate the evidence did settle.
        basis, units = _validate_resolution(commercial_unit_basis, units_accounted_for)
    else:
        basis, units = mapping.commercial_unit_basis, mapping.units_accounted_for
        if units is None:
            raise ValidationError(
                message="This candidate carries no multiplier. Resolve it before approving.",
                detail={"reason": "units_missing"},
            )

    mapping.commercial_unit_basis = basis
    mapping.units_accounted_for = units
    mapping.approval_state = STATE_APPROVED
    mapping.reviewed_by = reviewer
    mapping.reviewed_at = datetime.now(UTC)

    await repository.add_review(MasterCommercialReview(
        mapping_id=mapping.id,
        decision=DECISION_APPROVE,
        previous_approval_state=previous_state,
        new_approval_state=STATE_APPROVED,
        previous_commercial_unit_basis=previous_basis,
        new_commercial_unit_basis=basis,
        previous_units_accounted_for=previous_units,
        new_units_accounted_for=units,
        reviewer=reviewer,
        note=note,
        # A snapshot, so the decision stays explicable even if the candidate
        # is later re-derived from changed source data.
        evidence_considered=dict(mapping.evidence or {}),
    ))
    return ReviewOutcome(mapping.id, previous_state, STATE_APPROVED, basis, units)


async def reject(
    session: AsyncSession,
    mapping_id: uuid.UUID,
    *,
    reviewer: str,
    note: str | None = None,
) -> ReviewOutcome:
    """Refuse a candidate. The row is kept — rejection is a decision, not a delete."""
    repository = MasterCommercialRepository(session)
    row = await repository.get(mapping_id)
    if row is None:
        raise RecordNotFoundError(
            message="Commercial candidate not found.", detail={"mapping_id": str(mapping_id)},
        )
    mapping = row[0]
    if mapping.approval_state == STATE_REJECTED:
        return ReviewOutcome(
            mapping.id, STATE_REJECTED, STATE_REJECTED,
            mapping.commercial_unit_basis, mapping.units_accounted_for,
        )

    previous_state = mapping.approval_state
    mapping.approval_state = STATE_REJECTED
    mapping.reviewed_by = reviewer
    mapping.reviewed_at = datetime.now(UTC)

    await repository.add_review(MasterCommercialReview(
        mapping_id=mapping.id,
        decision=DECISION_REJECT,
        previous_approval_state=previous_state,
        new_approval_state=STATE_REJECTED,
        previous_commercial_unit_basis=mapping.commercial_unit_basis,
        new_commercial_unit_basis=mapping.commercial_unit_basis,
        previous_units_accounted_for=mapping.units_accounted_for,
        new_units_accounted_for=mapping.units_accounted_for,
        reviewer=reviewer,
        note=note,
        evidence_considered=dict(mapping.evidence or {}),
    ))
    return ReviewOutcome(
        mapping.id, previous_state, STATE_REJECTED,
        mapping.commercial_unit_basis, mapping.units_accounted_for,
    )


def requires_resolution(mapping) -> bool:
    """Whether a reviewer must supply an interpretation before approving."""
    return (
        mapping.commercial_unit_basis in {COMMERCIAL_CONFLICT, COMMERCIAL_UNKNOWN}
        or mapping.units_accounted_for is None
    )


def is_open(mapping) -> bool:
    return mapping.approval_state == STATE_REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# Derived review status — what a reviewer needs the queue split by
# ---------------------------------------------------------------------------

# A candidate's position in the review workflow. Derived, never stored: it
# is a view over approval_state + the evidence, so it can never drift from
# them.
REVIEW_READY = "READY_FOR_REVIEW"
REVIEW_PENDING = "PENDING"
REVIEW_CONFLICT = "CONFLICT"
REVIEW_NO_MULTIPLIER = "NO_MULTIPLIER"
REVIEW_APPROVED = "APPROVED"
REVIEW_REJECTED = "REJECTED"
VALID_REVIEW_STATUSES = frozenset({
    REVIEW_READY, REVIEW_PENDING, REVIEW_CONFLICT, REVIEW_NO_MULTIPLIER,
    REVIEW_APPROVED, REVIEW_REJECTED,
})

# How an existing governed mapping relates to the candidate. Legacy may
# corroborate or dissent; it never supplies a value.
LEGACY_AGREES = "AGREES"
LEGACY_DISSENTS = "DISSENTS"
LEGACY_ABSENT = "NO_LEGACY_MAPPING"
LEGACY_UNCOMPARABLE = "MASTER_HAS_NO_VALUE"


def review_status(mapping) -> str:
    """Which queue a candidate belongs in. A decided row keeps its decision."""
    if mapping.approval_state == STATE_APPROVED:
        return REVIEW_APPROVED
    if mapping.approval_state == STATE_REJECTED:
        return REVIEW_REJECTED
    if mapping.approval_state == STATE_PENDING:
        # A proposal is waiting on a person; that outranks what the
        # evidence alone would have said.
        return REVIEW_PENDING
    if mapping.commercial_unit_basis == COMMERCIAL_CONFLICT:
        return REVIEW_CONFLICT
    if mapping.units_accounted_for is None:
        return REVIEW_NO_MULTIPLIER
    return REVIEW_READY


def legacy_agreement(master_units: int | None, legacy_units: set[int]) -> str:
    """
    Whether the governed mapping corroborates the candidate.

    Reported so a reviewer sees the disagreement; it does not change the
    candidate's value, which stays whatever the evidence produced.
    """
    if not legacy_units:
        return LEGACY_ABSENT
    if master_units is None:
        return LEGACY_UNCOMPARABLE
    return LEGACY_AGREES if legacy_units == {master_units} else LEGACY_DISSENTS


async def propose(
    session: AsyncSession,
    mapping_id: uuid.UUID,
    *,
    units_accounted_for: int,
    proposer: str,
    note: str | None = None,
) -> ReviewOutcome:
    """
    Record a suggested multiplier. This is NOT an authoritative write.

    A USER may reach this; approval remains MANAGER/ADMIN. The candidate's
    own `units_accounted_for` and `commercial_unit_basis` are left exactly
    as the evidence produced them — a proposal sits beside them in
    `proposed_*` and moves the row to PENDING so a reviewer sees it.

    Flushed in the caller's transaction, like approve()/reject().
    """
    repository = MasterCommercialRepository(session)
    row = await repository.get(mapping_id)
    if row is None:
        raise RecordNotFoundError(
            message="Commercial candidate not found.", detail={"mapping_id": str(mapping_id)},
        )
    mapping = row[0]

    if mapping.approval_state in {STATE_APPROVED, STATE_REJECTED}:
        raise ValidationError(
            message="This candidate has already been decided; a proposal cannot change it.",
            detail={"approval_state": mapping.approval_state},
        )
    if not MIN_UNITS_ACCOUNTED_FOR <= units_accounted_for <= MAX_UNITS_ACCOUNTED_FOR:
        raise ValidationError(
            message=f"Units must be between {MIN_UNITS_ACCOUNTED_FOR} and "
                    f"{MAX_UNITS_ACCOUNTED_FOR}.",
            detail={"field": "units_accounted_for", "received": units_accounted_for},
        )

    previous_state = mapping.approval_state
    mapping.proposed_units_accounted_for = units_accounted_for
    mapping.proposed_by = proposer
    mapping.proposed_at = datetime.now(UTC)
    mapping.proposed_note = note
    mapping.approval_state = STATE_PENDING

    await repository.add_review(MasterCommercialReview(
        mapping_id=mapping.id,
        decision=DECISION_PROPOSE,
        previous_approval_state=previous_state,
        new_approval_state=STATE_PENDING,
        previous_commercial_unit_basis=mapping.commercial_unit_basis,
        # A proposal does not reinterpret the evidence; only the number is
        # suggested, and the basis stays whatever the evidence said.
        new_commercial_unit_basis=mapping.commercial_unit_basis,
        previous_units_accounted_for=mapping.units_accounted_for,
        new_units_accounted_for=units_accounted_for,
        reviewer=proposer,
        note=note,
        evidence_considered=dict(mapping.evidence or {}),
    ))
    return ReviewOutcome(
        mapping.id, previous_state, STATE_PENDING,
        mapping.commercial_unit_basis, units_accounted_for,
    )
