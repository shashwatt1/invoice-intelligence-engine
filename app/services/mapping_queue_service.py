"""
Requires Mapping — app/services/mapping_queue_service.py

Derives the set of unresolved case-mapping requirements across every
processed, store-assigned invoice, grouped by (store, normalized UPC)
so the same master-data gap seen on several invoices is one work item,
not several.

Purely a read over existing state — invoice_items, joined against
the commercial resolution (product_case_mappings, and approved Product
Master mappings when that resolution is on) for what already has an
authoritative value.
There is no separate queue table (see PART 17 of the phase spec): a
gap disappears from here the instant proposal_service.approve() writes
the mapping, because this is recomputed fresh on every call, never
cached or synchronized by hand.

Deliberately reuses pdi_items() and normalize_item_code()
(app.services.export_service) — the exact same functions the export
gate and case_mapping_service use to decide "does this line need a
mapping" — so this queue's definition of "unresolved" can never drift
from what actually blocks EDI export.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invoice import Invoice
from app.services.export_service import normalize_item_code, pdi_items
from app.services.product_master.commercial_resolution import resolve_store_codes
from app.services.proposal_service import pending_by_item_code


@dataclass(frozen=True)
class UnresolvedMappingOccurrence:
    """One invoice line waiting on this product's mapping."""

    invoice_id: uuid.UUID
    document_id: uuid.UUID
    invoice_number: str | None
    description: str | None
    quantity: float
    unit_price: float | None
    pack_size: str | None


@dataclass(frozen=True)
class UnresolvedMappingGroup:
    """One master-data gap: a (store, UPC) with no authoritative mapping."""

    store_id: uuid.UUID
    item_code: str
    description: str | None
    occurrences: list[UnresolvedMappingOccurrence] = field(default_factory=list)
    pending_proposal_id: str | None = None
    pending_value: int | None = None
    pending_proposed_by: str | None = None

    @property
    def invoice_count(self) -> int:
        return len({o.invoice_id for o in self.occurrences})


async def list_unresolved_mapping_groups(
    session: AsyncSession, *, store_id: uuid.UUID | None = None
) -> list[UnresolvedMappingGroup]:
    """
    Every (store, UPC) with a product line on some invoice and no
    product_case_mappings row, across ALL invoices — not filtered by
    uploader, not dependent on a proposal having been submitted.

    STORE_PENDING invoices (store_id NULL) are excluded: there is no
    store to scope a mapping check against, matching
    persisted_pdi_export_eligibility's own STORE_PENDING gate.
    """
    query = select(Invoice).where(Invoice.store_id.isnot(None)).options(selectinload(Invoice.items))
    if store_id is not None:
        query = query.where(Invoice.store_id == store_id)
    invoices = (await session.execute(query)).scalars().all()

    codes_by_store: dict[uuid.UUID, set[str]] = {}
    for invoice in invoices:
        for item in pdi_items(invoice):
            code = normalize_item_code(item.product_sku)
            if code is not None:
                codes_by_store.setdefault(invoice.store_id, set()).add(code)

    # The same resolution the export gate uses, so a line the export accepts
    # is never listed here and a line it blocks always is.
    mapped_by_store: dict[uuid.UUID, dict[str, int]] = {
        store: (await resolve_store_codes(session, store, sorted(codes))).units
        for store, codes in codes_by_store.items()
    }

    occurrences_by_key: dict[tuple[uuid.UUID, str], list[UnresolvedMappingOccurrence]] = {}
    description_by_key: dict[tuple[uuid.UUID, str], str | None] = {}
    for invoice in invoices:
        mapped = mapped_by_store.get(invoice.store_id, {})
        for item in pdi_items(invoice):
            code = normalize_item_code(item.product_sku)
            if code is None or code in mapped:
                continue
            key = (invoice.store_id, code)
            occurrences_by_key.setdefault(key, []).append(UnresolvedMappingOccurrence(
                invoice_id=invoice.id,
                document_id=invoice.document_id,
                invoice_number=invoice.invoice_number,
                description=item.description,
                quantity=float(item.quantity),
                unit_price=float(item.unit_price) if item.unit_price is not None else None,
                pack_size=item.pack_size,
            ))
            if item.description:
                description_by_key[key] = item.description

    if not occurrences_by_key:
        return []

    unresolved_codes_by_store: dict[uuid.UUID, list[str]] = {}
    for store, code in occurrences_by_key:
        unresolved_codes_by_store.setdefault(store, []).append(code)
    pending_by_key: dict[tuple[uuid.UUID, str], object] = {}
    for store, codes in unresolved_codes_by_store.items():
        pending = await pending_by_item_code(session, store, codes)
        for code, proposal in pending.items():
            pending_by_key[(store, code)] = proposal

    groups = [
        UnresolvedMappingGroup(
            store_id=store,
            item_code=code,
            description=description_by_key.get((store, code)),
            occurrences=occurrences,
            pending_proposal_id=str(pending_by_key[(store, code)].id) if (store, code) in pending_by_key else None,
            pending_value=int(pending_by_key[(store, code)].proposed_value) if (store, code) in pending_by_key else None,
            pending_proposed_by=pending_by_key[(store, code)].proposed_by if (store, code) in pending_by_key else None,
        )
        for (store, code), occurrences in occurrences_by_key.items()
    ]
    # Most-affected first: more invoices sharing the same gap is a bigger master-data problem.
    groups.sort(key=lambda g: (-len(g.occurrences), g.item_code))
    return groups
