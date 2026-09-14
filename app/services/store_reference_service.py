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
EVIDENCE_RETAIL = "reference_retail"       # the store's own selling price  -> reference_derived

# Retail-margin calibration. Measured on the 26 Testani products whose
# units-per-case were already approved on stronger evidence, using the
# store's own Avg Price from the July 2024 – July 2026 export:
#
#     margin = 1 - (case_cost / units) / avg_price
#     min 14.8%   p25 21.7%   median 25.7%   p75 29.5%   max 36.8%
#
# The band is the observed range, not a guess. A product whose margin at
# exactly one plausible pack lands inside it is STRONG evidence; one
# whose only plausible pack lands just below the floor (the five 30-packs
# sit at 11-12%, as thin as the approved 18-packs at 14.8%) is SUPPORTED
# but flagged for the reviewer; more than one pack inside the band is
# ambiguous and not proposed.
RETAIL_MARGIN_LOW = 0.148
RETAIL_MARGIN_HIGH = 0.368
RETAIL_MARGIN_FLOOR_SLACK = 0.05      # how far below the floor "supported" may reach
RETAIL_MARGIN_ABSURD = 0.50           # above this no honest pack reading exists


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


_PRIORITY = {EVIDENCE_EXPLICIT: 0, EVIDENCE_PACKAGE: 1, EVIDENCE_RATIO: 2, EVIDENCE_RETAIL: 3}


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
        elif row.unit_retail is not None:
            # An Item Sales period row: no pack information, but the
            # store's own selling price says what the sellable unit is.
            units, strength, detail = derive_units_from_retail(invoice_case_cost, row.unit_retail)
            if units is not None:
                found.append(UnitsEvidence(units, EVIDENCE_RETAIL,
                                           row.source_file, row.source_sheet, row.source_row,
                                           {**base, **detail, "strength": strength,
                                            "reference_description": None}))
    return sorted(found, key=lambda e: _PRIORITY[e.kind])


def _f(value):
    return None if value is None else float(value)


def derive_units_from_retail(
    invoice_case_cost: Decimal | None, unit_retail: Decimal | None
) -> tuple[int | None, str | None, dict[str, Any]]:
    """
    A units-per-case reading from what the store's till says the
    scanned unit sells for.

    For each plausible pack N, the implied margin is
    1 - (case_cost / N) / retail. Returns (units, strength, detail) with
    strength "strong" when exactly one N sits in the calibrated band,
    "supported" when nothing is in band but exactly one N is within the
    floor slack and every other N is absurd, else (None, None, detail).
    The detail carries every N considered so the reviewer sees why.
    """
    detail: dict[str, Any] = {"kind": "retail_margin", "unit_retail": _f(unit_retail),
                              "invoice_case_cost": _f(invoice_case_cost)}
    if not invoice_case_cost or not unit_retail or unit_retail <= 0 or invoice_case_cost <= 0:
        return None, None, detail
    cost, retail = float(invoice_case_cost), float(unit_retail)
    margins = {n: 1 - (cost / n) / retail for n in PLAUSIBLE_CASE_PACKS}
    detail["margin_by_units"] = {n: round(m, 4) for n, m in margins.items()}
    in_band = [n for n, m in margins.items() if RETAIL_MARGIN_LOW <= m <= RETAIL_MARGIN_HIGH]
    near = [n for n, m in margins.items()
            if RETAIL_MARGIN_LOW - RETAIL_MARGIN_FLOOR_SLACK <= m < RETAIL_MARGIN_LOW]
    plausible_at_all = [n for n, m in margins.items() if 0 <= m <= RETAIL_MARGIN_ABSURD]
    detail.update({"in_band": in_band, "near_floor": near,
                   "band": [RETAIL_MARGIN_LOW, RETAIL_MARGIN_HIGH]})
    if len(in_band) == 1:
        detail["margin"] = round(margins[in_band[0]], 4)
        return in_band[0], "strong", detail
    if not in_band and len(near) == 1 and plausible_at_all == near:
        detail["margin"] = round(margins[near[0]], 4)
        detail["note"] = ("margin below the calibrated floor but every other pack reading "
                          "implies an absurd margin; thin, like the approved 18-packs")
        return near[0], "supported", detail
    if len(in_band) > 1:
        detail["note"] = f"ambiguous: {in_band} all inside the band"
    return None, None, detail


def _match(
    row: StoreProductReference | None,
    invoice_case_cost: Decimal | None,
    pricing_rows,
    item_code: str,
    document_suggestion: int | None = None,
) -> ReferenceMatch:
    avg_cost = row.avg_cost if row else None
    ratio_candidate, ratio = derive_units_per_case(invoice_case_cost, avg_cost)

    evidence = _evidence_from_pricing(pricing_rows, invoice_case_cost)
    # Retail-margin ambiguity resolved by an independent source: if the
    # store's price leaves two packs in band and the DOCUMENT names one of
    # them unambiguously, the two agree and that is stronger than either
    # alone. Recorded as retail evidence with the corroboration spelled
    # out, so the reviewer can see both halves.
    if document_suggestion is not None:
        for prow in pricing_rows:
            if prow.unit_retail is None or prow.items_per_case_stated or prow.items_per_case_derived:
                continue
            _, _, detail = derive_units_from_retail(invoice_case_cost, prow.unit_retail)
            in_band = detail.get("in_band") or []
            if len(in_band) > 1 and document_suggestion in in_band:
                evidence.append(UnitsEvidence(
                    document_suggestion, EVIDENCE_RETAIL,
                    prow.source_file, prow.source_sheet, prow.source_row,
                    {**detail, "strength": "strong",
                     "margin": detail["margin_by_units"].get(document_suggestion),
                     "effective_from": prow.effective_from.isoformat() if prow.effective_from else None,
                     "distributor": prow.distributor, "pricing_basis": prow.pricing_basis,
                     "corroborated_by_document": True,
                     "note": f"retail alone allows {in_band}; the invoice's own pack notation "
                             f"names {document_suggestion}, and the two agree"},
                ))
                break
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

    # The document's own unambiguous pack reading, used only to break a
    # retail tie — never as evidence on its own here.
    from app.services.export_service import suggest_units_per_case
    doc_hint: dict[str, int | None] = {}
    for item in invoice.items:
        code = normalize_item_code(item.product_sku)
        if code and code not in doc_hint:
            units, source = suggest_units_per_case(item.pack_size, item.description)
            doc_hint[code] = units if source in ("pack_size", "description") else None

    matches: dict[str, ReferenceMatch] = {}
    for code in set(sales) | set(pricing):
        match = _match(sales.get(code), costs.get(code), pricing.get(code, []), code,
                       document_suggestion=doc_hint.get(code))
        if match.reference_description is None and code in identity:
            match = ReferenceMatch(**{**match.__dict__,
                                      "reference_description": identity[code].description})
        matches[code] = match
    return matches
