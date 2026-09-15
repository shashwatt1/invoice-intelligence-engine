"""
Store repository — app/repositories/store_repository.py

Resolution and bookkeeping for the store master. Flushes, never commits.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.product_case_mapping import ProductCaseMapping
from app.models.product_data_proposal import ProductDataProposal
from app.models.product_reference import ProductIdentifier, ProductIdentity, ProductPricing
from app.models.store import (
    IDENTITY_UNRESOLVED,
    SOURCE_ITEM_SALES,
    TYPE_STORE_CODE,
    Store,
    StoreIdentifier,
)
from app.models.store_product_reference import StoreProductReference


class StoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, store_id: uuid.UUID) -> Store | None:
        return (await self._session.execute(
            select(Store).where(Store.id == store_id))).scalar_one_or_none()

    async def list(self) -> list[Store]:
        return list((await self._session.execute(
            select(Store).order_by(Store.display_name.nulls_last(), Store.created_at))).scalars())

    async def by_identifier(
        self, source_system: str, identifier_type: str, value: str
    ) -> Store | None:
        """The one store a source system's identifier names, or None."""
        return (await self._session.execute(
            select(Store).join(StoreIdentifier, StoreIdentifier.store_id == Store.id).where(
                StoreIdentifier.source_system == source_system,
                StoreIdentifier.identifier_type == identifier_type,
                StoreIdentifier.identifier_value == value,
            )
        )).scalar_one_or_none()

    async def by_item_sales_code(self, code: str) -> Store | None:
        return await self.by_identifier(SOURCE_ITEM_SALES, TYPE_STORE_CODE, code)

    async def resolve(self, reference: str) -> Store | None:
        """
        A store from what an operator typed: a Store id, or an Item Sales
        store code. Anything else is None — nothing is guessed.
        """
        text = (reference or "").strip()
        if not text:
            return None
        try:
            return await self.get(uuid.UUID(text))
        except ValueError:
            return await self.by_item_sales_code(text)

    async def create(
        self,
        *,
        display_name: str | None = None,
        customer_name: str | None = None,
        address_line_1: str | None = None,
        address_line_2: str | None = None,
        city: str | None = None,
        state: str | None = None,
        postal_code: str | None = None,
        identity_status: str = IDENTITY_UNRESOLVED,
        notes: str | None = None,
    ) -> Store:
        store = Store(
            display_name=display_name, customer_name=customer_name,
            address_line_1=address_line_1, address_line_2=address_line_2,
            city=city, state=state, postal_code=postal_code,
            identity_status=identity_status, notes=notes,
        )
        self._session.add(store)
        await self._session.flush()
        return store

    async def add_identifier(
        self, store: Store, source_system: str, identifier_type: str, value: str,
        evidence: dict[str, Any] | None = None,
    ) -> StoreIdentifier:
        """
        Attach an identifier. Refuses if that identifier already names a
        different store in the same source system — one code, one store.
        """
        existing = await self.by_identifier(source_system, identifier_type, value)
        if existing is not None and existing.id != store.id:
            raise ValueError(
                f"{source_system}:{identifier_type}={value!r} already identifies store "
                f"{existing.label!r}; it cannot also identify {store.label!r}."
            )
        if existing is not None:
            return next(i for i in store.identifiers if i.identifier_value == value
                        and i.source_system == source_system and i.identifier_type == identifier_type)
        ident = StoreIdentifier(store=store, source_system=source_system,
                                identifier_type=identifier_type, identifier_value=value,
                                evidence=evidence)
        self._session.add(ident)
        await self._session.flush()
        await self._session.refresh(store, ["identifiers"])
        return ident

    async def all_identifiers(self) -> list[StoreIdentifier]:
        return list((await self._session.execute(select(StoreIdentifier))).scalars())

    async def counts(self) -> dict[uuid.UUID, dict[str, int]]:
        """Per-store row counts across the operational tables."""
        out: dict[uuid.UUID, dict[str, int]] = {}

        async def tally(key, column, extra=None):
            query = select(column, func.count()).group_by(column)
            if extra is not None:
                query = query.where(extra)
            for store_id, n in (await self._session.execute(query)).all():
                out.setdefault(store_id, {})[key] = n

        await tally("invoices", Invoice.store_id)
        await tally("pricing_rows", ProductPricing.store_id)
        await tally("identities", ProductIdentity.store_id)
        await tally("identifiers", ProductIdentifier.store_id)
        await tally("catalogue_rows", StoreProductReference.store_id)
        await tally("case_mappings", ProductCaseMapping.store_id)
        await tally("pending_proposals", ProductDataProposal.store_id,
                    ProductDataProposal.status == "PENDING")
        return out

    async def has_operational_data(self, store_id: uuid.UUID) -> bool:
        counts = (await self.counts()).get(store_id, {})
        return any(n for k, n in counts.items() if k != "pending_proposals") or bool(counts.get("pending_proposals"))

    async def labels(self, store_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Store]:
        ids = list({s for s in store_ids if s})
        if not ids:
            return {}
        rows = (await self._session.execute(select(Store).where(Store.id.in_(ids)))).scalars()
        return {s.id: s for s in rows}
