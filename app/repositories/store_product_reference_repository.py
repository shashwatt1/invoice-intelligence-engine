"""
Store Product Reference Repository — reads and upserts the per-store
product catalogue imported from the store's own sales export.

Follows the same contract as every other repository here: flushes so
identifiers are available, never commits — the transaction boundary
belongs to the service or API layer that owns the request.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store_product_reference import StoreProductReference


class StoreProductReferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, store_id: uuid.UUID, item_code: str) -> StoreProductReference | None:
        result = await self._session.execute(
            select(StoreProductReference).where(
                StoreProductReference.store_id == store_id,
                StoreProductReference.item_code == item_code,
            )
        )
        return result.scalar_one_or_none()

    async def by_item_codes(
        self, store_id: uuid.UUID, item_codes: Sequence[str]
    ) -> dict[str, StoreProductReference]:
        """
        Reference rows for the given normalized codes, keyed by code.

        Exact lookup only. Description similarity is deliberately not
        offered: in this store's own export 666 descriptions map to more
        than one scan code, so any automatic name match would assign one
        product's cost to another.
        """
        if not item_codes:
            return {}
        result = await self._session.execute(
            select(StoreProductReference).where(
                StoreProductReference.store_id == store_id,
                StoreProductReference.item_code.in_(list({c for c in item_codes if c})),
            )
        )
        return {row.item_code: row for row in result.scalars()}

    async def upsert_many(self, rows: Iterable[dict]) -> tuple[int, int]:
        """
        Insert or update reference rows, keyed by (store_id, item_code).

        Returns (inserted, updated). Idempotent by construction: importing
        the same export twice updates in place and leaves the row count
        unchanged, so a re-import after a catalogue refresh is safe.
        """
        rows = list(rows)
        if not rows:
            return 0, 0

        store_ids = {r["store_id"] for r in rows}
        codes = {r["item_code"] for r in rows}
        existing_result = await self._session.execute(
            select(StoreProductReference).where(
                StoreProductReference.store_id.in_(store_ids),
                StoreProductReference.item_code.in_(codes),
            )
        )
        existing = {
            (row.store_id, row.item_code): row for row in existing_result.scalars()
        }

        inserted = updated = 0
        for row in rows:
            key = (row["store_id"], row["item_code"])
            current = existing.get(key)
            if current is None:
                current = StoreProductReference(**row)
                self._session.add(current)
                existing[key] = current
                inserted += 1
                continue
            for field, value in row.items():
                setattr(current, field, value)
            updated += 1

        await self._session.flush()
        return inserted, updated

    async def stats(self, store_id: uuid.UUID) -> dict[str, int]:
        rows = (
            await self._session.execute(
                select(StoreProductReference).where(
                    StoreProductReference.store_id == store_id
                )
            )
        ).scalars().all()
        costed = sum(1 for r in rows if r.avg_cost is not None)
        return {
            "total": len(rows),
            "costed": costed,
            "uncosted": len(rows) - costed,
            "distinct_item_codes": len({r.item_code for r in rows}),
        }
