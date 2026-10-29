"""
Proposal Service — app/services/proposal_service.py

The only code allowed to turn a proposal into authoritative master data.

Two entry points, both used by the review CLI today and by an API later:

  approve()  — freezes the proposal, then writes the authoritative target
               and links it back. One transaction: if either half fails,
               neither lands.
  reject()   — freezes the proposal. Nothing else changes.

And one for the front end:

  propose_case_mapping() — records what an operator confirmed as a
               PENDING proposal, working out from the invoice's own
               review data how the value was arrived at, so the reviewer
               is told "reference_derived" or "operator_entered" by the
               system rather than by whoever clicked.

And two for the review table's fast path:

  decide_many() — approve() or reject() over a batch, all-or-nothing:
               every row is checked to be PENDING before the first one
               is touched, so a stale selection refuses cleanly instead
               of half-landing.
  revise()   — a reviewer's correction of a pending value. A proposal is
               immutable, so this is a NEW pending proposal that records
               which one it revised, and the original is frozen as
               rejected/superseded in the same transaction. Master data
               is not touched; the revision still has to be approved.

Nothing here has a path that writes product_case_mappings without an
APPROVED proposal id in hand. That is the point of the module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.product_case_mapping import SOURCE_APPROVED
from app.models.product_data_proposal import (
    ENTITY_CASE_MAPPING,
    ENTITY_VENDOR_ITEM,
    FIELD_ITEM_CODE,
    FIELD_UNITS_PER_CASE,
    SOURCE_BEER_INVENTORY_EXPLICIT,
    SOURCE_BEER_INVENTORY_PACKAGE,
    SOURCE_CONFLICT_RESOLUTION,
    SOURCE_DOCUMENT_AMBIGUOUS,
    SOURCE_DOCUMENT_DERIVED,
    SOURCE_OPERATOR_ENTERED,
    SOURCE_REFERENCE_DERIVED,
    ProductDataProposal,
)
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import (
    ProductDataProposalRepository,
    ProposalImmutableError,
)
from app.services.case_mapping_service import (
    REFERENCE_SUGGESTION_SOURCES,
    SUGGESTION_FROM_REFERENCE_EXPLICIT,
    SUGGESTION_FROM_REFERENCE_PACKAGE,
    CaseMappingStatus,
)
from app.services.product_master import shared_decisions

# suggestion source -> proposal source
_REFERENCE_PROPOSAL_SOURCE = {
    SUGGESTION_FROM_REFERENCE_EXPLICIT: SOURCE_BEER_INVENTORY_EXPLICIT,
    SUGGESTION_FROM_REFERENCE_PACKAGE: SOURCE_BEER_INVENTORY_PACKAGE,
}


SCOPE_STORE = "store"        # this store only (the historical behaviour)
SCOPE_SHARED = "shared"      # every physical store: a shared Product Master value (0038)
VALID_SCOPES = frozenset({SCOPE_STORE, SCOPE_SHARED})


@dataclass(frozen=True)
class ApprovalResult:
    proposal: ProductDataProposal
    applied_to: str          # e.g. "product_case_mappings:06206738062"
    previous_value: Any | None
    shared_mapping_id: str | None = None      # the shared value this approval set or confirmed
    overrides_recorded: int = 0               # stores whose differing value was kept as an override
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionOutcome:
    proposal: ProductDataProposal
    applied_to: str | None   # None on rejection


class BatchRefusedError(ValueError):
    """
    Raised by decide_many() before anything is written: at least one
    proposal in the batch is not PENDING. `failures` maps proposal id
    (str) to the reason, so the caller can say exactly which rows.
    """

    def __init__(self, failures: dict[str, str]) -> None:
        self.failures = failures
        super().__init__(
            f"{len(failures)} proposal(s) cannot be decided: "
            + "; ".join(f"{k[:8]} {v}" for k, v in failures.items())
        )


def _classify(status: CaseMappingStatus | None, value: int) -> tuple[str, dict[str, Any]]:
    """
    How the operator's number relates to what the invoice offered.

    Decided by the system from the review row, never by the client: a
    request cannot label its own value "reference_derived".
    """
    if status is None:
        return SOURCE_OPERATOR_ENTERED, {}
    evidence: dict[str, Any] = {
        "invoice_description": status.description,
        "pack_size": status.pack_size,
        "suggested_units_per_case": status.suggested_units_per_case,
        "suggestion_source": status.suggestion_source,
        "suggestion_candidates": status.suggestion_candidates,
        "reference_description": status.reference_description,
        "reference_avg_cost": status.reference_avg_cost,
    }
    if (status.suggestion_source in REFERENCE_SUGGESTION_SOURCES
            and value == status.suggested_units_per_case):
        return _REFERENCE_PROPOSAL_SOURCE.get(status.suggestion_source, SOURCE_REFERENCE_DERIVED), evidence
    if status.suggestion_source == "description_ambiguous":
        # Whatever they chose, the document only narrowed it to candidates.
        return SOURCE_DOCUMENT_AMBIGUOUS, evidence
    if status.suggestion_source in ("description", "pack_size") and value == status.suggested_units_per_case:
        return SOURCE_DOCUMENT_DERIVED, evidence
    # A suggestion existed but the operator typed something else, or
    # nothing was suggested at all: it is their number.
    return SOURCE_OPERATOR_ENTERED, evidence


async def propose_case_mapping(
    session: AsyncSession,
    *,
    store_id: uuid.UUID,
    invoice: Invoice,
    item_code: str,
    units_per_case: int,
    review_status: CaseMappingStatus | None,
    proposed_by: str,
) -> ProductDataProposal:
    """Record an operator's confirmation as a PENDING proposal."""
    current = await ProductCaseMappingRepository(session).get(store_id, item_code)
    source, evidence = _classify(review_status, units_per_case)
    return await ProductDataProposalRepository(session).create(
        store_id=store_id,
        entity_type=ENTITY_CASE_MAPPING,
        entity_key=item_code,
        field=FIELD_UNITS_PER_CASE,
        proposed_value=units_per_case,
        current_value=current.units_per_case if current else None,
        source=source,
        proposed_by=proposed_by,
        invoice_id=invoice.id,
        evidence=evidence,
        reason=f"Confirmed on invoice {invoice.invoice_number} in the review UI.",
    )


