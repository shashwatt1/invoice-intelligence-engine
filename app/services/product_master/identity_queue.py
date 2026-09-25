"""
Identity-unresolved queue — app/services/product_master/identity_queue.py

The reference rows that carried a commercial statement but never produced a
canonical product, so there was nothing for a commercial mapping to hang
from.

Served from the database, never from the reference workbooks. Those live
under data/reference/, are gitignored source evidence, and are not present
in the deployed container — reading them at request time worked locally and
would fail in the pilot. What a reviewer needs is the persisted snapshot,
which the seed captured.

Kept separate from the commercial review queue on purpose: these are not
candidates awaiting approval, they are rows awaiting an identity. Read-only
— nothing here creates a product.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_master import MasterCommercialMapping, MasterProduct


async def load_unresolved_candidates(session: AsyncSession) -> list[dict]:
    """
    Commercial candidates whose product identity never resolved.

    An unresolved identity has no canonical barcode, so these are the rows
    a reviewer cannot act on until identity is settled elsewhere.
    """
    rows = (await session.execute(
        select(MasterCommercialMapping, MasterProduct)
        .join(MasterProduct, MasterProduct.id == MasterCommercialMapping.product_id)
        .where(MasterProduct.canonical_upc.is_(None))
    )).all()

    candidates: list[dict] = []
    for mapping, _product in rows:
        snapshot = (mapping.source_snapshot or {}).get("source_rows") or [{}]
        first = snapshot[0]
        candidates.append({
            "raw_identifier": first.get("raw_identifier"),
            "source_system": mapping.source_system,
            "source_store_identifier": first.get("source_store_identifier"),
            "source_file": mapping.source_file or first.get("source_file") or "",
            "source_sheet": mapping.source_sheet or first.get("source_sheet") or "",
            "source_row": mapping.source_row or first.get("source_row") or 0,
            "description": first.get("raw_description"),
            "reason": "identifier did not resolve to a canonical barcode",
            "units_statement": first.get("raw_items_case"),
        })
    return sorted(candidates, key=lambda r: (r["source_sheet"], r["source_row"]))
