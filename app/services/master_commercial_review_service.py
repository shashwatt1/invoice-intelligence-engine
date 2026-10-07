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
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RecordNotFoundError, StaleReviewError, ValidationError
from app.models.product_master import (
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    COMMERCIAL_CONFLICT,
    COMMERCIAL_UNIT_IS_SELLING_UNIT,
    COMMERCIAL_UNKNOWN,
    DECISION_APPROVE,
    DECISION_PROPOSE,
    DECISION_REJECT,
    DECISION_REOPEN,
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


def _require_basis(note: str | None, decision: str) -> str:
    """
    The person's own words for what a decision, proposal or reopening rests
    on. Required: an unexplained decision cannot be audited. Checked here, not
    only in the UI, so no client can record one.
    """
    text = (note or "").strip()
    if not text:
        what = {DECISION_APPROVE: "approval", DECISION_REJECT: "rejection",
                DECISION_PROPOSE: "proposal", DECISION_REOPEN: "reconsideration"}.get(decision, "decision")
        raise ValidationError(
            message=f"Say what this {what} rests on — the decision basis is recorded with it.",
            detail={"field": "note", "reason": "required", "decision": decision},
        )
    return text


async def _check_version(repository, mapping, expected: int | None) -> None:
    """
    Refuse a decision made against a view that is no longer current.

    `expected` is the review version (the number of review events) the person
    saw. The mapping row is locked (get(..., for_update=True)), so a concurrent
    decision either committed first — and moved the version — or waits for
    this one. Nothing is written for a stale decision.
    """
    if expected is None:
        return
    current = await repository.review_version(mapping.id)
    if current != expected:
        raise StaleReviewError(detail={
            "mapping_id": str(mapping.id), "expected_review_version": expected,
            "review_version": current, "approval_state": mapping.approval_state,
        })


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
    reviewer_user_id: uuid.UUID | None = None,
    reviewer_role: str | None = None,
    expected_review_version: int | None = None,
) -> ReviewOutcome:
    """
    Approve one candidate, resolving its interpretation if it had none.

    With `expected_review_version` (the API always sends it), a decision made
    against a view that is no longer current is refused (StaleReviewError).
    Flushed in the caller's transaction; the API layer commits.
    """
    repository = MasterCommercialRepository(session)
    row = await repository.get(mapping_id, for_update=True)
    if row is None:
        raise RecordNotFoundError(
            message="Commercial candidate not found.", detail={"mapping_id": str(mapping_id)},
        )
    mapping = row[0]
    await _check_version(repository, mapping, expected_review_version)

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
    note = _require_basis(note, DECISION_APPROVE)

    outcome, review = _approval(
        mapping, reviewer=reviewer, note=note,
        commercial_unit_basis=commercial_unit_basis, units_accounted_for=units_accounted_for,
        reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role,
    )
    await repository.add_review(review)
    return outcome


def _approval(
    mapping,
    *,
    reviewer: str,
    note: str,
    commercial_unit_basis: str | None,
    units_accounted_for: int | None,
    reviewer_user_id: uuid.UUID | None,
    reviewer_role: str | None,
) -> tuple[ReviewOutcome, MasterCommercialReview]:
    """
    The approval decision itself, shared by approve() and bulk_approve(): settle
    the interpretation, move the mapping to APPROVED and build its review event
    (who, role, basis, before/after, evidence snapshot).

    The caller has already locked the row, checked the version it was decided
    against, refused a decided candidate and required a basis; the caller adds
    the review event. Nothing is flushed here.
    """
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

    review = MasterCommercialReview(
        mapping_id=mapping.id,
        decision=DECISION_APPROVE,
        previous_approval_state=previous_state,
        new_approval_state=STATE_APPROVED,
        previous_commercial_unit_basis=previous_basis,
        new_commercial_unit_basis=basis,
        previous_units_accounted_for=previous_units,
        new_units_accounted_for=units,
        reviewer=reviewer,
        reviewer_user_id=reviewer_user_id,
        reviewer_role=reviewer_role,
        note=note,
        # A snapshot, so the decision stays explicable even if the candidate
        # is later re-derived from changed source data.
        evidence_considered=dict(mapping.evidence or {}),
    )
    return ReviewOutcome(mapping.id, previous_state, STATE_APPROVED, basis, units), review


