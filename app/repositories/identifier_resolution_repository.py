"""
Identifier Resolution Repository — app/repositories/identifier_resolution_repository.py

Append-only data access for invoice_identifier_observations and
invoice_line_resolutions. Per the package convention it accepts an
AsyncSession and flushes, never commits.

There is deliberately no update or delete method. The ORM listeners in
app/models/identifier_resolution.py and the migration's triggers refuse
both anyway; this class simply offers no way to ask.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identifier_resolution import InvoiceIdentifierObservation, InvoiceLineResolution


class IdentifierResolutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_observations(self, rows: Sequence[dict[str, Any]]) -> list[InvoiceIdentifierObservation]:
        records = [InvoiceIdentifierObservation(**row) for row in rows]
        self._session.add_all(records)
        await self._session.flush()
        return records

    async def append_resolutions(self, rows: Sequence[dict[str, Any]]) -> list[InvoiceLineResolution]:
        records = [InvoiceLineResolution(**row) for row in rows]
        self._session.add_all(records)
        await self._session.flush()
        return records

    async def history_for_item(self, invoice_item_id: uuid.UUID) -> list[InvoiceLineResolution]:
        result = await self._session.execute(
            select(InvoiceLineResolution)
            .where(InvoiceLineResolution.invoice_item_id == invoice_item_id)
            .order_by(InvoiceLineResolution.created_at, InvoiceLineResolution.id)
        )
        return list(result.scalars())

    async def latest_for_invoice(self, invoice_id: uuid.UUID) -> dict[uuid.UUID, InvoiceLineResolution]:
        """The current resolution of each line: its most recent row."""
        result = await self._session.execute(
            select(InvoiceLineResolution)
            .where(InvoiceLineResolution.invoice_id == invoice_id,
                   InvoiceLineResolution.invoice_item_id.is_not(None))
            .order_by(InvoiceLineResolution.created_at, InvoiceLineResolution.id)
        )
        latest: dict[uuid.UUID, InvoiceLineResolution] = {}
        for row in result.scalars():
            assert row.invoice_item_id is not None
            latest[row.invoice_item_id] = row   # later rows win
        return latest

    async def observations_for_invoice(self, invoice_id: uuid.UUID) -> list[InvoiceIdentifierObservation]:
        result = await self._session.execute(
            select(InvoiceIdentifierObservation)
            .where(InvoiceIdentifierObservation.invoice_id == invoice_id)
            .order_by(InvoiceIdentifierObservation.created_at, InvoiceIdentifierObservation.id)
        )
        return list(result.scalars())

    async def reviewed_gtins_for_supplier(self, template_id: str, supplier_item_id: str) -> list[str]:
        """
        R8 history source: GTINs a person confirmed (REVIEW_CONFIRMED) on lines
        whose resolution carried this supplier item id under this template.
        """
        result = await self._session.execute(
            select(InvoiceLineResolution.chosen_gtin, InvoiceLineResolution.resolution)
            .where(InvoiceLineResolution.status == "REVIEW_CONFIRMED",
                   InvoiceLineResolution.template_id == template_id)
        )
        gtins = set()
        for gtin, resolution in result.all():
            values = {c.get("value") for c in (resolution or {}).get("candidates", [])
                      if c.get("kind") == "SUPPLIER_ITEM_ID"}
            if gtin and supplier_item_id in values:
                gtins.add(gtin)
        return sorted(gtins)
