"""
Line-by-line Product Master shadow trace — app/services/product_master/shadow_trace.py

For one stored invoice, follows every PDI line down both paths and says
where they part:

    printed code -> normalized item code
        LEGACY   product_case_mappings in the invoice's own store -> B record
        MASTER   the live commercial resolution (commercial_resolution.resolve_code):
                 an APPROVED physical-store override, else the APPROVED global
                 mapping -> B record

and, separately, compares the line's nomenclature:

    A  the invoice description (what live EDI emits today, cut to 25 chars)
    B  the Product Master canonical description
    C  PDI's own description for the item code, when one is supplied

Pure and read-only: the caller loads everything (scripts/shadow_edi_compare.py
does, inside a read-only transaction) and this module only reads it. The B
records come from the live exporter's own field encoders, unchanged; the
invoice and its items are never modified.

Scoping is deliberate. A mapping held by another store applies only when it
is a global mapping — held under the configured Item Sales location
(47708760), which is provenance, not the scope — and it is then reported as
Product Master · Global, never as the invoice's physical store. Any other
store's mapping is reported as the reason the master path is blocked, never
borrowed.

The master B record is what live EDI would emit under that resolution: the
invoice's own description (the EDI generator is unchanged), the resolved
units. The normalized product name is reported beside it, for display only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from app.models.product_master import STATE_APPROVED
from app.services.export_service import _pdi_detail_line, normalize_item_code, pdi_items
from app.services.product_master.commercial_resolution import (
    PATH_CONFLICT,
    PATH_LEGACY_FALLBACK,
    Scope,
    ScopedMapping,
    choose_product_name,
    resolve_code,
)
from app.services.product_master.display_name import formatting_key
from app.services.product_master.shadow_edi import compare_line_fields

# Legacy B record vs master B record, per line.
LINE_MATCH = "MATCH"
LINE_EXPECTED_DIFFERENCE = "EXPECTED_DIFFERENCE"   # description only: PDI matches on the UPC
LINE_BLOCKED = "BLOCKED"                           # one path cannot produce the line
LINE_UNSAFE = "UNSAFE"                             # item code, cost, multiplier or quantity differ

# Nomenclature, per line. B (canonical) is compared with C (PDI); A is
# reported against B separately (invoice_vs_master).
NOM_EXACT = "EXACT_MATCH"
NOM_NORMALIZED = "NORMALIZED_MATCH"
NOM_DIFFERENT = "DIFFERENT_SOURCE_NOMENCLATURE_BUT_SAME_PRODUCT"
NOM_MASTER_MISSING = "PRODUCT_MASTER_DESCRIPTION_MISSING"
NOM_PDI_MISSING = "PDI_DESCRIPTION_MISSING"
NOM_IDENTITY_CONFLICT = "TRUE_IDENTITY_CONFLICT"
# Before any description can be compared, the line must reach one product.
NOM_NO_IDENTIFIER = "NO_IDENTIFIER"
NOM_NOT_IN_MASTER = "NOT_IN_PRODUCT_MASTER"

# B-record field positions, as export_service's layout map states them.
B_FIELDS = {
    "item_code": (1, 12), "description": (12, 37), "case_cost_cents": (43, 49),
    "constant": (49, 53), "units_per_case": (53, 57), "sign": (57, 58),
    "quantity": (58, 62), "srp_tail": (62, 70),
}


@dataclass(frozen=True)
class LegacyMapping:
    units_per_case: int
    description: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class MasterProductRef:
    product_id: str
    canonical_key: str
    canonical_upc: str | None
    canonical_description: str | None          # what B is; None when the master holds none
    canonical_description_source: str | None = None


@dataclass(frozen=True)
class CommercialMappingRef:
    mapping_id: str
    product_id: str
    store_id: str
    store_label: str
    store_kind: str
    approval_state: str
    commercial_unit_basis: str
    units_accounted_for: int | None
    pdi_item_code: str


@dataclass
class TraceInputs:
    """Everything one invoice's trace reads. `legacy` is this invoice's store only;
    `products` and `mappings` hold every match for a code, in any store."""

    store_id: str | None
    store_label: str | None
    store_kind: str | None
    legacy: Mapping[str, LegacyMapping] = field(default_factory=dict)
    global_holder_id: str | None = None          # the Item Sales location the global mappings are held under
    global_scope_label: str | None = None
    products: Mapping[str, list[MasterProductRef]] = field(default_factory=dict)
    mappings: Mapping[str, list[CommercialMappingRef]] = field(default_factory=dict)
    pdi_descriptions: Mapping[str, str] = field(default_factory=dict)


@dataclass
class LineTrace:
    invoice_number: str | None
    line_number: int
    invoice_description: str | None
    printed_identifier: str | None
    normalized_identifier: str | None
    store_label: str | None
    store_kind: str | None
    legacy_units_per_case: int | None
    legacy_mapping_description: str | None
    unit_cost: str | None
    master_product_key: str | None
    master_canonical_upc: str | None
    canonical_description: str | None
    canonical_description_source: str | None
    pdi_description: str | None
    mapping_id: str | None
    mapping_state: str | None
    commercial_unit_basis: str | None
    units_accounted_for: int | None
    mapping_store_label: str | None
    master_pdi_item_code: str | None
    master_description: str | None
    legacy_b_record: str | None
    master_b_record: str | None
    master_b_fields: dict[str, str] | None
    resolution_path: str | None = None
    resolution_scope: str | None = None
    resolution_mapping_id: str | None = None
    legacy_fallback_used: bool = False
    normalized_description: str | None = None
    normalized_description_source: str | None = None
    resolved_b_record: str | None = None            # what live EDI emits for this line under the resolution
    differences: list[dict] = field(default_factory=list)
    comparison: str = LINE_BLOCKED
    blocked_reasons: list[str] = field(default_factory=list)
    nomenclature: str = NOM_NO_IDENTIFIER
    invoice_vs_master: str | None = None


def b_fields(record: str) -> dict[str, str]:
    """A B record cut into its fields by the confirmed layout."""
    return {name: record[start:end] for name, (start, end) in B_FIELDS.items()}


def wording_relation(left: str | None, right: str | None) -> str | None:
    """How two descriptions of the same product relate. None when either is missing."""
    if not left or not right:
        return None
    if left.strip() == right.strip():
        return NOM_EXACT
    if formatting_key(left) == formatting_key(right):
        return NOM_NORMALIZED
    return NOM_DIFFERENT


def _nomenclature(code: str | None, products: list[MasterProductRef], pdi: str | None) -> str:
    if code is None:
        return NOM_NO_IDENTIFIER
    if not products:
        return NOM_NOT_IN_MASTER
    if len(products) > 1:
        return NOM_IDENTITY_CONFLICT
    canonical = products[0].canonical_description
    if not canonical:
        return NOM_MASTER_MISSING
    if not pdi:
        return NOM_PDI_MISSING
    return wording_relation(canonical, pdi) or NOM_PDI_MISSING


def _build_line(item, invoice, *, product_sku, description, units: Mapping[str, int]) -> tuple[str | None, str | None]:
    """A B record from the live encoders, on a stand-in so the item itself is never touched."""
    stand_in: Any = SimpleNamespace(
        product_sku=product_sku, description=description or "",
        unit_price=item.unit_price, quantity=item.quantity,
    )
    try:
        return _pdi_detail_line(stand_in, invoice=invoice, units_by_item_code=units), None
    except ValueError as exc:
        return None, str(exc)


def _scoped(m: CommercialMappingRef) -> ScopedMapping:
    return ScopedMapping(m.mapping_id, m.product_id, m.approval_state, m.commercial_unit_basis, m.units_accounted_for)


def trace_invoice(invoice, inputs: TraceInputs) -> list[LineTrace]:
    """One LineTrace per line the live export would emit, in document order."""
    physical_scope = (Scope(inputs.store_id, inputs.store_label or inputs.store_id)
                      if inputs.store_id and inputs.store_kind == "physical" else None)
    global_scope = (Scope(inputs.global_holder_id, inputs.global_scope_label or inputs.global_holder_id)
                    if inputs.global_holder_id else None)
    traces: list[LineTrace] = []
    for line_number, item in enumerate(pdi_items(invoice), start=1):
        code = normalize_item_code(item.product_sku)
        legacy = inputs.legacy.get(code) if code else None
        products = list(inputs.products.get(code, [])) if code else []
        mappings = list(inputs.mappings.get(code, [])) if code else []
        product = products[0] if len(products) == 1 else None
        pdi = inputs.pdi_descriptions.get(code) if code else None
        in_physical = [m for m in mappings if physical_scope and m.store_id == physical_scope.store_id]
        in_global = [m for m in mappings if global_scope and m.store_id == global_scope.store_id]
        elsewhere = [m for m in mappings if m not in in_physical and m not in in_global]
        shown = next((m for m in in_physical + in_global if m.approval_state == STATE_APPROVED),
                     (in_physical + in_global)[0] if in_physical + in_global else None)

        resolution = resolve_code(
            code, physical=[_scoped(m) for m in in_physical], global_=[_scoped(m) for m in in_global],
            legacy_units=legacy.units_per_case if legacy else None,
            physical_scope=physical_scope, global_scope=global_scope,
        ) if code else None
        chosen = next((m for m in mappings if resolution and m.mapping_id == resolution.mapping_id), None)
        reported = chosen or shown   # the mapping that applied, else the one this scope holds
        name, name_source = choose_product_name(
            item.description, product.canonical_description if product else None, None)

        trace = LineTrace(
            invoice_number=invoice.invoice_number, line_number=line_number,
            invoice_description=item.description, printed_identifier=item.product_sku,
            normalized_identifier=code, store_label=inputs.store_label, store_kind=inputs.store_kind,
            legacy_units_per_case=legacy.units_per_case if legacy else None,
            legacy_mapping_description=legacy.description if legacy else None,
            unit_cost=None if item.unit_price is None else str(item.unit_price),
            master_product_key=product.canonical_key if product else None,
            master_canonical_upc=product.canonical_upc if product else None,
            canonical_description=product.canonical_description if product else None,
            canonical_description_source=product.canonical_description_source if product else None,
            pdi_description=pdi,
            mapping_id=reported.mapping_id if reported else None,
            mapping_state=reported.approval_state if reported else None,
            commercial_unit_basis=chosen.commercial_unit_basis if chosen else None,
            units_accounted_for=chosen.units_accounted_for if chosen else None,
            mapping_store_label=chosen.store_label if chosen else None,
            master_pdi_item_code=chosen.pdi_item_code if chosen else None,
            master_description=None, legacy_b_record=None, master_b_record=None, master_b_fields=None,
            resolution_path=resolution.path if resolution else None,
            resolution_scope=resolution.scope_label if resolution else None,
            resolution_mapping_id=resolution.mapping_id if resolution else None,
            legacy_fallback_used=bool(resolution and resolution.path == PATH_LEGACY_FALLBACK),
            normalized_description=name, normalized_description_source=name_source,
        )
        trace.nomenclature = _nomenclature(code, products, pdi)
        trace.invoice_vs_master = wording_relation(item.description, trace.canonical_description)

        # Legacy path: exactly what the live export emitted before Product Master resolution.
        if code is None:
            trace.legacy_b_record, _ = _build_line(item, invoice, product_sku=item.product_sku,
                                                    description=item.description, units={})
        elif legacy is not None:
            trace.legacy_b_record, error = _build_line(
                item, invoice, product_sku=item.product_sku, description=item.description,
                units={code: legacy.units_per_case})
            if error:
                trace.blocked_reasons.append(f"legacy line cannot be built: {error}")
        else:
            trace.blocked_reasons.append("legacy: no product_case_mapping for this code in the invoice's store")

        # Master path: the live resolution, only when it is Product Master authoritative.
        if code is None or resolution is None:
            trace.blocked_reasons.append("master: no item code, so no Product Master identity can apply")
        elif len(products) > 1:
            trace.blocked_reasons.append(f"master: {len(products)} master products share this code")
        elif not resolution.product_master_authoritative:
            reasons = list(resolution.notes) or [f"master: {resolution.path}"]
            approved_elsewhere = [m for m in elsewhere if m.approval_state == STATE_APPROVED]
            if approved_elsewhere and not (in_physical or in_global):
                where = ", ".join(sorted({f"{m.store_label} ({m.store_kind})" for m in approved_elsewhere}))
                reasons = [f"master: approved mapping exists only in another store: {where}"]
            elif not mappings:
                reasons = ["master: code is not in the Product Master" if not products
                           else "master: product has no commercial mapping"]
            trace.blocked_reasons.extend(r if r.startswith("master:") else f"master: {r}" for r in reasons)
        else:
            assert resolution.units_per_case is not None
            trace.master_description = item.description
            trace.master_b_record, error = _build_line(
                item, invoice, product_sku=item.product_sku, description=item.description,
                units={code: resolution.units_per_case})
            if trace.master_b_record is None:
                trace.blocked_reasons.append(f"master line cannot be built: {error}")
            else:
                trace.master_b_fields = b_fields(trace.master_b_record)

        # What live EDI emits for this line once the resolution is on.
        if resolution is not None and resolution.units_per_case is not None and resolution.path != PATH_CONFLICT:
            trace.resolved_b_record = (trace.master_b_record if resolution.product_master_authoritative
                                       else trace.legacy_b_record)
        elif code is None:
            trace.resolved_b_record = trace.legacy_b_record

        if trace.legacy_b_record and trace.master_b_record and not trace.blocked_reasons:
            trace.differences = compare_line_fields(trace.legacy_b_record, trace.master_b_record)
            if not trace.differences:
                trace.comparison = LINE_MATCH
            elif any(d["kind"] == "unsafe" for d in trace.differences):
                trace.comparison = LINE_UNSAFE
            else:
                trace.comparison = LINE_EXPECTED_DIFFERENCE
        else:
            trace.comparison = LINE_BLOCKED
        traces.append(trace)
    return traces