async def reject(
    session: AsyncSession,
    mapping_id: uuid.UUID,
    *,
    reviewer: str,
    note: str | None = None,
    reviewer_user_id: uuid.UUID | None = None,
    reviewer_role: str | None = None,
    expected_review_version: int | None = None,
) -> ReviewOutcome:
    """Refuse a candidate. The row is kept — rejection is a decision, not a delete."""
    repository = MasterCommercialRepository(session)
    row = await repository.get(mapping_id, for_update=True)
    if row is None:
        raise RecordNotFoundError(
            message="Commercial candidate not found.", detail={"mapping_id": str(mapping_id)},
        )
    mapping = row[0]
    await _check_version(repository, mapping, expected_review_version)
    if mapping.approval_state == STATE_REJECTED:
        return ReviewOutcome(
            mapping.id, STATE_REJECTED, STATE_REJECTED,
            mapping.commercial_unit_basis, mapping.units_accounted_for,
        )

    note = _require_basis(note, DECISION_REJECT)
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
        reviewer_user_id=reviewer_user_id,
        reviewer_role=reviewer_role,
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
    proposer_user_id: uuid.UUID | None = None,
    proposer_role: str | None = None,
) -> ReviewOutcome:
    """
    Record a suggested multiplier. This is NOT an authoritative write.

    A USER may reach this; approval remains MANAGER/ADMIN. The candidate's
    own `units_accounted_for` and `commercial_unit_basis` are left exactly
    as the evidence produced them — a proposal sits beside them in
    `proposed_*` and moves the row to PENDING so a reviewer sees it.

    Flushed in the caller's transaction, like approve()/reject(). The
    proposer must say what the proposal rests on.
    """
    repository = MasterCommercialRepository(session)
    row = await repository.get(mapping_id, for_update=True)
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
    note = _require_basis(note, DECISION_PROPOSE)
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
        reviewer_user_id=proposer_user_id,
        reviewer_role=proposer_role,
        note=note,
        evidence_considered=dict(mapping.evidence or {}),
    ))
    return ReviewOutcome(
        mapping.id, previous_state, STATE_PENDING,
        mapping.commercial_unit_basis, units_accounted_for,
    )


# ---------------------------------------------------------------------------
# Reconsideration and multi-select approval — the same governance, not a bypass
# ---------------------------------------------------------------------------

async def reopen(
    session: AsyncSession,
    mapping_id: uuid.UUID,
    *,
    reviewer: str,
    reason: str | None,
    reviewer_user_id: uuid.UUID | None = None,
    reviewer_role: str | None = None,
    expected_review_version: int | None = None,
) -> ReviewOutcome:
    """
    Reopen a decided candidate for reconsideration — never an undo.

    The decision being reconsidered stays in the history untouched; this adds a
    REOPEN event with the reason and who reopened it. The candidate returns to
    REVIEW_REQUIRED with the interpretation the evidence gave BEFORE that
    decision (a resolved conflict is a conflict again), so reconsideration
    starts from the evidence, not from the decision being questioned.
    """
    repository = MasterCommercialRepository(session)
    row = await repository.get(mapping_id, for_update=True)
    if row is None:
        raise RecordNotFoundError(
            message="Commercial candidate not found.", detail={"mapping_id": str(mapping_id)},
        )
    mapping = row[0]
    await _check_version(repository, mapping, expected_review_version)
    if mapping.approval_state not in {STATE_APPROVED, STATE_REJECTED}:
        raise ValidationError(
            message="Only an approved or rejected candidate can be reopened.",
            detail={"approval_state": mapping.approval_state},
        )
    reason_text = _require_basis(reason, DECISION_REOPEN)

    decided = next((entry for entry in await repository.history_for(mapping.id)
                    if entry.decision in {DECISION_APPROVE, DECISION_REJECT}), None)
    previous_state = mapping.approval_state
    previous_basis, previous_units = mapping.commercial_unit_basis, mapping.units_accounted_for
    restored_basis = decided.previous_commercial_unit_basis if decided else previous_basis
    restored_units = decided.previous_units_accounted_for if decided else previous_units

    mapping.approval_state = STATE_REVIEW_REQUIRED
    mapping.commercial_unit_basis = restored_basis
    mapping.units_accounted_for = restored_units
    # The current decision no longer stands; who made it stays in the history.
    mapping.reviewed_by = None
    mapping.reviewed_at = None

    await repository.add_review(MasterCommercialReview(
        mapping_id=mapping.id,
        decision=DECISION_REOPEN,
        previous_approval_state=previous_state,
        new_approval_state=STATE_REVIEW_REQUIRED,
        previous_commercial_unit_basis=previous_basis,
        new_commercial_unit_basis=restored_basis,
        previous_units_accounted_for=previous_units,
        new_units_accounted_for=restored_units,
        reviewer=reviewer,
        reviewer_user_id=reviewer_user_id,
        reviewer_role=reviewer_role,
        note=reason_text,
        evidence_considered=dict(mapping.evidence or {}),
    ))
    return ReviewOutcome(mapping.id, previous_state, STATE_REVIEW_REQUIRED, restored_basis, restored_units)


