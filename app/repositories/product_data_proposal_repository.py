"""
Product Data Proposal Repository.

Persistence for the approval gate. The one rule that matters lives here:
a proposal that has been reviewed is immutable. Every method that writes
checks the status first and raises rather than silently changing history.

Flushes, never commits — the transaction belongs to the caller, which
matters for approval: the proposal's status change and the authoritative
write it causes must land together or not at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_data_proposal import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    VALID_SOURCES,
    ProductDataProposal,
)


class ProposalImmutableError(ValueError):
    """Raised on any attempt to change a proposal after review."""


class ProductDataProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, proposal_id: uuid.UUID) -> ProductDataProposal | None:
        return await self._session.get(ProductDataProposal, proposal_id)

    async def list(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        entity_type: str | None = None,
        entity_key: str | None = None,
        invoice_id: uuid.UUID | None = None,
        store_number: str | None = None,
    ) -> list[ProductDataProposal]:
        query = select(ProductDataProposal)
        if status:
            query = query.where(ProductDataProposal.status == status)
        if source:
            query = query.where(ProductDataProposal.source == source)
        if entity_type:
            query = query.where(ProductDataProposal.entity_type == entity_type)
        if entity_key:
            query = query.where(ProductDataProposal.entity_key == entity_key)
        if invoice_id:
            query = query.where(ProductDataProposal.invoice_id == invoice_id)
        if store_number:
            query = query.where(ProductDataProposal.store_number == store_number)
        result = await self._session.execute(
            query.order_by(ProductDataProposal.created_at, ProductDataProposal.id)
        )
        return list(result.scalars())

    async def pending_for_keys(
        self, store_number: str, entity_type: str, field: str, keys: Sequence[str]
    ) -> dict[str, ProductDataProposal]:
        """Latest PENDING proposal per entity key, for the review UI."""
        if not keys:
            return {}
        result = await self._session.execute(
            select(ProductDataProposal)
            .where(
                ProductDataProposal.store_number == store_number,
                ProductDataProposal.entity_type == entity_type,
                ProductDataProposal.field == field,
                ProductDataProposal.status == STATUS_PENDING,
                ProductDataProposal.entity_key.in_(list(keys)),
            )
            .order_by(ProductDataProposal.created_at)
        )
        latest: dict[str, ProductDataProposal] = {}
        for row in result.scalars():
            latest[row.entity_key] = row   # later rows win: newest pending per key
        return latest

    async def create(
        self,
        *,
        store_number: str,
        entity_type: str,
        entity_key: str,
        field: str,
        proposed_value: Any,
        current_value: Any | None,
        source: str,
        proposed_by: str,
        invoice_id: uuid.UUID | None = None,
        evidence: dict[str, Any] | None = None,
        reason: str | None = None,
        source_file: str | None = None,
        source_sheet: str | None = None,
        source_row: int | None = None,
    ) -> ProductDataProposal:
        """
        Record a new PENDING proposal.

        An identical PENDING proposal (same key, field and value) is
        returned rather than duplicated — re-clicking Confirm must not
        pile up rows for a reviewer. A *different* pending value for the
        same key is a genuinely new proposal and is kept alongside; the
        reviewer sees both and decides.
        """
        if source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}; got {source!r}.")

        existing = await self._session.execute(
            select(ProductDataProposal).where(
                ProductDataProposal.store_number == store_number,
                ProductDataProposal.entity_type == entity_type,
                ProductDataProposal.entity_key == entity_key,
                ProductDataProposal.field == field,
                ProductDataProposal.status == STATUS_PENDING,
            )
        )
        for row in existing.scalars():
            if row.proposed_value == proposed_value:
                return row

        proposal = ProductDataProposal(
            store_number=store_number,
            entity_type=entity_type,
            entity_key=entity_key,
            field=field,
            proposed_value=proposed_value,
            current_value=current_value,
            source=source,
            source_file=source_file,
            source_sheet=source_sheet,
            source_row=source_row,
            invoice_id=invoice_id,
            evidence=evidence,
            reason=reason,
            proposed_by=proposed_by,
            status=STATUS_PENDING,
        )
        self._session.add(proposal)
        await self._session.flush()
        return proposal

    def _require_pending(self, proposal: ProductDataProposal) -> None:
        if proposal.status != STATUS_PENDING:
            raise ProposalImmutableError(
                f"Proposal {proposal.id} is {proposal.status} and cannot be changed. "
                "Submit a new proposal instead."
            )

    async def mark_approved(
        self, proposal: ProductDataProposal, *, reviewed_by: str, note: str | None = None
    ) -> ProductDataProposal:
        """Freeze the proposal as APPROVED. The caller applies the value."""
        self._require_pending(proposal)
        proposal.status = STATUS_APPROVED
        proposal.reviewed_by = reviewed_by
        proposal.reviewed_at = datetime.now(UTC)
        proposal.review_note = note
        await self._session.flush()
        return proposal

    async def mark_rejected(
        self, proposal: ProductDataProposal, *, reviewed_by: str, note: str | None = None
    ) -> ProductDataProposal:
        """Freeze the proposal as REJECTED. Authoritative data is untouched."""
        self._require_pending(proposal)
        proposal.status = STATUS_REJECTED
        proposal.reviewed_by = reviewed_by
        proposal.reviewed_at = datetime.now(UTC)
        proposal.review_note = note
        await self._session.flush()
        return proposal
