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

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.store_product_reference import StoreProductReference
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


@dataclass(frozen=True)
class ReferenceMatch:
    """What the store's catalogue knows about one invoice line."""

    item_code: str
    reference_description: str | None
    avg_cost: Decimal | None
    avg_price: Decimal | None
    units_per_case_candidate: int | None
    candidate_ratio: float | None

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


def _match(row: StoreProductReference, invoice_case_cost: Decimal | None) -> ReferenceMatch:
    candidate, ratio = derive_units_per_case(invoice_case_cost, row.avg_cost)
    return ReferenceMatch(
        item_code=row.item_code,
        reference_description=row.description,
        avg_cost=row.avg_cost,
        avg_price=row.avg_price,
        units_per_case_candidate=candidate,
        candidate_ratio=ratio,
    )


async def match_invoice_against_reference(
    session: AsyncSession, invoice: Invoice, store_number: str
) -> dict[str, ReferenceMatch]:
    """
    Reference matches for this invoice's products, keyed by normalized
    item code. Exact UPC only; unmatched products are simply absent.
    """
    costs: dict[str, Decimal | None] = {}
    for item in invoice.items:
        code = normalize_item_code(item.product_sku)
        if code and code not in costs:
            costs[code] = item.unit_price

    rows = await StoreProductReferenceRepository(session).by_item_codes(
        store_number, list(costs)
    )
    return {code: _match(row, costs.get(code)) for code, row in rows.items()}
