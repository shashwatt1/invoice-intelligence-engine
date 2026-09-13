"""
Store Reference Service — app/services/store_reference_service.py

Joins an invoice to the store's own product catalogue and turns that
into evidence a human can act on.

What this layer does:
  - identifies a product by EXACT normalized UPC;
  - proposes a units-per-case value for a human to confirm.

What it deliberately does not do:
  - match by description. In this store's export 666 descriptions map to
    more than one scan code (one maps to 26), and across 35 UPC-verified
    invoice/reference pairs the median token overlap was 0.25 with 7
    pairs sharing no tokens at all. Any threshold loose enough to catch
    the true pairs would assign one product's cost to another.
  - write anything. Nothing here confirms a mapping, changes an invoice,
    or reaches an EDI byte.

THE UNITS-PER-CASE DERIVATION
-----------------------------
The store's `avg_cost` is per SELLING unit; an invoice bills per CASE.
So for a matched product:

    units per case  ~=  invoice case cost / reference avg cost

Measured across 25 costed matches on two real invoices, that ratio landed
on a whole number 22 times exactly and never drifted more than 2.9%
(`avg_cost` is a period-weighted average, so it moves when a product's
cost changed mid-period). Snapping to a plausible case pack recovers the
rest: Red Bull at 24.74 and 11.32 resolve to 24 and 12.

It is still only a suggestion. It is offered through the same review
mechanism as every other hint, carries its own provenance so it is
distinguishable from a confirmed mapping, and a person must accept it
before it can reach the EDI.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.store_product_reference import StoreProductReference
from app.repositories.product_reference_repository import ProductReferenceRepository
from app.repositories.store_product_reference_repository import (
    StoreProductReferenceRepository,
)
from app.services.export_service import normalize_item_code

# Case packs a beverage/grocery supplier actually ships. Snapping to this
# set is what lets a drifted average still resolve correctly; without it,
# Red Bull's 24.74 would round to 25 and its 11.32 to 11.
PLAUSIBLE_CASE_PACKS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 16, 18, 20, 24, 28, 30, 36, 48)

# How far the observed ratio may sit from a plausible pack before we stay
# silent. The largest genuine drift measured on real data was 5.7% (Red
# Bull 16oz, whose cost moved mid-period), so 8% covers every observed
# case with headroom. It is deliberately not looser: the plausible packs
# thin out above 24, and a wider band would let a ratio of 40 snap onto
# 36 and present an 11% guess as evidence.
MAX_PACK_RELATIVE_ERROR = 0.08


# How a reference-derived units-per-case candidate was arrived at, in
# descending order of trust. Each maps to a proposal source.
EVIDENCE_EXPLICIT = "reference_explicit"   # a typed items/case cell        -> beer_inventory_explicit
EVIDENCE_PACKAGE = "reference_package"     # two-fraction package string    -> beer_inventory_package
EVIDENCE_RATIO = "reference_ratio"         # a source's own case/unit cost  -> reference_derived


@dataclass(frozen=True)
class UnitsEvidence:
    """One piece of evidence for units-per-case, with where it came from."""

    units_per_case: int
    kind: str                       # EVIDENCE_*
    source_file: str | None
    source_sheet: str | None
    source_row: int | None
    detail: dict[str, Any]          # what the reviewer should see: package, costs, ratio…


@dataclass(frozen=True)
class ReferenceMatch:
    """What the store's catalogue knows about one invoice line."""

    item_code: str
    reference_description: str | None
    avg_cost: Decimal | None
    avg_price: Decimal | None
    units_per_case_candidate: int | None
    candidate_ratio: float | None
    # Best units-per-case evidence across every source, plus everything
    # else that was found, so a reviewer can see agreement or dissent.
    best_evidence: UnitsEvidence | None = None
    all_evidence: tuple[UnitsEvidence, ...] = ()

    @property
    def has_cost(self) -> bool:
        return self.avg_cost is not None


def derive_units_per_case(
    invoice_case_cost: Decimal | None, avg_cost: Decimal | None
) -> tuple[int | None, float | None]:
    """
    A units-per-case candidate from the store's own cost basis.

    Returns (candidate, raw_ratio). Both are None when the inputs cannot
    support a candidate — no cost on either side, or a ratio too far from
    any plausible case pack to be worth showing.
    """
    if invoice_case_cost is None or avg_cost is None or avg_cost <= 0:
        return None, None
    if invoice_case_cost <= 0:
        return None, None

    ratio = float(invoice_case_cost) / float(avg_cost)
    if ratio < 0.5:
        return None, ratio

    nearest = min(PLAUSIBLE_CASE_PACKS, key=lambda pack: abs(pack - ratio))
    if abs(nearest - ratio) / nearest > MAX_PACK_RELATIVE_ERROR:
        return None, ratio
    return nearest, ratio


