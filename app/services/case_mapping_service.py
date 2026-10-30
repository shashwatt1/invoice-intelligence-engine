"""
Case Mapping Service — app/services/case_mapping_service.py

Bridges an Invoice aggregate to the UPC → units-per-case mapping table.

Kept separate from export_service so that module stays pure and
synchronous (it takes a plain dict), and separate from the repository so
the repository stays unaware of invoices. Both the export endpoint and
the invoice-detail endpoint use these helpers, so the rule they enforce
cannot drift between "can I download?" and "what does the UI show?".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.services.export_service import (
    normalize_item_code,
    pack_candidates,
    pdi_items,
    suggest_units_per_case,
)
from app.services.product_master.commercial_resolution import (
    PATH_PRODUCT_MASTER_GLOBAL,
    PATH_SHARED_UNCONFIRMED,
    CodeResolution,
    CommercialResolution,
    resolve_store_codes,
)
from app.services.store_reference_service import ReferenceMatch


class PendingProposal(Protocol):
    """The two things the review row needs from a queued proposal."""

    id: Any
    proposed_value: Any


SUGGESTION_FROM_DATABASE = "database"
# The invoice prints an explicit count and the reference derives a
# different one from a cost ratio or a retail margin. Neither is
# prefilled: both are offered and a person decides.
SUGGESTION_CONFLICT = "conflict"
# Reference evidence that is *derived* (a ratio, a margin) rather than
# stated. It may corroborate a printed count; it may never overrule one.
DERIVED_REFERENCE_SOURCES = frozenset({"reference_ratio", "reference_retail"})
# Reference-derived suggestions, strongest first. Each names how the
# number was arrived at, because a reviewer weighs a typed items/case
# cell, a decoded package string and a cost ratio very differently.
# All three are still suggestions: nothing reaches an EDI without an
# approved mapping.
SUGGESTION_FROM_REFERENCE_EXPLICIT = "reference_explicit"
SUGGESTION_FROM_REFERENCE_PACKAGE = "reference_package"
SUGGESTION_FROM_REFERENCE_RATIO = "reference_ratio"
SUGGESTION_FROM_REFERENCE_RETAIL = "reference_retail"
REFERENCE_SUGGESTION_SOURCES = frozenset({
    SUGGESTION_FROM_REFERENCE_EXPLICIT, SUGGESTION_FROM_REFERENCE_PACKAGE,
    SUGGESTION_FROM_REFERENCE_RATIO, SUGGESTION_FROM_REFERENCE_RETAIL,
})
# Kept for callers that only need "did the reference say something".
SUGGESTION_FROM_REFERENCE = SUGGESTION_FROM_REFERENCE_RATIO


@dataclass(frozen=True)
class CaseMappingStatus:
    """One line item's units-per-case state, for the review UI."""

    item_code: str | None          # normalized; None when the line has no usable code
    description: str | None
    units_per_case: int | None     # confirmed value, when one exists
    suggested_units_per_case: int | None  # from the document, needs confirmation
    suggestion_source: str | None  # where the number came from; see SUGGESTION_*
    suggestion_candidates: list[int]  # readings an ambiguous pack could support
    pack_size: str | None          # raw printed pack descriptor, shown as evidence
    mapped: bool
    # Store-catalogue evidence for this product, when it was matched by
    # exact UPC. Shown to the operator; never applied automatically.
    reference_description: str | None = None
    reference_avg_cost: float | None = None
    # A submitted-but-unreviewed value. Shown to the operator so they know
    # it is in the queue; NEVER used for the EDI — only a mapping is.
    pending_proposal_id: str | None = None
    pending_value: int | None = None
    # The line itself, so a reviewer can identify the product without leaving
    # the mapping. Source values exactly as stored on the line (invoice prices,
    # never another store's or a distributor's); nothing here is written
    # anywhere or used by the export. `original_description` is the text
    # before the first description correction, kept for traceability.
    sort_order: int | None = None
    product_code: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    line_total: float | None = None
    unit_discount: float | None = None
    original_description: str | None = None


def _num(value) -> float | None:
    return float(value) if value is not None else None


def original_description(item) -> str | None:
    """The description before its first correction, or None when it was never corrected."""
    for entry in item.correction_history or []:
        if isinstance(entry, dict) and entry.get("field") == "description":
            return entry.get("old")
    return None


async def invoice_units_by_item_code(
    session: AsyncSession, invoice: Invoice
) -> dict[str, int]:
    """
    Confirmed units-per-case for every product on this invoice, in the
    invoice's own store. Another physical store's confirmation of the same
    UPC is not consulted: the store is part of the mapping's identity.

    Resolved by commercial_resolution: legacy product_case_mappings only,
    unless Product Master resolution is switched on (see that module).
    """
    return (await invoice_commercial_resolution(session, invoice)).units


def printed_disagreement(line: CodeResolution | None, pack_size: str | None,
                         description: str | None) -> str | None:
    """
    Why a value decided for every store must not be used on this line yet:
    the store has no value of its own for the item, and the line's printed
    pack states a different count. None when it may be used.

    The C-18 shape the replay found (MICHELOB ULTRA / TWISTED TEA C-18 12OZ):
    the shared value says 1 (the case is the selling unit) while one store
    holds 18. Both readings fit the printed "C-18", so the store decides once
    — by confirming the shared value or recording its own as an override.
    """
    if line is None or line.path != PATH_PRODUCT_MASTER_GLOBAL or line.legacy_units is not None:
        return None
    units = line.units_per_case
    printed, source = suggest_units_per_case(pack_size, description)
    if source in ("pack_size", "description") and printed is not None and printed != units:
        return (f"the invoice prints {printed} per case and {line.scope_label or 'the shared value'} says {units}; "
                "this store has not decided this item — confirm the shared value or record its own")
    if source == "description_ambiguous":
        candidates = pack_candidates(description)
        if candidates and units not in candidates:
            return (f"the invoice's pack reads as {' or '.join(map(str, candidates))} and "
                    f"{line.scope_label or 'the shared value'} says {units}; confirm it for this store")
    return None


