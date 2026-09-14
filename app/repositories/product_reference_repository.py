"""
Product reference repository — identity, identifiers, pricing.

Upserts keyed by provenance (source file/sheet/row) so a re-import of
the same workbook updates in place. Flushes, never commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_reference import ProductIdentifier, ProductIdentity, ProductPricing
from app.services.beer_inventory_import import ReferenceRecord


def _row_signature(sheet, row, item_code, unit_cost, unit_retail, case_cost, start, end) -> tuple:
    """The content of one pricing row, comparable across imports."""
    def num(v):
        return None if v is None else str(Decimal(str(v)).normalize())
    return (sheet, row, item_code, num(unit_cost), num(unit_retail), num(case_cost),
            None if start is None else str(start), None if end is None else str(end))


def records_fingerprint(records: Sequence[ReferenceRecord]) -> frozenset:
    """The same signature for parsed records, before anything is written."""
    return frozenset(
        _row_signature(r.sheet, r.row, r.item_code, r.unit_cost, r.unit_retail, r.case_cost,
                       r.effective_from, r.effective_to)
        for r in records if r.item_code
    )


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

    async def source_files_for(self, store_number: str) -> dict[str, int]:
        """Every source_file this store has pricing rows from, with row counts."""
        result = await self._session.execute(
            select(ProductPricing.source_file, func.count())
            .where(ProductPricing.store_number == store_number)
            .group_by(ProductPricing.source_file)
        )
        return dict(result.all())

    async def content_fingerprints(self, store_number: str) -> dict[str, frozenset]:
        """
        What each of this store's source files actually said, keyed by
        filename — so a copy of a workbook under another name ("(1)",
        a re-download) is recognised by its content, not its name.
        """
        result = await self._session.execute(
            select(ProductPricing).where(ProductPricing.store_number == store_number)
        )
        by_file: dict[str, set] = {}
        for row in result.scalars():
            by_file.setdefault(row.source_file, set()).add(_row_signature(
                row.source_sheet, row.source_row, row.item_code, row.unit_cost,
                row.unit_retail, row.case_cost, row.effective_from, row.effective_to,
            ))
        return {f: frozenset(v) for f, v in by_file.items()}

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

        # Scoped by store as well as file: a filename is not an identity,
        # and another store's rows from an identically named workbook are
        # not ours to update.
        existing_pricing = {
            (p.source_file, p.source_sheet, p.source_row): p
            for p in (await self._session.execute(
                select(ProductPricing).where(
                    ProductPricing.store_number == store_number,
                    ProductPricing.source_file == source_file,
                )
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
                "unit_retail": rec.unit_retail,
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
