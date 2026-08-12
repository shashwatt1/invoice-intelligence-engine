"""
Product Case Mapping Repository — app/repositories/product_case_mapping_repository.py

Data access for the UPC → units-per-case mapping.

Per the package convention (app/repositories/__init__.py): repositories
accept an AsyncSession and flush, but never commit — transaction
boundaries belong to the service/API layer.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_case_mapping import (
    MAX_UNITS_PER_CASE,
    MIN_UNITS_PER_CASE,
    SOURCE_MANUAL,
    VALID_SOURCES,
    ProductCaseMapping,
)


class ProductCaseMappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, item_code: str) -> ProductCaseMapping | None:
        result = await self._session.execute(
            select(ProductCaseMapping).where(ProductCaseMapping.item_code == item_code)
        )
        return result.scalar_one_or_none()

    async def units_by_item_code(self, item_codes: Iterable[str]) -> dict[str, int]:
        """
        Confirmed units-per-case for the given codes, as a plain dict.

        One query for the whole invoice rather than one per line, and the
        shape the formatter consumes — it stays free of ORM objects and
        of the session.
        """
        codes = [code for code in dict.fromkeys(item_codes) if code]
        if not codes:
            return {}
        result = await self._session.execute(
            select(ProductCaseMapping.item_code, ProductCaseMapping.units_per_case).where(
                ProductCaseMapping.item_code.in_(codes)
            )
        )
        return {row.item_code: row.units_per_case for row in result}

    async def upsert(
        self,
        *,
        item_code: str,
        units_per_case: int,
        description: str | None = None,
        source: str = SOURCE_MANUAL,
    ) -> ProductCaseMapping:
        """
        Create or update the mapping for one product.

        Upserts rather than inserting so re-confirming a product corrects
        the existing row instead of colliding with the unique constraint
        on item_code. Validates here — the database enforces uniqueness
        but not the value range, and a bad pack size silently corrupts
        Case Retail inside PDI.

        Raises:
            ValueError: blank item code, out-of-range units, unknown source.
        """
        code = (item_code or "").strip()
        if not code:
            raise ValueError("item_code is required.")
        if not isinstance(units_per_case, int) or isinstance(units_per_case, bool):
            raise ValueError("units_per_case must be an integer.")
        if not MIN_UNITS_PER_CASE <= units_per_case <= MAX_UNITS_PER_CASE:
            raise ValueError(
                f"units_per_case must be between {MIN_UNITS_PER_CASE} and "
                f"{MAX_UNITS_PER_CASE}; got {units_per_case}."
            )
        if source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}; got {source!r}.")

        existing = await self.get(code)
        if existing is not None:
            existing.units_per_case = units_per_case
            existing.source = source
            if description:
                existing.description = description
            await self._session.flush()
            return existing

        mapping = ProductCaseMapping(
            item_code=code,
            units_per_case=units_per_case,
            description=description,
            source=source,
        )
        self._session.add(mapping)
        await self._session.flush()
        return mapping
