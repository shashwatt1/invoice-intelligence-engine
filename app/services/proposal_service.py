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

Nothing here has a path that writes product_case_mappings without an
APPROVED proposal id in hand. That is the point of the module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.product_case_mapping import SOURCE_APPROVED
from app.models.product_data_proposal import (
    ENTITY_CASE_MAPPING,
    FIELD_UNITS_PER_CASE,
    SOURCE_BEER_INVENTORY_EXPLICIT,
    SOURCE_BEER_INVENTORY_PACKAGE,
    SOURCE_DOCUMENT_AMBIGUOUS,
    SOURCE_DOCUMENT_DERIVED,
    SOURCE_OPERATOR_ENTERED,
    SOURCE_REFERENCE_DERIVED,
    ProductDataProposal,
)
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import (
    ProductDataProposalRepository,
)
from app.services.case_mapping_service import (
    REFERENCE_SUGGESTION_SOURCES,
    SUGGESTION_FROM_REFERENCE_EXPLICIT,
    SUGGESTION_FROM_REFERENCE_PACKAGE,
    CaseMappingStatus,
)

# suggestion source -> proposal source
_REFERENCE_PROPOSAL_SOURCE = {
    SUGGESTION_FROM_REFERENCE_EXPLICIT: SOURCE_BEER_INVENTORY_EXPLICIT,
    SUGGESTION_FROM_REFERENCE_PACKAGE: SOURCE_BEER_INVENTORY_PACKAGE,
}


@dataclass(frozen=True)
class ApprovalResult:
    proposal: ProductDataProposal
    applied_to: str          # e.g. "product_case_mappings:06206738062"
    previous_value: Any | None


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
    store_number: str,
    invoice: Invoice,
    item_code: str,
    units_per_case: int,
    review_status: CaseMappingStatus | None,
    proposed_by: str,
) -> ProductDataProposal:
    """Record an operator's confirmation as a PENDING proposal."""
    current = await ProductCaseMappingRepository(session).get(store_number, item_code)
    source, evidence = _classify(review_status, units_per_case)
    return await ProductDataProposalRepository(session).create(
        store_number=store_number,
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
) -> ApprovalResult:
    """
    Promote one proposal to authoritative data.

    Freezes the proposal first, then applies it. Both changes are
    flushed in the caller's transaction; commit or roll back together.
    """
    proposals = ProductDataProposalRepository(session)
    await proposals.mark_approved(proposal, reviewed_by=reviewed_by, note=note)

    if proposal.entity_type == ENTITY_CASE_MAPPING and proposal.field == FIELD_UNITS_PER_CASE:
        # The proposal's store is the mapping's store. A reviewer approving
        # store A's evidence writes store A's row and no other.
        mappings = ProductCaseMappingRepository(session)
        previous = await mappings.get(proposal.store_number, proposal.entity_key)
        previous_value = previous.units_per_case if previous else None
        mapping = await mappings.upsert(
            store_number=proposal.store_number,
            item_code=proposal.entity_key,
            units_per_case=int(proposal.proposed_value),
            description=(proposal.evidence or {}).get("invoice_description"),
            source=SOURCE_APPROVED,
        )
        mapping.approved_proposal_id = proposal.id
        await session.flush()
        return ApprovalResult(
            proposal=proposal,
            applied_to=f"product_case_mappings:{proposal.store_number}:{proposal.entity_key}",
            previous_value=previous_value,
        )

    raise ValueError(
        f"No approval handler for {proposal.entity_type}.{proposal.field}; "
        "the proposal was not applied."
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


async def pending_by_item_code(
    session: AsyncSession, store_number: str, item_codes: list[str]
) -> dict[str, ProductDataProposal]:
    return await ProductDataProposalRepository(session).pending_for_keys(
        store_number, ENTITY_CASE_MAPPING, FIELD_UNITS_PER_CASE, item_codes
    )
