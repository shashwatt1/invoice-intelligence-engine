"""
Canonical description policy — app/services/product_master/descriptions.py

Decides whether a product has a description authoritative enough to be
canonical, per docs/product-master-description-policy.md.

The corpus contains no product-master description source at meaningful
coverage: 77.7% of products are described only by Item Sales POS shorthand,
Monarch names average 34-37 characters with pack size in a different
column, and Zink frequently doubles brand and product name. The single
source shaped like a product master — brand plus explicit package
notation, roughly the PDI field width — is the Beer Inventory product
sheet, covering 1.0% of products.

So this promotes descriptions from that source only, and leaves the rest
without a canonical description rather than inventing one. Nothing is
chosen by frequency, length, recency, order or invoice wording.
"""

from __future__ import annotations

from dataclasses import dataclass

# The PDI B-record description field. A Tier-1 candidate that does not fit
# is not truncated to qualify: the package notation is what gets cut.
PDI_DESCRIPTION_WIDTH = 25

# Purpose-built product reference sheets. Deliberately excludes Monarch
# (pack size lives in a separate column), Zink (degenerate duplication) and
# Item Sales (transaction shorthand).
CANONICAL_SOURCE_SHEETS = frozenset({"Sheet1", "Sheet2", "Sheet3"})

OUTCOME_CANONICAL = "CANONICAL_SET"
OUTCOME_NO_SANCTIONED_SOURCE = "NO_SANCTIONED_SOURCE"
OUTCOME_SOURCE_CONFLICT = "SOURCE_CONFLICT"
OUTCOME_TOO_LONG_FOR_PDI = "TOO_LONG_FOR_PDI_FIELD"
OUTCOME_NO_DESCRIPTIONS = "NO_DESCRIPTIONS"


@dataclass
class DescriptionDecision:
    """What the policy concluded for one product, and why."""

    description: str | None
    outcome: str
    source_sheet: str | None = None
    source_file: str | None = None
    source_row: int | None = None
    reason: str = ""


def decide_canonical_description(descriptions: list[dict]) -> DescriptionDecision:
    """
    Apply the policy to every description observed for one product.

    `descriptions` are rows carrying at least `description`, `source_sheet`,
    `source_file` and `source_row`.
    """
    if not descriptions:
        return DescriptionDecision(
            None, OUTCOME_NO_DESCRIPTIONS,
            reason="No source described this product.",
        )

    tier_one = [d for d in descriptions if d.get("source_sheet") in CANONICAL_SOURCE_SHEETS]
    if not tier_one:
        return DescriptionDecision(
            None, OUTCOME_NO_SANCTIONED_SOURCE,
            reason=(
                "Only transaction shorthand or price-feed wording is available; no "
                "distributor product sheet describes this product."
            ),
        )

    distinct = {d["description"].strip() for d in tier_one if d.get("description")}
    if len(distinct) > 1:
        return DescriptionDecision(
            None, OUTCOME_SOURCE_CONFLICT,
            reason=(
                "The product sheet states more than one description: "
                + "; ".join(sorted(distinct)[:3])
            ),
        )

    chosen = next(iter(distinct))
    row = next(d for d in tier_one if d["description"].strip() == chosen)
    if len(chosen) > PDI_DESCRIPTION_WIDTH:
        return DescriptionDecision(
            None, OUTCOME_TOO_LONG_FOR_PDI,
            source_sheet=row.get("source_sheet"), source_file=row.get("source_file"),
            source_row=row.get("source_row"),
            reason=(
                f"{len(chosen)} characters exceeds the {PDI_DESCRIPTION_WIDTH}-character "
                "PDI field; truncating would cut the package notation."
            ),
        )

    return DescriptionDecision(
        chosen, OUTCOME_CANONICAL,
        source_sheet=row.get("source_sheet"), source_file=row.get("source_file"),
        source_row=row.get("source_row"),
        reason=(
            "Distributor product sheet states one description, and it fits the PDI field."
        ),
    )