async def approve(
    session: AsyncSession,
    proposal: ProductDataProposal,
    *,
    reviewed_by: str,
    note: str | None = None,
    scope: str = SCOPE_STORE,
    override_reason: str | None = None,
    keep_store_values: bool = False,
    reviewer_user_id: uuid.UUID | None = None,
    reviewer_role: str | None = None,
) -> ApprovalResult:
    """
    Promote one proposal to authoritative data.

    Freezes the proposal first, then applies it. Both changes are
    flushed in the caller's transaction; commit or roll back together.

    For a units-per-case proposal:
      * scope "store" (default): the proposal's store's mapping, as always;
        with `override_reason` it is also recorded as that store's deliberate
        exception to the shared value (a Product Master store override);
      * scope "shared": the store's mapping AND the shared value every other
        physical store resolves from (shared_decisions.set_shared_value). If
        other stores hold a different approved value the approval is refused
        unless `keep_store_values` records theirs as overrides.
    A vendor_item proposal confirms the vendor item crosswalk link.
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope must be one of {sorted(VALID_SCOPES)}; got {scope!r}.")
    if proposal.entity_type == ENTITY_VENDOR_ITEM and proposal.field == FIELD_ITEM_CODE:
        from app.services import (
            vendor_item_crosswalk,  # local: the crosswalk service imports this module
        )

        proposals = ProductDataProposalRepository(session)
        await proposals.mark_approved(proposal, reviewed_by=reviewed_by, note=note)
        link = await vendor_item_crosswalk.confirm_from_proposal(
            session, proposal, reviewer=reviewed_by, reviewer_role=reviewer_role)
        return ApprovalResult(proposal=proposal, applied_to=f"vendor_item_crosswalk:{link.id}",
                              previous_value=proposal.current_value)

    if proposal.entity_type != ENTITY_CASE_MAPPING or proposal.field != FIELD_UNITS_PER_CASE:
        raise ValueError(
            f"No approval handler for {proposal.entity_type}.{proposal.field}; "
            "the proposal was not applied."
        )
    units = int(proposal.proposed_value)
    evidence = {"proposal_id": str(proposal.id), "invoice_id": str(proposal.invoice_id or ""),
                "store_id": str(proposal.store_id), "proposal_source": proposal.source,
                "invoice_description": (proposal.evidence or {}).get("invoice_description")}

    proposals = ProductDataProposalRepository(session)
    await proposals.mark_approved(proposal, reviewed_by=reviewed_by, note=note)

    shared_id: str | None = None
    overrides = 0
    notes: list[str] = []
    if scope == SCOPE_SHARED:
        outcome = await shared_decisions.set_shared_value(
            session, item_code=proposal.entity_key, units=units, reviewer=reviewed_by,
            reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role,
            note=note or f"Approved from proposal {proposal.id} for every store.", evidence=evidence,
            origin_store_id=proposal.store_id, keep_store_values=keep_store_values,
        )
        shared_id, overrides, notes = str(outcome.mapping.id), len(outcome.overrides), list(outcome.notes)
    elif override_reason:
        await shared_decisions.set_store_override(
            session, store_id=proposal.store_id, item_code=proposal.entity_key, units=units,
            reason=override_reason, reviewer=reviewed_by, reviewer_user_id=reviewer_user_id,
            reviewer_role=reviewer_role, evidence=evidence,
        )
        notes.append("recorded as this store's override of the shared value")

    # The proposal's store is the mapping's store. A reviewer approving
    # store A's evidence writes store A's row and no other.
    mappings = ProductCaseMappingRepository(session)
    previous = await mappings.get(proposal.store_id, proposal.entity_key)
    previous_value = previous.units_per_case if previous else None
    mapping = await mappings.upsert(
        store_id=proposal.store_id,
        item_code=proposal.entity_key,
        units_per_case=units,
        description=(proposal.evidence or {}).get("invoice_description"),
        source=SOURCE_APPROVED,
    )
    mapping.approved_proposal_id = proposal.id
    await session.flush()
    return ApprovalResult(
        proposal=proposal,
        applied_to=f"product_case_mappings:{proposal.store_id}:{proposal.entity_key}",
        previous_value=previous_value, shared_mapping_id=shared_id, overrides_recorded=overrides,
        notes=tuple(notes),
    )


async def reject(
    session: AsyncSession,
    proposal: ProductDataProposal,
    *,
    reviewed_by: str,
    note: str | None = None,
) -> ProductDataProposal:
    """Freeze the proposal as rejected. Authoritative data is untouched."""
    return await ProductDataProposalRepository(session).mark_rejected(
        proposal, reviewed_by=reviewed_by, note=note
    )


async def decide_many(
    session: AsyncSession,
    proposals: list[ProductDataProposal],
    *,
    approve_them: bool,
    reviewed_by: str,
    note: str | None = None,
    scope: str = SCOPE_STORE,
    keep_store_values: bool = False,
    reviewer_user_id: uuid.UUID | None = None,
    reviewer_role: str | None = None,
) -> list[DecisionOutcome]:
    """
    Approve or reject a batch, all-or-nothing.

    Each row goes through the same approve()/reject() as a single
    decision — same reviewer, same timestamp precision, its own row and
    its own written mapping. What the batch adds is only the pre-check:
    if any row is already decided, nothing is written and the caller
    hears which ones. The caller owns the commit.
    """
    failures = {str(p.id): f"already {p.status}" for p in proposals if not p.is_pending}
    if failures:
        raise BatchRefusedError(failures)
    outcomes: list[DecisionOutcome] = []
    for p in proposals:
        if approve_them:
            result = await approve(session, p, reviewed_by=reviewed_by, note=note,
                                   scope=scope if p.entity_type == ENTITY_CASE_MAPPING else SCOPE_STORE,
                                   keep_store_values=keep_store_values,
                                   reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role)
            outcomes.append(DecisionOutcome(proposal=p, applied_to=result.applied_to))
        else:
            await reject(session, p, reviewed_by=reviewed_by, note=note)
            outcomes.append(DecisionOutcome(proposal=p, applied_to=None))
    return outcomes


async def revise(
    session: AsyncSession,
    original: ProductDataProposal,
    *,
    proposed_value: Any,
    proposed_by: str,
    note: str | None = None,
) -> ProductDataProposal:
    """
    Replace a pending proposal's value with a reviewer's correction.

    The original is never edited. A new PENDING proposal is created with
    the corrected value, source operator_entered (it is the reviewer's
    number now, whatever the original's evidence said), the original's
    evidence kept and annotated with `revised_from`, and the original is
    frozen as REJECTED with a note naming its successor. Both land in one
    flush. The new proposal is approved — or not — exactly like any other.
    """
    proposals = ProductDataProposalRepository(session)
    if not original.is_pending:
        raise ProposalImmutableError(
            f"Proposal {original.id} is {original.status} and cannot be revised. "
            "Submit a new proposal instead."
        )
    if proposed_value == original.proposed_value:
        raise ValueError("The revised value is the same as the proposed value; nothing to revise.")

    current = None
    if original.entity_type == ENTITY_CASE_MAPPING:
        mapping = await ProductCaseMappingRepository(session).get(original.store_id, original.entity_key)
        current = mapping.units_per_case if mapping else None

    evidence = dict(original.evidence or {})
    evidence["revised_from"] = str(original.id)
    evidence["revised_from_value"] = original.proposed_value
    evidence["revised_from_source"] = original.source
    if note:
        evidence["revision_note"] = note
    revised = await proposals.create(
        store_id=original.store_id,
        entity_type=original.entity_type,
        entity_key=original.entity_key,
        field=original.field,
        proposed_value=proposed_value,
        current_value=current,
        source=SOURCE_OPERATOR_ENTERED,
        proposed_by=proposed_by,
        invoice_id=original.invoice_id,
        evidence=evidence,
        reason=(
            f"Revised from proposal {original.id} "
            f"({original.proposed_value!r} -> {proposed_value!r}) in the review table."
            + (f" {note}" if note else "")
        ),
        source_file=original.source_file,
        source_sheet=original.source_sheet,
        source_row=original.source_row,
    )
    await proposals.mark_rejected(
        original, reviewed_by=proposed_by,
        note=f"Superseded by revised proposal {revised.id}: "
             f"{original.proposed_value!r} -> {proposed_value!r}." + (f" {note}" if note else ""),
    )
    return revised


async def pending_by_item_code(
    session: AsyncSession, store_id: uuid.UUID, item_codes: list[str]
) -> dict[str, ProductDataProposal]:
    return await ProductDataProposalRepository(session).pending_for_keys(
        store_id, ENTITY_CASE_MAPPING, FIELD_UNITS_PER_CASE, item_codes
    )


@dataclass(frozen=True)
class DifferenceResolution:
    item_code: str
    units: int
    shared_mapping_id: str
    adopted_store_ids: tuple[uuid.UUID, ...]
    kept_store_ids: tuple[uuid.UUID, ...]


async def resolve_difference(
    session: AsyncSession,
    *,
    item_code: str,
    units: int,
    keep_store_ids: set[uuid.UUID],
    note: str,
    reviewed_by: str,
    reviewer_user_id: uuid.UUID | None = None,
    reviewer_role: str | None = None,
) -> DifferenceResolution:
    """
    Settle a cross-store difference (shared_decisions.cross_store_differences):
    `units` becomes the shared value; each store listed in `keep_store_ids`
    keeps its own value as a recorded override; every other differing store
    adopts the shared value through an approved proposal (source
    conflict_resolution), so its own mapping's history says who changed it
    and why. A store whose differing value is an approved Product Master
    mapping is refused — reopen that decision first. All or nothing.
    """
    note = (note or "").strip()
    if not note:
        raise shared_decisions.SharedDecisionRefusedError(
            message="Say why this value is right; the note is kept with every record this writes.",
            detail={"reason": "note_required"})
    state = (await shared_decisions.item_states(session, [item_code]))[item_code]
    differing = state.differing(units)
    adopt = [v for v in differing if v.store_id not in keep_store_ids]
    blocked = [v for v in adopt if v.source != "store mapping"]
    if blocked:
        raise shared_decisions.SharedDecisionRefusedError(
            message=("These stores hold an approved Product Master value, not a store mapping: "
                     f"{', '.join(v.store_label for v in blocked)}. Reopen those in Product Master Approvals, "
                     "or keep them as overrides."),
            detail={"reason": "store_product_master_value", "stores": [str(v.store_id) for v in blocked]})
    repository = ProductDataProposalRepository(session)
    for v in adopt:
        proposal = await repository.create(
            store_id=v.store_id, entity_type=ENTITY_CASE_MAPPING, entity_key=item_code, field=FIELD_UNITS_PER_CASE,
            proposed_value=units, current_value=v.units, source=SOURCE_CONFLICT_RESOLUTION, proposed_by=reviewed_by,
            evidence={"resolution": "adopt_shared_value", "previous_units": v.units, "note": note},
            reason=f"Cross-store difference settled at {units}: {note}",
        )
        await approve(session, proposal, reviewed_by=reviewed_by, note=note)
    outcome = await shared_decisions.set_shared_value(
        session, item_code=item_code, units=units, reviewer=reviewed_by, reviewer_user_id=reviewer_user_id,
        reviewer_role=reviewer_role, note=note, keep_store_values=True,
        evidence={"decision": "cross_store_resolution", "adopted": [str(v.store_id) for v in adopt]},
    )
    return DifferenceResolution(
        item_code=item_code, units=units, shared_mapping_id=str(outcome.mapping.id),
        adopted_store_ids=tuple(v.store_id for v in adopt),
        kept_store_ids=tuple(o.store_id for o in outcome.overrides),
    )
