"""
Product reference repository — identity, identifiers, pricing.

Upserts keyed by provenance (source file/sheet/row) so a re-import of
the same workbook updates in place. Flushes, never commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_reference import ProductIdentifier, ProductIdentity, ProductPricing
from app.services.beer_inventory_import import ReferenceRecord


class ProductReferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def pricing_for(
        self, store_number: str, item_codes: Sequence[str], *, include_conflicted: bool = False
    ) -> dict[str, list[ProductPricing]]:
        """Every pricing row per product. Conflicted rows excluded unless asked for."""
        if not item_codes:
            return {}
        query = select(ProductPricing).where(
            ProductPricing.store_number == store_number,
            ProductPricing.item_code.in_(list({c for c in item_codes if c})),
        )
        if not include_conflicted:
            query = query.where(ProductPricing.is_conflicted.is_(False))
        result = await self._session.execute(query.order_by(ProductPricing.source_sheet,
                                                            ProductPricing.source_row))
        out: dict[str, list[ProductPricing]] = {}
        for row in result.scalars():
            out.setdefault(row.item_code, []).append(row)
        return out

    async def identity_for(
        self, store_number: str, item_codes: Sequence[str]
    ) -> dict[str, ProductIdentity]:
        if not item_codes:
            return {}
        result = await self._session.execute(
            select(ProductIdentity).where(
                ProductIdentity.store_number == store_number,
                ProductIdentity.item_code.in_(list({c for c in item_codes if c})),
            )
        )
        return {row.item_code: row for row in result.scalars()}

    async def import_records(
        self,
        records: list[ReferenceRecord],
        *,
        store_number: str,
        source_file: str,
        imported_at: datetime,
    ) -> dict[str, int]:
        """
        Persist parsed records. Returns counts.

        Pricing rows are keyed by (file, sheet, row) — one per source row,
        including conflicted ones, which are stored flagged so the record
        of the disagreement survives. Identity is merged: the first source
        to supply a field wins and is recorded in `provenance`; later
        sources do not overwrite. Identifiers are unique per
        (product, kind, value, distributor).
        """
        counts = {"pricing_inserted": 0, "pricing_updated": 0, "identity_created": 0,
                  "identity_enriched": 0, "identifiers": 0, "skipped_no_upc": 0}

        existing_pricing = {
            (p.source_file, p.source_sheet, p.source_row): p
            for p in (await self._session.execute(
                select(ProductPricing).where(ProductPricing.source_file == source_file)
            )).scalars()
        }
        codes = {r.item_code for r in records if r.item_code}
        identities = await self.identity_for(store_number, list(codes))
        existing_ids = {
            (i.item_code, i.kind, i.value, i.distributor)
            for i in (await self._session.execute(
                select(ProductIdentifier).where(
                    ProductIdentifier.store_number == store_number,
                    ProductIdentifier.item_code.in_(list(codes)) if codes else False,
                )
            )).scalars()
        }

        for rec in records:
            if not rec.item_code:
                counts["skipped_no_upc"] += 1
                continue

            # --- pricing: one row per source row ---
            key = (source_file, rec.sheet, rec.row)
            fields = {
                "store_number": store_number, "item_code": rec.item_code,
                "distributor": rec.distributor, "pricing_basis": rec.pricing_basis,
                "case_cost": rec.case_cost, "unit_cost": rec.unit_cost,
                "previous_case_cost": rec.previous_case_cost, "package": rec.package,
                "items_per_case_stated": rec.items_per_case_stated,
                "items_per_case_derived": rec.items_per_case_derived,
                "items_per_case_derivation": rec.items_per_case_derivation,
                "effective_from": rec.effective_from, "effective_to": rec.effective_to,
                "is_conflicted": rec.is_conflicted, "conflict_detail": rec.conflict_detail,
                "source_file": source_file, "source_sheet": rec.sheet, "source_row": rec.row,
                "raw_identifier": rec.raw_identifier, "imported_at": imported_at,
            }
            row = existing_pricing.get(key)
            if row is None:
                row = ProductPricing(**fields)
                self._session.add(row)
                existing_pricing[key] = row
                counts["pricing_inserted"] += 1
            else:
                for f, v in fields.items():
                    setattr(row, f, v)
                counts["pricing_updated"] += 1

            # --- identity: first source to say a thing wins, and is named ---
            identity = identities.get(rec.item_code)
            if identity is None:
                identity = ProductIdentity(store_number=store_number, item_code=rec.item_code,
                                           provenance={})
                self._session.add(identity)
                identities[rec.item_code] = identity
                counts["identity_created"] += 1
            enriched = False
            for field in ("description", "brand", "supplier", "product_class"):
                value = getattr(rec, field)
                if value and getattr(identity, field) is None:
                    setattr(identity, field, value)
                    identity.provenance = {
                        **(identity.provenance or {}),
                        field: {"source_file": source_file, "source_sheet": rec.sheet,
                                "source_row": rec.row},
                    }
                    enriched = True
            if enriched:
                counts["identity_enriched"] += 1

            # --- identifiers ---
            for ident in rec.identifiers:
                ikey = (rec.item_code, ident.kind, ident.value, ident.distributor)
                if ikey in existing_ids:
                    continue
                self._session.add(ProductIdentifier(
                    store_number=store_number, item_code=rec.item_code, kind=ident.kind,
                    value=ident.value, distributor=ident.distributor, source_file=source_file,
                    source_sheet=rec.sheet, source_row=rec.row, imported_at=imported_at,
                ))
                existing_ids.add(ikey)
                counts["identifiers"] += 1

        await self._session.flush()
        return counts
