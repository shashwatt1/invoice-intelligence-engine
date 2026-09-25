"""
Commercial-unit decisions — app/services/product_master/commercial.py

Decides what PDI would multiply a product's Item Retail by, for one store,
from the evidence the reference corpus actually contains — and refuses to
decide when the evidence does not support one answer.

The field being decided is not a count of what is in the box. PDI computes
**Case Retail = Item Retail x units_accounted_for**, so the number answers
"how many sellable units does this case account for", which depends on what
the PDI item's Item Retail prices. A case of MICHELOB ULTRA C-18 12OZ
physically holds 18 cans and its PDI item accounts for 1. Both facts are
true and they live in different tables.

Admissible evidence, in order:

  1. An explicit distributor statement of sellable units per case — an
     `items/case` column, or the divisor in the sheet's own unit-cost
     formula (`L190 = I190/1`). These are the same assertion written two
     ways, and they are the only evidence that speaks to commercial units
     directly.
  2. A governed mapping that already exists for the product, used ONLY to
     dissent. Agreement corroborates; disagreement withholds the value.

Inadmissible, explicitly:

  * physical pack composition — 18 cans in a pack says nothing about
    whether the PDI item prices the pack or the can;
  * package notation and description strings — "C-18", "24/12OZ",
    "18/12OZ" are packaging facts, never multipliers;
  * frequency — more source rows saying 12 does not outvote one saying 24.

When evidence is insufficient or contradictory the result carries no
number. That is the point: a wrong multiplier silently corrupts Case
Retail in PDI, so an unresolved mapping is safer than a guessed one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.models.product_master import (
    COMMERCIAL_CASE_IS_SELLING_UNIT,
    COMMERCIAL_CONFLICT,
    COMMERCIAL_UNIT_IS_SELLING_UNIT,
    COMMERCIAL_UNKNOWN,
    COST_CONFLICTING_SOURCES,
    COST_DISTRIBUTOR_CASE_PRICE,
    COST_UNRESOLVED,
    MAX_UNITS_ACCOUNTED_FOR,
    MIN_UNITS_ACCOUNTED_FOR,
    STATE_REVIEW_REQUIRED,
)


@dataclass
class CommercialDecision:
    """
    One store's commercial reading of one product, with the reason.

    `units_accounted_for` is None whenever the basis is UNKNOWN or
    CONFLICT. Nothing downstream should have to infer that from the basis.
    """

    units_accounted_for: int | None
    commercial_unit_basis: str
    # Always REVIEW_REQUIRED in this phase: structural validity is not
    # approval, and nothing here may become authoritative for EDI.
    approval_state: str = STATE_REVIEW_REQUIRED
    notes: str = ""
    evidence: dict = field(default_factory=dict)


def _as_units(value) -> int | None:
    try:
        number = int(Decimal(str(value)))
    except (TypeError, ValueError, InvalidOperation):
        return None
    if MIN_UNITS_ACCOUNTED_FOR <= number <= MAX_UNITS_ACCOUNTED_FOR:
        return number
    return None


def decide_commercial_unit(
    source_statements: list[dict],
    *,
    governed_units: set[int] | None = None,
) -> CommercialDecision:
    """
    Decide the multiplier for one (product, store) pair.

    `source_statements` are the distributor rows that stated sellable units
    per case, each with its own provenance. `governed_units` are the values
    an existing `product_case_mappings` row already holds for this product,
    in any store — consulted only to withhold a value, never to supply one.
    """
    observed: dict[int, list[dict]] = {}
    for statement in source_statements:
        units = _as_units(statement.get("units_accounted_for"))
        if units is None:
            continue
        observed.setdefault(units, []).append(statement)

    evidence: dict = {
        "source_statements": [
            {
                "units": _as_units(s.get("units_accounted_for")),
                "statement": (s.get("evidence") or {}).get("statement")
                if isinstance(s.get("evidence"), dict) else s.get("evidence"),
                "source_file": s.get("source_file"),
                "source_sheet": s.get("source_sheet"),
                "source_row": s.get("source_row"),
            }
            for s in source_statements
        ],
        "governed_units_observed": sorted(governed_units) if governed_units else [],
        "inadmissible_evidence": [
            "physical pack composition", "package notation", "description text", "frequency",
        ],
    }

    if not observed:
        return CommercialDecision(
            None, COMMERCIAL_UNKNOWN,
            notes="No distributor stated sellable units per case for this product.",
            evidence=evidence,
        )

    if len(observed) > 1:
        return CommercialDecision(
            None, COMMERCIAL_CONFLICT,
            notes=(
                "Distributor sources state incompatible sellable-unit counts "
                f"({', '.join(str(u) for u in sorted(observed))}); no value was chosen. "
                "Frequency is not a tiebreak."
            ),
            evidence=evidence,
        )

    units = next(iter(observed))

    # A governed mapping that disagrees does not prove the corpus wrong — it
    # proves the commercial question for this product is open. An open
    # question must not be seeded with a number.
    if governed_units and governed_units != {units}:
        return CommercialDecision(
            None, COMMERCIAL_CONFLICT,
            notes=(
                f"Reference data states {units}, but an existing governed mapping holds "
                f"{sorted(governed_units)}. The disagreement is unresolved, so no "
                "multiplier was recorded."
            ),
            evidence=evidence,
        )

    basis = (
        COMMERCIAL_CASE_IS_SELLING_UNIT if units == 1 else COMMERCIAL_UNIT_IS_SELLING_UNIT
    )
    corroborated = bool(governed_units) and governed_units == {units}
    note = (
        "The case accounts for one sellable unit: its unit cost equals its case price."
        if units == 1 else
        f"The case breaks into {units} sellable units, so Item Retail prices one of them."
    )
    if corroborated:
        note += " An existing governed mapping independently agrees."
    return CommercialDecision(units, basis, notes=note, evidence=evidence)


def decide_case_cost(source_statements: list[dict]) -> tuple[Decimal | None, str]:
    """
    The case cost and what it measures.

    Every source that states sellable units also prints a case/promo price
    in its own case-price column, so that is the one meaning established
    here. Disagreeing sources yield no value rather than an averaged or
    first-seen one.
    """
    values: set[Decimal] = set()
    for statement in source_statements:
        raw = statement.get("case_cost")
        if raw in (None, "", "None"):
            continue
        try:
            values.add(Decimal(str(raw)))
        except InvalidOperation:
            continue
    if not values:
        return None, COST_UNRESOLVED
    if len(values) > 1:
        return None, COST_CONFLICTING_SOURCES
    return next(iter(values)), COST_DISTRIBUTOR_CASE_PRICE
