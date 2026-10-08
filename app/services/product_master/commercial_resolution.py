"""
Commercial resolution — app/services/product_master/commercial_resolution.py

Which units-per-case a store's invoice line gets, and on whose authority.
This is the one place that decides it; the export gate, the EDI file, the
invoice detail and the Requires Mapping queue all read its answer, so they
cannot disagree. The EDI generator itself is unchanged: it still takes a
plain {item_code: units} dict.

Per item code, in this fixed order:

  1. PRODUCT_MASTER_PHYSICAL_STORE   an APPROVED commercial mapping held by the
                                     invoice's own physical store
  2. PRODUCT_MASTER_GLOBAL           an APPROVED global commercial mapping —
                                     distributor/product evidence that applies
                                     to every physical store. For this
                                     transition the global mappings are the
                                     ones held under the configured Item Sales
                                     location (47708760): that location is
                                     provenance — where the evidence was
                                     imported from — not a vendor, not a
                                     physical store and not itself the scope.
                                     No store record is read for writing.
  3. LEGACY_FALLBACK                 the store's product_case_mappings row,
                                     exactly as the live export used it before
  4. REQUIRES_MAPPING                nothing above: the line enters the
                                     existing Requires Mapping workflow
  -  CONFLICT                        two approved Product Master mappings
                                     disagree (a physical-store override vs the
                                     global mapping, or two in one scope). Nothing is chosen; the
                                     line blocks export until a person decides.

Physical-store mappings are explicit store-specific overrides of the global
mapping (business rule, 2026-10-08).

Only APPROVED Product Master mappings are ever used. A mapping awaiting
review (REVIEW_REQUIRED, PENDING, a CONFLICT basis) or REJECTED is reported
and skipped; the legacy value applies where it already did.

Off unless `product_master_commercial_resolution` is set: then every line
resolves exactly as before (legacy, or requires mapping), so turning this on
is a deliberate, reversible deployment decision rather than a side effect.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.product_master import (
    DESC_CANONICAL,
    STATE_APPROVED,
    MasterCommercialMapping,
    MasterProduct,
    MasterProductDescription,
)
from app.models.store import (
    KIND_PHYSICAL,
    KIND_SOURCE_IDENTITY,
    SOURCE_ITEM_SALES,
    TYPE_STORE_CODE,
    Store,
)
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.store_repository import StoreRepository
from app.services.product_master.display_name import NAME_CANONICAL, resolve_display_name
from app.services.product_master.identifiers import upc_a_check_digit

logger = get_logger(__name__)

PATH_PRODUCT_MASTER_PHYSICAL = "PRODUCT_MASTER_PHYSICAL_STORE"
PATH_PRODUCT_MASTER_GLOBAL = "PRODUCT_MASTER_GLOBAL"
PATH_LEGACY_FALLBACK = "LEGACY_FALLBACK"
PATH_REQUIRES_MAPPING = "REQUIRES_MAPPING"
PATH_CONFLICT = "CONFLICT"
PRODUCT_MASTER_PATHS = frozenset({PATH_PRODUCT_MASTER_PHYSICAL, PATH_PRODUCT_MASTER_GLOBAL})

# Where a line's normalized product name came from. Never PDI: the
# application holds no PDI description, and does not claim one.
NAME_FROM_CANONICAL_DESCRIPTION = "PRODUCT_MASTER_CANONICAL_DESCRIPTION"
NAME_FROM_CANONICAL_NAME = "PRODUCT_MASTER_CANONICAL_NAME"
NAME_FROM_INVOICE = "INVOICE_DESCRIPTION"


@dataclass(frozen=True)
class ScopedMapping:
    """A Product Master commercial mapping, as the resolver needs it."""

    mapping_id: str
    product_id: str
    approval_state: str
    commercial_unit_basis: str
    units_accounted_for: int | None


@dataclass(frozen=True)
class CodeResolution:
    item_code: str
    path: str
    units_per_case: int | None
    mapping_id: str | None = None
    product_id: str | None = None
    scope_store_id: str | None = None
    scope_label: str | None = None
    legacy_units: int | None = None
    notes: tuple[str, ...] = ()

    @property
    def product_master_authoritative(self) -> bool:
        return self.path in PRODUCT_MASTER_PATHS


@dataclass(frozen=True)
class Scope:
    """One place a commercial mapping may come from."""

    store_id: str
    label: str


def global_scope_label(item_sales_code: str) -> str:
    """How the global scope is named: global, with where its evidence is held as provenance."""
    return f"Global (distributor evidence, held under Item Sales · {item_sales_code})"


def _describe(m: ScopedMapping) -> str:
    return f"{m.commercial_unit_basis} x{m.units_accounted_for}"


def resolve_code(
    code: str,
    *,
    physical: Sequence[ScopedMapping] = (),
    global_: Sequence[ScopedMapping] = (),
    legacy_units: int | None = None,
    physical_scope: Scope | None = None,
    global_scope: Scope | None = None,
) -> CodeResolution:
    """The resolution for one item code. Pure: everything it reads is passed in."""
    approved_physical = [m for m in physical if m.approval_state == STATE_APPROVED]
    approved_global = [m for m in global_ if m.approval_state == STATE_APPROVED]
    notes: list[str] = []

    def conflict(reason: str) -> CodeResolution:
        return CodeResolution(code, PATH_CONFLICT, None, legacy_units=legacy_units, notes=(reason,))

    for scope, approved in ((physical_scope, approved_physical), (global_scope, approved_global)):
        if len(approved) > 1:
            return conflict(f"{len(approved)} approved Product Master mappings for this code in "
                            f"{scope.label if scope else 'one scope'}")
    if approved_physical and approved_global:
        p, g = approved_physical[0], approved_global[0]
        if (p.commercial_unit_basis, p.units_accounted_for) != (g.commercial_unit_basis, g.units_accounted_for):
            return conflict(f"physical-store override ({_describe(p)}) and global mapping "
                            f"({_describe(g)}) disagree")
        notes.append("global mapping agrees with the physical-store override")

    if approved_physical or approved_global:
        chosen, scope, path = (
            (approved_physical[0], physical_scope, PATH_PRODUCT_MASTER_PHYSICAL) if approved_physical
            else (approved_global[0], global_scope, PATH_PRODUCT_MASTER_GLOBAL)
        )
        if chosen.units_accounted_for is None:
            return conflict("approved Product Master mapping carries no units_accounted_for")
        if legacy_units is not None and legacy_units != chosen.units_accounted_for:
            notes.append(f"legacy mapping says {legacy_units}; the approved Product Master value is used")
        return CodeResolution(
            code, path, chosen.units_accounted_for, mapping_id=chosen.mapping_id, product_id=chosen.product_id,
            scope_store_id=scope.store_id if scope else None, scope_label=scope.label if scope else None,
            legacy_units=legacy_units, notes=tuple(notes),
        )

    for scope, mappings in ((physical_scope, physical), (global_scope, global_)):
        for m in mappings:
            notes.append(f"Product Master mapping in {scope.label if scope else 'scope'} is "
                         f"{m.approval_state} ({m.commercial_unit_basis}) — not used until approved")
    if legacy_units is not None:
        return CodeResolution(code, PATH_LEGACY_FALLBACK, legacy_units, legacy_units=legacy_units, notes=tuple(notes))
    return CodeResolution(code, PATH_REQUIRES_MAPPING, None, notes=tuple(notes))


@dataclass
class CommercialResolution:
    """Every item code's resolution for one store."""

    lines: dict[str, CodeResolution] = field(default_factory=dict)
    enabled: bool = False

    @property
    def units(self) -> dict[str, int]:
        """What the exporter and the export gate consume. A CONFLICT or unmapped code is absent: it blocks."""
        return {code: r.units_per_case for code, r in self.lines.items()
                if r.units_per_case is not None and r.path != PATH_CONFLICT}

    def paths(self) -> dict[str, int]:
        return dict(Counter(r.path for r in self.lines.values()))


