"""
Shared units-per-case decisions — app/services/product_master/shared_decisions.py

"Verify once, use at every store." A reviewer's units-per-case decision can be
recorded for every physical store at once — a SHARED value, a Product Master
commercial mapping with no store (migration 0038) — instead of only for the
store whose invoice raised it. Commercial resolution then resolves the same
item code at every other store from that one decision.

What this module never does:
  * choose between two approved values. When stores already hold different
    approved values, setting a shared value is refused and every differing
    store is named, unless the reviewer explicitly keeps those stores' values
    as recorded store overrides (each with a reason) or has them adopt the
    shared value (each through an approved, audited proposal);
  * replace an existing shared value, or approve around an open Product
    Master candidate that disagrees — those are decided in Product Master
    Approvals, where their evidence and history are;
  * invent product identity. A shared value needs an 11-digit PDI item code;
    a product is created for it only from that code, exactly as the Item Sales
    seed derives products (UPC-A = code + check digit), and is AUTO_MATCHED,
    never CONFIRMED.

Every write is flushed in the caller's transaction and leaves a
master_commercial_reviews event naming the reviewer, their role and the
evidence (the proposal, invoice and store it came from).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.product_case_mapping import (
    MAX_UNITS_PER_CASE,
    MIN_UNITS_PER_CASE,
    ProductCaseMapping,
)
from app.models.product_master import (
    BASIS_UPC_A,
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    COMMERCIAL_UNIT_IS_SELLING_UNIT,
    DECISION_APPROVE,
    DERIVE_APPEND_CHECK_DIGIT,
    DERIVE_NONE,
    ID_PDI_ITEM_CODE,
    ID_UPC_A,
    STATE_APPROVED,
    STATE_AUTO_MATCHED,
    STATE_REJECTED,
    MasterCommercialMapping,
    MasterCommercialReview,
    MasterProduct,
    MasterProductIdentifier,
)
from app.models.store import KIND_PHYSICAL
from app.repositories.store_repository import StoreRepository
from app.services.product_master.commercial_resolution import global_mapping_holder
from app.services.product_master.identifiers import upc_a_check_digit

SOURCE_SYSTEM_INVOICE_REVIEW = "invoice_review"
NEW_MAPPING_STATE = "NONE"          # previous_approval_state of a mapping created by the decision itself


class SharedDecisionRefusedError(ValidationError):
    """A shared or override decision that would choose between approved values, or bypass one."""


def basis_for(units: int) -> str:
    return COMMERCIAL_CASE_IS_SELLING_UNIT if units == 1 else COMMERCIAL_UNIT_IS_SELLING_UNIT


def _check_code(item_code: str) -> None:
    if not (isinstance(item_code, str) and len(item_code) == 11 and item_code.isdigit()):
        raise SharedDecisionRefusedError(
            message="Only an 11-digit PDI item code (a UPC without its check digit) can carry a shared value.",
            detail={"item_code": item_code, "reason": "not_a_pdi_item_code"},
        )


def _check_units(units: int) -> None:
    if not (isinstance(units, int) and MIN_UNITS_PER_CASE <= units <= MAX_UNITS_PER_CASE):
        raise SharedDecisionRefusedError(
            message=f"Units per case must be a whole number from {MIN_UNITS_PER_CASE} to {MAX_UNITS_PER_CASE}.",
            detail={"units": units, "reason": "units_out_of_range"},
        )


# ---- product identity ---------------------------------------------------------

async def find_or_create_product(session: AsyncSession, item_code: str) -> tuple[MasterProduct, bool]:
    """The master product an 11-digit PDI item code names; created (AUTO_MATCHED) when none exists."""
    _check_code(item_code)
    upc = item_code + upc_a_check_digit(item_code)
    product = (await session.execute(select(MasterProduct).where(MasterProduct.canonical_upc == upc))).scalars().first()
    if product is not None:
        return product, False
    product = MasterProduct(canonical_key=f"upc:{upc}", canonical_upc=upc, identity_basis=BASIS_UPC_A,
                            identity_state=STATE_AUTO_MATCHED)
    session.add(product)
    await session.flush()
    observed = datetime.now(UTC)
    session.add_all([
        MasterProductIdentifier(
            product_id=product.id, raw_value=item_code, normalized_value=item_code,
            identifier_type=ID_PDI_ITEM_CODE, derivation=DERIVE_NONE,
            derivation_detail={"source": "a reviewed invoice line's PDI item code"},
            evidence_state=STATE_AUTO_MATCHED, source_system=SOURCE_SYSTEM_INVOICE_REVIEW, observed_at=observed),
        MasterProductIdentifier(
            product_id=product.id, raw_value=item_code, normalized_value=upc,
            identifier_type=ID_UPC_A, derivation=DERIVE_APPEND_CHECK_DIGIT,
            derivation_detail={"source": "PDI item code + UPC-A check digit, as the Item Sales seed derives it"},
            evidence_state=STATE_AUTO_MATCHED, source_system=SOURCE_SYSTEM_INVOICE_REVIEW, observed_at=observed),
    ])
    await session.flush()
    return product, True


# ---- what every store currently holds -------------------------------------------

@dataclass(frozen=True)
class StoreValue:
    store_id: uuid.UUID
    store_label: str
    units: int
    source: str                    # "store mapping" | "store Product Master mapping" | "store override"
    override_reason: str | None = None


@dataclass
class ItemState:
    """Every approved value held for one item code, by scope."""

    item_code: str
    stores: dict[uuid.UUID, StoreValue] = field(default_factory=dict)
    shared: MasterCommercialMapping | None = None
    holder_global: MasterCommercialMapping | None = None
    open_global_candidates: list[MasterCommercialMapping] = field(default_factory=list)

    @property
    def shared_units(self) -> int | None:
        for m in (self.shared, self.holder_global):
            if m is not None:
                return m.units_accounted_for
        return None

    def differing(self, units: int, *, exclude: set[uuid.UUID] = frozenset()) -> list[StoreValue]:
        """Stores whose effective value differs from `units` and is not already a deliberate override."""
        return [v for sid, v in sorted(self.stores.items(), key=lambda kv: kv[1].store_label)
                if sid not in exclude and v.units != units and v.override_reason is None]


async def _physical_labels(session: AsyncSession) -> dict[uuid.UUID, str]:
    return {s.id: s.label for s in await StoreRepository(session).list() if s.kind == KIND_PHYSICAL}


async def item_states(session: AsyncSession, item_codes: list[str] | None = None) -> dict[str, ItemState]:
    """
    Per item code: each physical store's effective value (its Product Master
    mapping when approved, else its legacy mapping), the approved shared value,
    the approved distributor-evidence global mapping and open global candidates.
    """
    labels = await _physical_labels(session)
    holder = await global_mapping_holder(session)
    states: dict[str, ItemState] = {}

    def state(code: str) -> ItemState:
        return states.setdefault(code, ItemState(code))

    legacy_q = select(ProductCaseMapping).where(ProductCaseMapping.store_id.in_(list(labels)))
    pm_q = select(MasterCommercialMapping)
    if item_codes is not None:
        legacy_q = legacy_q.where(ProductCaseMapping.item_code.in_(item_codes))
        pm_q = pm_q.where(MasterCommercialMapping.pdi_item_code.in_(item_codes))
    for row in (await session.execute(legacy_q)).scalars().all() if labels else []:
        state(row.item_code).stores[row.store_id] = StoreValue(row.store_id, labels[row.store_id],
                                                               row.units_per_case, "store mapping")
    for m in (await session.execute(pm_q)).scalars().all():
        s = state(m.pdi_item_code)
        if m.store_id is None or (holder is not None and m.store_id == holder.id):
            if m.approval_state == STATE_APPROVED:
                if m.store_id is None:
                    s.shared = m
                else:
                    s.holder_global = m
            elif m.approval_state != STATE_REJECTED:
                s.open_global_candidates.append(m)
        elif m.store_id in labels and m.approval_state == STATE_APPROVED and m.units_accounted_for is not None:
            s.stores[m.store_id] = StoreValue(
                m.store_id, labels[m.store_id], m.units_accounted_for,
                "store override" if m.override_reason else "store Product Master mapping", m.override_reason)
    for code in item_codes or []:
        state(code)
    return states


# ---- decisions ------------------------------------------------------------------

@dataclass
class SharedOutcome:
    mapping: MasterCommercialMapping
    created: bool
    product_created: bool = False
    overrides: list[MasterCommercialMapping] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _review(mapping: MasterCommercialMapping, *, previous_state: str, reviewer: str, reviewer_user_id,
            reviewer_role: str | None, note: str, evidence: dict[str, Any]) -> MasterCommercialReview:
    return MasterCommercialReview(
        mapping_id=mapping.id, decision=DECISION_APPROVE,
        previous_approval_state=previous_state, new_approval_state=STATE_APPROVED,
        previous_commercial_unit_basis=mapping.commercial_unit_basis, new_commercial_unit_basis=mapping.commercial_unit_basis,
        previous_units_accounted_for=None, new_units_accounted_for=mapping.units_accounted_for,
        reviewer=reviewer, reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role,
        note=note[:2000], evidence_considered=evidence,
    )


async def _new_mapping(session: AsyncSession, *, product: MasterProduct, store_id: uuid.UUID | None, item_code: str,
                       units: int, reviewer: str, reviewer_user_id, reviewer_role: str | None, note: str,
                       evidence: dict[str, Any], override_reason: str | None = None) -> MasterCommercialMapping:
    now = datetime.now(UTC)
    mapping = MasterCommercialMapping(
        product_id=product.id, store_id=store_id, pdi_item_code=item_code, commercial_unit_basis=basis_for(units),
        units_accounted_for=units, approval_state=STATE_APPROVED, reviewed_by=reviewer[:64], reviewed_at=now,
        override_reason=override_reason, source_snapshot={}, evidence=evidence,
        source_system=SOURCE_SYSTEM_INVOICE_REVIEW,
    )
    session.add(mapping)
    await session.flush()
    session.add(_review(mapping, previous_state=NEW_MAPPING_STATE, reviewer=reviewer,
                        reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role, note=note, evidence=evidence))
    await session.flush()
    return mapping


async def set_shared_value(
    session: AsyncSession, *, item_code: str, units: int, reviewer: str, reviewer_user_id=None,
    reviewer_role: str | None = None, note: str | None = None, evidence: dict[str, Any] | None = None,
    origin_store_id: uuid.UUID | None = None, keep_store_values: bool = False,
    adopting_store_ids: set[uuid.UUID] = frozenset(),
) -> SharedOutcome:
    """
    Record `units` as the shared value for `item_code`.

    Refused (nothing written) when: a different shared or distributor-global
    value is already approved; an open global candidate disagrees; or another
    physical store holds a different approved value and the reviewer neither
    kept it (`keep_store_values`: a store override is recorded for each, with
    the reason) nor listed the store in `adopting_store_ids` (the caller writes
    the adopted value for those stores). `origin_store_id`'s own value is the
    caller's to write and is not compared.
    """
    _check_code(item_code)
    _check_units(units)
    evidence = dict(evidence or {})
    note = (note or "").strip() or "Shared units-per-case decided in review."
    st = (await item_states(session, [item_code]))[item_code]

    for existing, label in ((st.shared, "shared value"), (st.holder_global, "distributor-evidence global mapping")):
        if existing is not None and existing.units_accounted_for != units:
            raise SharedDecisionRefusedError(
                message=(f"An approved {label} already says {existing.units_accounted_for} for this item. "
                         "Reopen it in Product Master Approvals, or approve this value for its store only."),
                detail={"reason": "shared_value_differs", "item_code": item_code,
                        "existing_units": existing.units_accounted_for, "mapping_id": str(existing.id)},
            )
    disagreeing = [c for c in st.open_global_candidates
                   if c.units_accounted_for is not None and c.units_accounted_for != units]
    if disagreeing:
        raise SharedDecisionRefusedError(
            message=("A Product Master candidate for this item says "
                     f"{disagreeing[0].units_accounted_for} and is still open. Decide it in Product Master "
                     "Approvals first, or approve this value for its store only."),
            detail={"reason": "open_candidate_differs", "item_code": item_code,
                    "candidates": {str(c.id): c.units_accounted_for for c in disagreeing}},
        )
    origin = st.stores.get(origin_store_id) if origin_store_id else None
    if origin is not None and origin.source != "store mapping" and origin.units != units:
        raise SharedDecisionRefusedError(
            message=(f"This store has an approved Product Master value of {origin.units} for this item. "
                     "Reopen it in Product Master Approvals before sharing a different value."),
            detail={"reason": "origin_store_value_differs", "item_code": item_code, "units": origin.units},
        )
    exclude = {origin_store_id} if origin_store_id else set()
    differing = [v for v in st.differing(units, exclude=exclude) if v.store_id not in adopting_store_ids]
    if differing and not keep_store_values:
        raise SharedDecisionRefusedError(
            message=(f"{len(differing)} other store(s) already hold a different approved value for this item "
                     f"({', '.join(f'{v.store_label}: {v.units}' for v in differing[:5])}). Keep those stores' "
                     "values as store overrides, have them adopt this value, or approve for this store only."),
            detail={"reason": "stores_differ", "item_code": item_code,
                    "stores": [{"store_id": str(v.store_id), "store": v.store_label, "units": v.units,
                                "source": v.source} for v in differing]},
        )

    product, product_created = await find_or_create_product(session, item_code)
    outcome: SharedOutcome
    if st.shared is not None:              # same value already shared: nothing new to decide
        outcome = SharedOutcome(st.shared, created=False, product_created=product_created,
                                notes=["the shared value was already approved; nothing changed"])
    else:
        mapping = await _new_mapping(
            session, product=product, store_id=None, item_code=item_code, units=units, reviewer=reviewer,
            reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role, note=note,
            evidence=evidence | {"decision": "shared_value", "origin_store_id": str(origin_store_id or "")},
        )
        outcome = SharedOutcome(mapping, created=True, product_created=product_created)
    for v in differing:                    # keep_store_values: each kept value becomes a recorded override
        reason = (f"Kept this store's existing value ({v.units}) when the shared value {units} was set"
                  f" — {note}")[:500]
        override = await _new_mapping(
            session, product=product, store_id=v.store_id, item_code=item_code, units=v.units, reviewer=reviewer,
            reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role, note=reason,
            evidence=evidence | {"decision": "kept_store_value", "previous_source": v.source},
            override_reason=reason,
        )
        outcome.overrides.append(override)
    return outcome


async def set_store_override(
    session: AsyncSession, *, store_id: uuid.UUID, item_code: str, units: int, reason: str, reviewer: str,
    reviewer_user_id=None, reviewer_role: str | None = None, evidence: dict[str, Any] | None = None,
) -> MasterCommercialMapping:
    """
    Record a physical store's deliberate exception to the shared value, with a
    reason. Refused when the store already holds a different approved Product
    Master value (reopen that one first) or the store is not physical.
    """
    _check_code(item_code)
    _check_units(units)
    reason = (reason or "").strip()
    if not reason:
        raise SharedDecisionRefusedError(message="Say why this store differs; the reason is kept with the override.",
                                    detail={"reason": "override_reason_required"})
    labels = await _physical_labels(session)
    if store_id not in labels:
        raise SharedDecisionRefusedError(message="Only a physical store can hold an override.",
                                    detail={"reason": "not_a_physical_store", "store_id": str(store_id)})
    existing = (await session.execute(select(MasterCommercialMapping).where(
        MasterCommercialMapping.store_id == store_id, MasterCommercialMapping.pdi_item_code == item_code,
        MasterCommercialMapping.approval_state == STATE_APPROVED,
    ))).scalars().first()
    if existing is not None:
        if existing.units_accounted_for == units:
            return existing
        raise SharedDecisionRefusedError(
            message=(f"This store already has an approved Product Master value of {existing.units_accounted_for}. "
                     "Reopen it in Product Master Approvals before recording a different one."),
            detail={"reason": "store_value_exists", "mapping_id": str(existing.id)},
        )
    product, _ = await find_or_create_product(session, item_code)
    return await _new_mapping(
        session, product=product, store_id=store_id, item_code=item_code, units=units, reviewer=reviewer,
        reviewer_user_id=reviewer_user_id, reviewer_role=reviewer_role, note=reason,
        evidence=dict(evidence or {}) | {"decision": "store_override"}, override_reason=reason[:500],
    )


# ---- cross-store differences ---------------------------------------------------------

@dataclass(frozen=True)
class Difference:
    item_code: str
    shared_units: int | None
    stores: list[StoreValue]
    overrides: list[StoreValue]
    distinct_values: list[int]


async def cross_store_differences(session: AsyncSession) -> list[Difference]:
    """
    Item codes whose approved values disagree between physical stores, or with
    the shared value — every place where approved data conflicts. Deliberate
    store overrides are listed beside, not counted as a disagreement.
    """
    found: list[Difference] = []
    for code, st in sorted((await item_states(session)).items()):
        plain = [v for v in st.stores.values() if v.override_reason is None]
        values = {v.units for v in plain} | ({st.shared_units} if st.shared_units is not None else set())
        if len(values) > 1:
            found.append(Difference(
                item_code=code, shared_units=st.shared_units,
                stores=sorted(plain, key=lambda v: v.store_label),
                overrides=sorted((v for v in st.stores.values() if v.override_reason), key=lambda v: v.store_label),
                distinct_values=sorted(values),
            ))
    return found