MAX_BULK_APPROVAL = 500


@dataclass(frozen=True)
class BulkApprovalItem:
    mapping_id: uuid.UUID
    expected_review_version: int


def bulk_eligible(mapping, latest_decision: str | None) -> bool:
    """
    Whether a candidate may be approved as part of a multi-select approval: it
    must be READY_FOR_REVIEW — the evidence settled a multiplier, there is no
    conflict and no pending proposal — and not just reopened for reconsideration.
    Everything else needs an individual decision.
    """
    return review_status(mapping) == REVIEW_READY and latest_decision != DECISION_REOPEN


async def bulk_approve(
    session: AsyncSession,
    items: list[BulkApprovalItem],
    *,
    reviewer: str,
    note: str | None,
    reviewer_user_id: uuid.UUID | None = None,
    reviewer_role: str | None = None,
) -> list[ReviewOutcome]:
    """
    Approve several candidates in one transaction, each by the same decision
    as approve() (_approval).

    All or nothing: every selected candidate is locked (one statement, id
    order) and checked first. If any changed since the reviewer saw it, the
    whole request is refused (StaleReviewError) and nothing is written; if any
    is not eligible for a multi-select approval (a conflict, a pending
    proposal, no multiplier, a reopened or already decided candidate), it is
    refused (ValidationError). Otherwise each is approved, writing one review
    event per candidate with the reviewer, role, basis and evidence snapshot —
    never a single summary record.

    The checks are made once for the whole selection rather than again per
    candidate: the locks are held until the caller commits, so nothing can
    change between the check and the write. All events are flushed together.
    """
    if not items:
        raise ValidationError(message="Select at least one mapping to approve.", detail={"field": "items"})
    if len(items) > MAX_BULK_APPROVAL:
        raise ValidationError(message=f"Approve at most {MAX_BULK_APPROVAL} mappings at a time.",
                              detail={"field": "items", "limit": MAX_BULK_APPROVAL})
    ids = [item.mapping_id for item in items]
    if len(set(ids)) != len(ids):
        raise ValidationError(message="A mapping was selected twice.", detail={"field": "items"})
    basis = _require_basis(note, DECISION_APPROVE)

    repository = MasterCommercialRepository(session)
    locked = await repository.lock_many(ids)
    facts = await repository.review_facts(ids)

    stale: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []
    for item in items:
        mapping = locked.get(item.mapping_id)
        if mapping is None:
            ineligible.append({"mapping_id": str(item.mapping_id), "reason": "not_found"})
            continue
        version, latest = facts.get(item.mapping_id, (0, None))
        if version != item.expected_review_version:
            stale.append({"mapping_id": str(item.mapping_id), "expected_review_version": item.expected_review_version,
                          "review_version": version, "approval_state": mapping.approval_state})
        elif not bulk_eligible(mapping, latest):
            ineligible.append({"mapping_id": str(item.mapping_id), "reason": "individual_review_required",
                               "review_status": review_status(mapping), "latest_decision": latest})
    if stale:
        raise StaleReviewError(
            message="The queue changed since you selected these mappings. Refresh and select again — nothing was approved.",
            detail={"stale": stale},
        )
    if ineligible:
        raise ValidationError(
            message="Some selected mappings need an individual decision. Nothing was approved.",
            detail={"ineligible": ineligible},
        )
    decisions = [
        _approval(
            locked[item.mapping_id], reviewer=reviewer, note=basis,
            commercial_unit_basis=None, units_accounted_for=None,
            reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role,
        )
        for item in items
    ]
    await repository.add_reviews([review for _, review in decisions])
    return [outcome for outcome, _ in decisions]