async def global_mapping_holder(session: AsyncSession) -> Store | None:
    """
    The Item Sales location record the global mappings are held under, when
    one is configured (`commercial_source_identity`). It is provenance only:
    found by its exact Item Sales store code, and refused if that record is a
    physical store, because one store's own mappings must never be read as
    global.
    """
    code = get_settings().commercial_source_identity.strip()
    if not code:
        return None
    store = await StoreRepository(session).by_identifier(SOURCE_ITEM_SALES, TYPE_STORE_CODE, code)
    if store is None:
        logger.warning("global_mapping_holder_not_found", store_code=code)
        return None
    if store.kind != KIND_SOURCE_IDENTITY:
        logger.warning("global_mapping_holder_is_physical_refused", store_code=code, store_id=str(store.id))
        return None
    return store


async def resolve_store_codes(
    session: AsyncSession, store_id: uuid.UUID, codes: Sequence[str],
) -> CommercialResolution:
    """Resolve item codes for one store. Reads only; writes nothing."""
    codes = [c for c in dict.fromkeys(codes) if c]
    legacy = await ProductCaseMappingRepository(session).units_by_item_code(store_id, codes) if codes else {}
    if not get_settings().product_master_commercial_resolution:
        return CommercialResolution({
            c: (CodeResolution(c, PATH_LEGACY_FALLBACK, legacy[c], legacy_units=legacy[c]) if c in legacy
                else CodeResolution(c, PATH_REQUIRES_MAPPING, None))
            for c in codes
        }, enabled=False)

    store = await StoreRepository(session).get(store_id)
    physical = Scope(str(store.id), store.label) if store is not None and store.kind == KIND_PHYSICAL else None
    holder = await global_mapping_holder(session)
    global_ = (Scope(str(holder.id), global_scope_label(get_settings().commercial_source_identity.strip()))
               if holder is not None else None)

    scope_ids = [uuid.UUID(s.store_id) for s in (physical, global_) if s is not None]
    rows = (await session.execute(
        select(MasterCommercialMapping).where(
            MasterCommercialMapping.store_id.in_(scope_ids),
            MasterCommercialMapping.pdi_item_code.in_(codes),
        )
    )).scalars().all() if scope_ids and codes else []
    by_scope: dict[tuple[str, str], list[ScopedMapping]] = {}
    for row in rows:
        by_scope.setdefault((str(row.store_id), row.pdi_item_code), []).append(ScopedMapping(
            str(row.id), str(row.product_id), row.approval_state, row.commercial_unit_basis, row.units_accounted_for))

    resolution = CommercialResolution(enabled=True)
    for c in codes:
        resolution.lines[c] = resolve_code(
            c,
            physical=by_scope.get((physical.store_id, c), []) if physical else [],
            global_=by_scope.get((global_.store_id, c), []) if global_ else [],
            legacy_units=legacy.get(c), physical_scope=physical, global_scope=global_,
        )
    fallback = sorted(c for c, r in resolution.lines.items() if r.path == PATH_LEGACY_FALLBACK)
    logger.info(
        "commercial_resolution", store_id=str(store_id), paths=resolution.paths(),
        legacy_fallback_codes=fallback,
        conflict_codes=sorted(c for c, r in resolution.lines.items() if r.path == PATH_CONFLICT),
    )
    return resolution


