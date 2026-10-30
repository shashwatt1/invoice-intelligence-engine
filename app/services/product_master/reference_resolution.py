"""
Product reference resolution for inventory — app/services/product_master/reference_resolution.py

Which units-per-case and which description a store's inventory product gets from reviewed
commercial data, and on whose authority. It READS the decisions the application already makes and
adds no rule of its own:

  * units per case  -> commercial_resolution.resolve_store_codes — the one resolver the EDI export,
                       the invoice detail and the Requires Mapping queue already share, so inventory
                       can never disagree with them. Per code, in order: an APPROVED store-specific
                       Product Master mapping; an APPROVED shared / global one (only where no store
                       decision of its own disagrees); the store's confirmed case mapping; nothing.
                       Two approved values that disagree are a CONFLICT and nothing is chosen.
  * description     -> commercial_resolution.canonical_names — the confirmed canonical description
                       or name of the ONE master product the item code reaches.

On top of that this module adds only what inventory needs:

  * Assortment stays separate. These functions are handed item codes; they never add one. A product
    belongs to a store's inventory because its catalogue lists it or it has a movement — never
    because a commercial mapping exists. Nothing here writes: no movement, receipt, opening count,
    catalogue row, proposal or mapping.
  * Provenance on every value: the source path, the scope it was approved in, its approval status and
    the mapping id (what a reviewer can look up).
  * Ambiguous product identity is held. If an item code reaches more than one master product (the UPC
    is shared by several, or the approved mapping belongs to a different product than the UPC names), no
    value derived from the product is used — neither the units nor the description — and the code is
    HELD for review with the reason.
  * An explicitly confirmed store-specific value is never silently replaced: it is only ever replaced by
    nothing (see resolve_code: a shared value that disagrees with a store's own value is a CONFLICT).

Statuses: RESOLVED (a value, with provenance), MISSING (no reviewed source says anything), HELD (a source
exists but must be decided by a person first: conflict, ambiguity, or a mapping not yet approved is
reported as a note and the code stays MISSING).

When `product_master_commercial_resolution` is off, resolve_store_codes reads only the store's case
mapping, exactly as the EDI export does; inventory follows it so the two never differ.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_master import MasterProduct
from app.services.product_master.commercial_resolution import (
    NAME_FROM_CANONICAL_DESCRIPTION,
    NAME_FROM_CANONICAL_NAME,
    PATH_CONFLICT,
    PATH_LEGACY_FALLBACK,
    PATH_PRODUCT_MASTER_GLOBAL,
    PATH_PRODUCT_MASTER_PHYSICAL,
    PATH_REQUIRES_MAPPING,
    CodeResolution,
    canonical_names,
    resolve_store_codes,
    upc12_for,
)

STATUS_RESOLVED = "RESOLVED"
STATUS_MISSING = "MISSING"
STATUS_HELD = "HELD"

APPROVAL_APPROVED = "APPROVED"                 # a reviewer approved the Product Master mapping
APPROVAL_STORE_CONFIRMED = "STORE_CONFIRMED"   # the store's own confirmed case mapping (legacy authority)
APPROVAL_CONFIRMED = "CONFIRMED"               # a person confirmed the canonical description


@dataclass(frozen=True)
class ResolvedValue:
    value: int | str | None
    status: str
    source: str | None = None          # the resolution path / description source it came from
    scope: str | None = None           # where it was approved: a store, "All stores (shared value)", ...
    approval: str | None = None
    mapping_id: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProductReference:
    item_code: str
    units_per_case: ResolvedValue
    description: ResolvedValue


def _held(note: str, *notes: str) -> ResolvedValue:
    return ResolvedValue(None, STATUS_HELD, notes=(note, *notes))


def units_value(line: CodeResolution | None, ambiguous: str | None = None) -> ResolvedValue:
    """The units-per-case a resolution amounts to, with provenance. Pure."""
    if line is None or line.path == PATH_REQUIRES_MAPPING:
        return ResolvedValue(None, STATUS_MISSING, notes=line.notes if line is not None else ())
    if line.path == PATH_CONFLICT:
        return _held("conflicting approved values: nothing is chosen until a person decides", *line.notes)
    if line.units_per_case is None:
        return ResolvedValue(None, STATUS_MISSING, notes=line.notes)
    if ambiguous:
        return _held(f"product identity is ambiguous: {ambiguous}", *line.notes)
    if line.path == PATH_PRODUCT_MASTER_PHYSICAL:
        return ResolvedValue(line.units_per_case, STATUS_RESOLVED, line.path, line.scope_label, APPROVAL_APPROVED,
                             line.mapping_id, line.notes)
    if line.path == PATH_PRODUCT_MASTER_GLOBAL:
        return ResolvedValue(line.units_per_case, STATUS_RESOLVED, line.path, line.scope_label, APPROVAL_APPROVED,
                             line.mapping_id, line.notes)
    if line.path == PATH_LEGACY_FALLBACK:
        return ResolvedValue(line.units_per_case, STATUS_RESOLVED, line.path, "This store's confirmed case mapping",
                             APPROVAL_STORE_CONFIRMED, None, line.notes)
    return _held(f"resolution path {line.path} is not used for inventory", *line.notes)


def description_value(canonical_description: str | None, canonical_name: str | None,
                      ambiguous: str | None = None) -> ResolvedValue:
    """The reviewed commercial description a product has, with provenance. Pure."""
    if ambiguous:
        return _held(f"product identity is ambiguous: {ambiguous}")
    if canonical_description:
        return ResolvedValue(canonical_description, STATUS_RESOLVED, NAME_FROM_CANONICAL_DESCRIPTION,
                             "Product Master", APPROVAL_CONFIRMED)
    if canonical_name:
        return ResolvedValue(canonical_name, STATUS_RESOLVED, NAME_FROM_CANONICAL_NAME, "Product Master",
                             APPROVAL_CONFIRMED)
    return ResolvedValue(None, STATUS_MISSING)


def ambiguity_reason(code: str, by_upc: Sequence[str], mapped_product: str | None) -> str | None:
    """
    Why an item code does not name exactly one master product, or None when it does (or names none at all).
    `by_upc` are the master products whose canonical UPC the code stands for; `mapped_product` is the product
    the chosen approved mapping belongs to. Pure.
    """
    distinct = sorted(set(by_upc))
    if len(distinct) > 1:
        return f"{len(distinct)} master products share the UPC of item {code}"
    if mapped_product and distinct and mapped_product not in distinct:
        return f"the approved mapping for item {code} belongs to a different master product than its UPC names"
    return None


async def identity_ambiguities(session: AsyncSession, codes: Sequence[str],
                               product_ids: dict[str, str] | None = None) -> dict[str, str]:
    """Per item code that is ambiguous, why. Codes that name one product, or none, are absent."""
    upc_of = {c: u for c in codes if (u := upc12_for(c))}
    if not upc_of:
        return {}
    products = (await session.execute(
        select(MasterProduct.id, MasterProduct.canonical_upc).where(MasterProduct.canonical_upc.in_(list(upc_of.values())))
    )).all()
    by_upc: dict[str, list[str]] = {}
    for pid, upc in products:
        by_upc.setdefault(upc, []).append(str(pid))
    out: dict[str, str] = {}
    for code, upc in upc_of.items():
        reason = ambiguity_reason(code, by_upc.get(upc, []), (product_ids or {}).get(code))
        if reason:
            out[code] = reason
    return out


async def resolve_references(session: AsyncSession, store_id: uuid.UUID, codes: Sequence[str]) -> dict[str, ProductReference]:
    """
    The reference values for `codes` at one store. Reads only. A code that is not asked for is never added,
    and no code is added because a mapping exists for it.
    """
    wanted = [c for c in dict.fromkeys(codes) if c]
    if not wanted:
        return {}
    resolution = await resolve_store_codes(session, store_id, wanted)
    product_ids = {c: line.product_id for c, line in resolution.lines.items() if line.product_id}
    ambiguous = await identity_ambiguities(session, wanted, product_ids)
    clear = [c for c in wanted if c not in ambiguous]
    names = await canonical_names(session, clear, {c: p for c, p in product_ids.items() if c in clear}) if clear else {}
    return {
        c: ProductReference(
            c, units_value(resolution.lines.get(c), ambiguous.get(c)),
            description_value(*names.get(c, (None, None)), ambiguous.get(c)))
        for c in wanted
    }
