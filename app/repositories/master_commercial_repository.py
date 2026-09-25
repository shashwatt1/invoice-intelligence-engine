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
)
from app.models.store import Store


def _review_status_clause(status: str):
    """
    Translate the derived review status into a predicate.

    Kept beside the derivation in the service (review_status()) — the two
    must agree, so the mapping is written once here rather than duplicated
    at each call site.
    """
    from app.models.product_master import STATE_APPROVED, STATE_REJECTED, STATE_REVIEW_REQUIRED

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

    async def get(self, mapping_id: uuid.UUID):
        statement = self._base().where(MasterCommercialMapping.id == mapping_id)
        return (await self._session.execute(statement)).first()

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