# ---- normalized product name ----------------------------------------------------

def upc12_for(code: str) -> str | None:
    """The 12-digit UPC-A an 11-digit item code stands for, as master products store it."""
    return code + upc_a_check_digit(code) if len(code) == 11 and code.isdigit() else None


def choose_product_name(invoice_description: str | None, canonical_description: str | None,
                        canonical_name: str | None) -> tuple[str | None, str]:
    """
    The normalized name a line shows, and what it rests on. The invoice
    wording is the fallback and stays the line's source evidence either way.
    """
    if canonical_description:
        return canonical_description, NAME_FROM_CANONICAL_DESCRIPTION
    if canonical_name:
        return canonical_name, NAME_FROM_CANONICAL_NAME
    return invoice_description, NAME_FROM_INVOICE


async def canonical_names(
    session: AsyncSession, codes: Sequence[str], product_ids: dict[str, str] | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """
    Per item code: (canonical_description, canonical display name). Only a
    code that reaches exactly one master product gets either; a source-derived
    wording is an alias, never a normalized name (description policy §4).
    """
    upc_by_code = {c: u for c in codes if (u := upc12_for(c))}
    wanted_ids = {uuid.UUID(p) for p in (product_ids or {}).values() if p}
    if not upc_by_code and not wanted_ids:
        return {}
    clause: ColumnElement[bool] = MasterProduct.canonical_upc.in_(list(upc_by_code.values()))
    if wanted_ids:
        clause = or_(clause, MasterProduct.id.in_(wanted_ids))
    products = (await session.execute(select(MasterProduct).where(clause))).scalars().all()
    rows = (await session.execute(select(MasterProductDescription).where(
        MasterProductDescription.product_id.in_([p.id for p in products]),
        MasterProductDescription.role == DESC_CANONICAL,
    ))).scalars().all() if products else []
    canonical_rows: dict[uuid.UUID, list] = {}
    for row in rows:
        canonical_rows.setdefault(row.product_id, []).append(row)

    names: dict[str, tuple[str | None, str | None]] = {}
    for code in codes:
        matches = {p.id: p for p in products
                   if p.canonical_upc == upc_by_code.get(code) or str(p.id) == (product_ids or {}).get(code)}
        if len(matches) != 1:
            continue
        product = next(iter(matches.values()))
        display = resolve_display_name(canonical_rows.get(product.id, []))
        names[code] = (product.canonical_description,
                       display.name if display.basis == NAME_CANONICAL else None)
    return names


# ---- what the invoice detail shows --------------------------------------------

def resolution_fields(resolution: CommercialResolution | None, code: str | None) -> dict:
    """A case-mapping row's resolution: which path produced its units, from which scope."""
    line = resolution.lines.get(code) if resolution is not None and code else None
    if line is None:
        return {}
    return {
        "resolution_path": line.path,
        "resolution_scope": line.scope_label,
        "resolution_mapping_id": line.mapping_id,
        "resolution_notes": list(line.notes),
    }


async def line_product_names(
    session: AsyncSession, invoice, resolution: CommercialResolution | None,
) -> dict[int, dict[str, str | None]]:
    """
    Per line (by sort_order): the normalized product name and what it rests on.
    The line's own description is untouched — it stays the source evidence.
    """
    from app.services.export_service import (
        normalize_item_code,  # local: export_service imports nothing from here
    )

    codes = {item.sort_order: normalize_item_code(item.product_sku) for item in invoice.items}
    product_ids = {c: r.product_id for c, r in resolution.lines.items() if r.product_id} if resolution else {}
    found = await canonical_names(session, sorted({c for c in codes.values() if c}), product_ids)
    names: dict[int, dict[str, str | None]] = {}
    for item in invoice.items:
        canonical_description, canonical_name = found.get(codes[item.sort_order] or "", (None, None))
        name, source = choose_product_name(item.description, canonical_description, canonical_name)
        names[item.sort_order] = {"normalized_description": name, "normalized_description_source": source}
    return names