_PRIORITY = {EVIDENCE_EXPLICIT: 0, EVIDENCE_PACKAGE: 1, EVIDENCE_RATIO: 2}


def _evidence_from_pricing(pricing_rows, invoice_case_cost: Decimal | None) -> list[UnitsEvidence]:
    """
    Every units-per-case reading the Beer Inventory rows support.

    Ordered explicit > package > ratio. A row flagged conflicted never
    reaches here — the repository excludes it — so a reading only
    appears if the source agreed with itself.
    """
    found: list[UnitsEvidence] = []
    for row in pricing_rows:
        base = {
            "distributor": row.distributor, "pricing_basis": row.pricing_basis,
            "package": row.package, "case_cost": _f(row.case_cost), "unit_cost": _f(row.unit_cost),
            "effective_from": row.effective_from.isoformat() if row.effective_from else None,
            "invoice_case_cost": _f(invoice_case_cost),
            "case_cost_matches_invoice": (
                invoice_case_cost is not None and row.case_cost is not None
                and abs(invoice_case_cost - row.case_cost) < Decimal("0.005")
            ),
        }
        if row.items_per_case_stated is not None:
            found.append(UnitsEvidence(row.items_per_case_stated, EVIDENCE_EXPLICIT,
                                       row.source_file, row.source_sheet, row.source_row, base))
        elif row.items_per_case_derived is not None:
            kind = EVIDENCE_PACKAGE if row.items_per_case_derivation == "package" else EVIDENCE_RATIO
            found.append(UnitsEvidence(row.items_per_case_derived, kind,
                                       row.source_file, row.source_sheet, row.source_row,
                                       {**base, "derivation": row.items_per_case_derivation}))
    return sorted(found, key=lambda e: _PRIORITY[e.kind])


def _f(value):
    return None if value is None else float(value)


def _match(
    row: StoreProductReference | None,
    invoice_case_cost: Decimal | None,
    pricing_rows,
    item_code: str,
) -> ReferenceMatch:
    avg_cost = row.avg_cost if row else None
    ratio_candidate, ratio = derive_units_per_case(invoice_case_cost, avg_cost)

    evidence = _evidence_from_pricing(pricing_rows, invoice_case_cost)
    if ratio_candidate is not None:
        evidence.append(UnitsEvidence(
            ratio_candidate, EVIDENCE_RATIO, "Item_Sales_Summary", None, None,
            {"avg_cost": _f(avg_cost), "invoice_case_cost": _f(invoice_case_cost),
             "ratio": round(ratio, 4) if ratio else None},
        ))
    evidence.sort(key=lambda e: _PRIORITY[e.kind])
    best = evidence[0] if evidence else None

    return ReferenceMatch(
        item_code=item_code,
        reference_description=row.description if row else None,
        avg_cost=avg_cost,
        avg_price=row.avg_price if row else None,
        units_per_case_candidate=best.units_per_case if best else None,
        candidate_ratio=ratio,
        best_evidence=best,
        all_evidence=tuple(evidence),
    )


async def match_invoice_against_reference(
    session: AsyncSession, invoice: Invoice, store_number: str
) -> dict[str, ReferenceMatch]:
    """
    Reference matches for this invoice's products, keyed by normalized
    item code. Exact UPC only, across both the Item Sales catalogue and
    the Beer Inventory pricing rows. A product absent from both is
    simply absent.
    """
    costs: dict[str, Decimal | None] = {}
    for item in invoice.items:
        code = normalize_item_code(item.product_sku)
        if code and code not in costs:
            costs[code] = item.unit_price

    sales = await StoreProductReferenceRepository(session).by_item_codes(store_number, list(costs))
    pricing = await ProductReferenceRepository(session).pricing_for(store_number, list(costs))
    identity = await ProductReferenceRepository(session).identity_for(store_number, list(costs))

    matches: dict[str, ReferenceMatch] = {}
    for code in set(sales) | set(pricing):
        match = _match(sales.get(code), costs.get(code), pricing.get(code, []), code)
        if match.reference_description is None and code in identity:
            match = ReferenceMatch(**{**match.__dict__,
                                      "reference_description": identity[code].description})
        matches[code] = match
    return matches
