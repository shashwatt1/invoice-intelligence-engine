"""
Master Commercial Repository — app/repositories/master_commercial_repository.py

Data access for the Product Master commercial review queue.

Per the package convention: this accepts an AsyncSession and flushes, but
never commits — the transaction boundary belongs to the service layer.

Read-only with respect to everything outside the Product Master: the
legacy `product_case_mappings` rows this joins for context are selected,
never written.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_master import (
    COMMERCIAL_CONFLICT,
    MasterCommercialMapping,
    MasterCommercialReview,
    MasterProduct,
    MasterProductDescription,
)
from app.models.store import Store


def _review_status_clause(status: str):
    """
    Translate the derived review status into a predicate.

    Kept beside the derivation in the service (review_status()) — the two
    must agree, so the mapping is written once here rather than duplicated
    at each call site.
    """
    from app.models.product_master import (
        STATE_APPROVED,
        STATE_PENDING,
        STATE_REJECTED,
        STATE_REVIEW_REQUIRED,
    )

    open_row = MasterCommercialMapping.approval_state == STATE_REVIEW_REQUIRED
    if status == "APPROVED":
        return MasterCommercialMapping.approval_state == STATE_APPROVED
    if status == "REJECTED":
        return MasterCommercialMapping.approval_state == STATE_REJECTED
    if status == "CONFLICT":
        return open_row & (MasterCommercialMapping.commercial_unit_basis == COMMERCIAL_CONFLICT)
    if status == "NO_MULTIPLIER":
        return (open_row
                & (MasterCommercialMapping.commercial_unit_basis != COMMERCIAL_CONFLICT)
                & MasterCommercialMapping.units_accounted_for.is_(None))
    if status == "UNDECIDED":
        # Everything still awaiting a MANAGER/ADMIN decision: the approval queue.
        return MasterCommercialMapping.approval_state.in_((STATE_REVIEW_REQUIRED, STATE_PENDING))
    if status == "READY_FOR_REVIEW":
        return (open_row
                & (MasterCommercialMapping.commercial_unit_basis != COMMERCIAL_CONFLICT)
                & MasterCommercialMapping.units_accounted_for.isnot(None))
    # An unknown status must not silently widen the queue.
    return MasterCommercialMapping.id.is_(None)


class MasterCommercialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _base(self) -> Select:
        return (
            select(MasterCommercialMapping, MasterProduct, Store)
            .join(MasterProduct, MasterProduct.id == MasterCommercialMapping.product_id)
            .join(Store, Store.id == MasterCommercialMapping.store_id)
        )

    def _filtered(
        self,
        statement: Select,
        *,
        approval_state: str | None = None,
        review_status: str | None = None,
        commercial_unit_basis: str | None = None,
        conflicts_only: bool = False,
        store_id: uuid.UUID | None = None,
        cost_basis: str | None = None,
        search: str | None = None,
    ) -> Select:
        if approval_state:
            statement = statement.where(
                MasterCommercialMapping.approval_state == approval_state
            )
        if review_status:
            statement = statement.where(_review_status_clause(review_status))
        if commercial_unit_basis:
            statement = statement.where(
                MasterCommercialMapping.commercial_unit_basis == commercial_unit_basis
            )
        if conflicts_only:
            statement = statement.where(
                MasterCommercialMapping.commercial_unit_basis == COMMERCIAL_CONFLICT
            )
        if store_id:
            statement = statement.where(MasterCommercialMapping.store_id == store_id)
        if cost_basis:
            statement = statement.where(MasterCommercialMapping.cost_basis == cost_basis)
        if search:
            term = f"%{search.strip()}%"
            statement = statement.where(or_(
                MasterProduct.canonical_upc.ilike(term),
                MasterCommercialMapping.pdi_item_code.ilike(term),
                MasterProduct.canonical_key.ilike(term),
                # Any description on record, canonical or source — so a
                # reviewer can find a product by the name they know it by.
                select(MasterProductDescription.id).where(
                    MasterProductDescription.product_id == MasterProduct.id,
                    MasterProductDescription.description.ilike(term),
                ).exists(),
            ))
        return statement

    async def list_candidates(self, *, page: int, page_size: int, **filters) -> Sequence:
        statement = self._filtered(self._base(), **filters).order_by(
            # Conflicts first — they are the rows that hold no answer yet.
            (MasterCommercialMapping.commercial_unit_basis != COMMERCIAL_CONFLICT),
            MasterProduct.canonical_upc,
        ).limit(page_size).offset((page - 1) * page_size)
        return (await self._session.execute(statement)).all()

    async def count_candidates(self, **filters) -> int:
        inner = self._filtered(
            select(MasterCommercialMapping.id)
            .join(MasterProduct, MasterProduct.id == MasterCommercialMapping.product_id)
            .join(Store, Store.id == MasterCommercialMapping.store_id),
            **filters,
        ).subquery()
        return int(
            (await self._session.execute(select(func.count()).select_from(inner))).scalar() or 0
        )

    async def get(self, mapping_id: uuid.UUID, *, for_update: bool = False):
        """
        The candidate with its product and store. `for_update` locks the mapping
        row for the rest of the transaction, so two decisions on one candidate
        are serialized and the second sees the first (see review_version).
        """
        statement = self._base().where(MasterCommercialMapping.id == mapping_id)
        if for_update:
            statement = statement.with_for_update(of=MasterCommercialMapping)
        return (await self._session.execute(statement)).first()

    async def review_version(self, mapping_id: uuid.UUID) -> int:
        """
        How many review events the candidate has. The history is append-only,
        so this only grows: every proposal, decision and reopening changes it,
        which makes it the version a reviewer's decision is checked against.
        """
        return int((await self._session.execute(
            select(func.count()).select_from(MasterCommercialReview)
            .where(MasterCommercialReview.mapping_id == mapping_id)
        )).scalar() or 0)

    async def review_facts(self, mapping_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, tuple[int, str | None]]:
        """Per candidate: (review_version, latest decision) — two grouped queries for a whole page."""
        ids = list(mapping_ids)
        if not ids:
            return {}
        counts: dict[uuid.UUID, int] = dict((await self._session.execute(
            select(MasterCommercialReview.mapping_id, func.count())
            .where(MasterCommercialReview.mapping_id.in_(ids))
            .group_by(MasterCommercialReview.mapping_id)
        )).tuples().all())
        ranked = (
            select(
                MasterCommercialReview.mapping_id,
                MasterCommercialReview.decision,
                func.row_number().over(
                    partition_by=MasterCommercialReview.mapping_id,
                    order_by=(MasterCommercialReview.created_at.desc(), MasterCommercialReview.id.desc()),
                ).label("rank"),
            )
            .where(MasterCommercialReview.mapping_id.in_(ids))
            .subquery()
        )
        latest: dict[uuid.UUID, str] = dict((await self._session.execute(
            select(ranked.c.mapping_id, ranked.c.decision).where(ranked.c.rank == 1)
        )).tuples().all())
        return {i: (int(counts.get(i, 0)), latest.get(i)) for i in ids}

    async def summary(self) -> dict[str, int]:
        rows = (await self._session.execute(
            select(
                MasterCommercialMapping.approval_state,
                MasterCommercialMapping.commercial_unit_basis,
                func.count(),
            ).group_by(
                MasterCommercialMapping.approval_state,
                MasterCommercialMapping.commercial_unit_basis,
            )
        )).all()
        totals: dict[str, int] = {}
        for state, basis, count in rows:
            totals[state] = totals.get(state, 0) + count
            totals[f"basis:{basis}"] = totals.get(f"basis:{basis}", 0) + count
            if basis == COMMERCIAL_CONFLICT:
                totals["conflicts"] = totals.get("conflicts", 0) + count
        return totals

    async def legacy_mappings_for(self, item_codes: list[str]) -> dict[str, list[ProductCaseMapping]]:
        """
        The legacy rows that currently drive EDI, for context only.

        Selected and never modified — this phase does not write to
        product_case_mappings.
        """
        if not item_codes:
            return {}
        rows = (await self._session.execute(
            select(ProductCaseMapping).where(ProductCaseMapping.item_code.in_(item_codes))
        )).scalars().all()
        grouped: dict[str, list[ProductCaseMapping]] = {}
        for row in rows:
            grouped.setdefault(row.item_code, []).append(row)
        return grouped

    async def descriptions_for(
        self, product_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[MasterProductDescription]]:
        """Every description on record for these products. Read only."""
        if not product_ids:
            return {}
        rows = (await self._session.execute(
            select(MasterProductDescription)
            .where(MasterProductDescription.product_id.in_(product_ids))
        )).scalars().all()
        grouped: dict[uuid.UUID, list[MasterProductDescription]] = {}
        for row in rows:
            grouped.setdefault(row.product_id, []).append(row)
        return grouped

    async def history_for(self, mapping_id: uuid.UUID) -> Sequence[MasterCommercialReview]:
        return (await self._session.execute(
            select(MasterCommercialReview)
            .where(MasterCommercialReview.mapping_id == mapping_id)
            .order_by(MasterCommercialReview.created_at.desc())
        )).scalars().all()

    async def add_review(self, review: MasterCommercialReview) -> MasterCommercialReview:
        self._session.add(review)
        await self._session.flush()
        return review