def guard_printed_packs(resolution: CommercialResolution, items) -> CommercialResolution:
    """The resolution with every code whose shared value disagrees with a printed pack held back."""
    held: dict[str, str] = {}
    for item in items:
        code = normalize_item_code(item.product_sku)
        reason = printed_disagreement(resolution.lines.get(code or ""), item.pack_size, item.description)
        if code and reason and code not in held:
            held[code] = reason
    for code, reason in held.items():
        line = resolution.lines[code]
        resolution.lines[code] = CodeResolution(
            code, PATH_SHARED_UNCONFIRMED, None, mapping_id=line.mapping_id, product_id=line.product_id,
            scope_store_id=line.scope_store_id, scope_label=line.scope_label, legacy_units=None,
            notes=(reason,) + line.notes,
        )
    return resolution


async def invoice_commercial_resolution(session: AsyncSession, invoice: Invoice) -> CommercialResolution:
    """
    Every item code on this invoice, resolved — with the path each took, and a
    shared value held back where this invoice's printed pack disagrees with it
    and the store never decided the item (guard_printed_packs).
    """
    if invoice.store_id is None:
        return CommercialResolution()               # STORE_PENDING: no store, no mappings
    codes = [
        code
        for code in (normalize_item_code(item.product_sku) for item in invoice.items)
        if code
    ]
    resolution = await resolve_store_codes(session, invoice.store_id, codes)
    return guard_printed_packs(resolution, pdi_items(invoice))


def build_case_mapping_status(
    invoice: Invoice,
    units_by_item_code: dict[str, int],
    reference_matches: dict[str, ReferenceMatch] | None = None,
    pending: dict[str, PendingProposal] | None = None,
) -> list[CaseMappingStatus]:
    """
    Per-line mapping state, in document order.

    Lines without a usable product code are reported as mapped: there is
    nothing to key a mapping on, they already export with the blank
    item-code convention, and blocking on them would make such an invoice
    permanently un-exportable.
    """
    statuses: list[CaseMappingStatus] = []
    candidates_override: list[int] | None
    for item in pdi_items(invoice):
        code = normalize_item_code(item.product_sku)
        units = units_by_item_code.get(code or "") if code else None
        suggestion, source = suggest_units_per_case(item.pack_size, item.description)
        candidates_override = None
        # Evidence precedence, strongest first: a confirmed mapping (below);
        # an explicit count printed on the invoice; explicit reference
        # evidence (a typed items/case cell, a decoded package); and only
        # then a number DERIVED from a cost ratio or a retail margin. A
        # derived number may corroborate a printed count but never overrule
        # it: on a real invoice the store's average cost turned printed
        # "C-15" cases into 16 and "C-12" into 10 and 48. When the two
        # disagree, nothing is prefilled and both readings are offered.
        reference = (reference_matches or {}).get(code or "")
        queued = (pending or {}).get(code or "")
        if reference is not None and reference.best_evidence is not None:
            evidence = reference.best_evidence
            printed = suggestion if source == "pack_size" or source == "description" else None
            if (printed is not None and evidence.kind in DERIVED_REFERENCE_SOURCES
                    and evidence.units_per_case != printed):
                suggestion, source = None, SUGGESTION_CONFLICT
                candidates_override = sorted({printed, evidence.units_per_case})
            else:
                # Explicit reference evidence, or derived evidence that agrees
                # with the printed count: the reference label carries the
                # stronger, corroborated claim, and the value is the same.
                suggestion = evidence.units_per_case
                source = evidence.kind      # reference_explicit / reference_package / _ratio / _retail
        statuses.append(
            CaseMappingStatus(
                item_code=code,
                description=item.description,
                units_per_case=units,
                suggested_units_per_case=suggestion,
                # A confirmed mapping outranks any suggestion, so report the
                # database as the source rather than whatever the document
                # happened to say — that is the value actually used.
                suggestion_source=SUGGESTION_FROM_DATABASE if units is not None else source,
                # Only populated for a structurally ambiguous description,
                # and only while the product is still unmapped: once a
                # human has decided, the candidates are history.
                suggestion_candidates=(
                    []
                    if units is not None
                    else candidates_override
                    if candidates_override is not None
                    else []
                    if source in REFERENCE_SUGGESTION_SOURCES
                    else pack_candidates(item.description)
                ),
                pack_size=item.pack_size,
                mapped=units is not None or code is None,
                reference_description=reference.reference_description if reference else None,
                reference_avg_cost=(
                    float(reference.avg_cost)
                    if reference and reference.avg_cost is not None
                    else None
                ),
                pending_proposal_id=str(queued.id) if queued else None,
                pending_value=int(queued.proposed_value) if queued else None,
                sort_order=item.sort_order,
                product_code=item.product_sku,
                quantity=_num(item.quantity),
                unit_price=_num(item.unit_price),
                line_total=_num(item.line_total),
                unit_discount=_num(item.discount),
                original_description=original_description(item),
            )
        )
    return statuses
