"""
Product display name — app/services/product_master/display_name.py

What a person reads to recognise a master product: in the review queue,
the evidence panel and the human review package. One function decides it,
so every surface shows the same thing for the same product.

A display name is a label, never an identity. Nothing here is read by
identity resolution, deduplication, matching, the commercial decision or
the seed; the product is whatever its canonical identifier says it is.

The rules, in order (docs/product-master-description-policy.md §6):

  1. CANONICAL — the product has a canonical description (policy Tier 1).
     It is the name.

  2. SOURCE — no canonical description, and the product's source
     descriptions are effectively one wording. That wording is shown,
     labelled source-derived, with where it came from.

  3. AMBIGUOUS_SOURCE — the source descriptions differ materially. No
     wording is chosen: the name is "Multiple source names" and every
     distinct wording is returned with its provenance, so a reviewer sees
     them all. A different flavour, size, pack or variety pack is exactly
     what makes wordings differ, and picking one would name a product the
     evidence does not settle. This includes the commercial CONFLICT rows,
     whose disputed pack sizes appear in their names.

  4. UNAVAILABLE — no description at all. Nothing is invented.

Which descriptions are compared follows only what the policy states:
distributor product sheets are the strongest reference (§4 Tier 1), and
Item Sales POS descriptions are transaction shorthand (§3). So product
sheet wordings are compared if the product has any; otherwise every other
distributor price-sheet wording (Monarch, Zink, ...) together, with no
precedence among them — the policy states none; Item Sales only when
nothing else describes the product.

Two wordings are the same wording when they differ only in formatting:
letter case, spacing, or punctuation other than the `/` and `.` that carry
pack and size notation ("18/12", "19.2"), or Zink's documented doubled
brand ("MICHELOB ULTRA MICHELOB ULTRA"). Any difference in words or
numbers is material — "FOEDER FIEND" and "FOEDER FIEND MANGO" are two
products — so no word is ever treated as harmless.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.models.product_master import DESC_CANONICAL
from app.services.product_master.descriptions import CANONICAL_SOURCE_SHEETS

NAME_CANONICAL = "CANONICAL"
NAME_SOURCE = "SOURCE"
NAME_AMBIGUOUS = "AMBIGUOUS_SOURCE"
NAME_UNAVAILABLE = "UNAVAILABLE"

AMBIGUOUS_LABEL = "Multiple source names"
UNAVAILABLE_LABEL = "Product name unavailable"

# The source classes the policy distinguishes, strongest first. Within a
# class there is no ranking.
CLASS_PRODUCT_SHEET = "distributor product sheet"
CLASS_PRICE_SHEET = "distributor price sheet"
CLASS_POS = "Item Sales POS description"
_CLASS_ORDER = (CLASS_PRODUCT_SHEET, CLASS_PRICE_SHEET, CLASS_POS)


@dataclass(frozen=True)
class SourceWording:
    """One distinct wording, and every row that carried it."""

    description: str
    source_class: str
    references: tuple[str, ...]      # "Monarch Package row 1202", ...


@dataclass(frozen=True)
class DisplayName:
    """What to show for a product, and exactly where it came from."""

    name: str | None
    basis: str                        # CANONICAL | SOURCE | AMBIGUOUS_SOURCE | UNAVAILABLE
    source_class: str | None = None   # which policy source class was compared
    source_system: str | None = None
    source_file: str | None = None
    source_sheet: str | None = None
    source_row: int | None = None
    # AMBIGUOUS_SOURCE: every distinct wording. SOURCE: the single wording.
    wordings: tuple[SourceWording, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        if self.basis == NAME_AMBIGUOUS:
            return AMBIGUOUS_LABEL
        return self.name if self.name else UNAVAILABLE_LABEL

    @property
    def variant_count(self) -> int:
        return len(self.wordings) if self.basis == NAME_AMBIGUOUS else 0

    @property
    def source_reference(self) -> str | None:
        """'Monarch Frontline row 12' — where to find the shown wording."""
        return _reference(self.source_sheet, self.source_file, self.source_row)


def _field(row: Any, name: str):
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _reference(sheet, file, row) -> str | None:
    where = sheet or file
    if not where:
        return None
    return f"{where} row {row}" if row is not None else str(where)


def source_class(source_sheet: str | None, source_system: str | None = None) -> str:
    """Which of the policy's source classes a description row belongs to."""
    sheet = source_sheet or ""
    if sheet in CANONICAL_SOURCE_SHEETS:
        return CLASS_PRODUCT_SHEET
    if source_system == "item_sales_summary" or sheet == "data":
        return CLASS_POS
    return CLASS_PRICE_SHEET


def formatting_key(description: str) -> str:
    """
    A wording with formatting removed, for deciding whether two are the same.

    Keeps every word and number, and the `/` and `.` of pack and size
    notation. Collapses Zink's doubled brand-and-name ("X Y X Y" -> "X Y").
    """
    text = re.sub(r"[^A-Z0-9./]+", " ", description.upper())
    tokens = text.split()
    half = len(tokens) // 2
    if half and len(tokens) % 2 == 0 and tokens[:half] == tokens[half:]:
        tokens = tokens[:half]
    return " ".join(tokens)


def _is_doubled(description: str) -> bool:
    tokens = re.sub(r"[^A-Z0-9./]+", " ", description.upper()).split()
    half = len(tokens) // 2
    return bool(half) and len(tokens) % 2 == 0 and tokens[:half] == tokens[half:]


def _representative_key(row: Any) -> tuple:
    # Which spelling of one wording to display. Cosmetic only — every member
    # already says the same thing — and deterministic: an undoubled spelling
    # first, then plain ordering. Never frequency, length or recency.
    text = str(_field(row, "description")).strip()
    return (_is_doubled(text), text.upper(), text,
            str(_field(row, "source_file") or ""), str(_field(row, "source_sheet") or ""),
            _field(row, "source_row") if _field(row, "source_row") is not None else -1)


def _wordings(rows: list, cls: str) -> tuple[SourceWording, ...]:
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(formatting_key(str(_field(row, "description"))), []).append(row)
    wordings = []
    for members in groups.values():
        members.sort(key=_representative_key)
        references = sorted({
            ref for ref in (_reference(_field(m, "source_sheet"), _field(m, "source_file"),
                                       _field(m, "source_row")) for m in members) if ref
        })
        wordings.append(SourceWording(
            description=str(_field(members[0], "description")).strip(),
            source_class=cls, references=tuple(references),
        ))
    return tuple(sorted(wordings, key=lambda w: (w.description.upper(), w.references)))


def resolve_display_name(descriptions: Iterable[Any]) -> DisplayName:
    """
    Decide what to show for one product from its description rows.

    `descriptions` are master_product_descriptions rows (models or dicts)
    carrying role, description, source_system, source_file, source_sheet
    and source_row.
    """
    rows = [d for d in descriptions if str(_field(d, "description") or "").strip()]

    canonical = sorted((d for d in rows if _field(d, "role") == DESC_CANONICAL),
                       key=_representative_key)
    if canonical:
        chosen = canonical[0]
        return DisplayName(
            name=str(_field(chosen, "description")).strip(), basis=NAME_CANONICAL,
            source_system=_field(chosen, "source_system"),
            source_file=_field(chosen, "source_file"),
            source_sheet=_field(chosen, "source_sheet"), source_row=_field(chosen, "source_row"),
        )

    source = [d for d in rows if _field(d, "role") != DESC_CANONICAL]
    if not source:
        return DisplayName(name=None, basis=NAME_UNAVAILABLE)

    classes = {id(d): source_class(_field(d, "source_sheet"), _field(d, "source_system"))
               for d in source}
    compared_class = min(classes.values(), key=_CLASS_ORDER.index)
    compared = [d for d in source if classes[id(d)] == compared_class]
    wordings = _wordings(compared, compared_class)

    if len(wordings) > 1:
        return DisplayName(name=None, basis=NAME_AMBIGUOUS, source_class=compared_class,
                           wordings=wordings)

    chosen = sorted(compared, key=_representative_key)[0]
    return DisplayName(
        name=wordings[0].description, basis=NAME_SOURCE, source_class=compared_class,
        source_system=_field(chosen, "source_system"), source_file=_field(chosen, "source_file"),
        source_sheet=_field(chosen, "source_sheet"), source_row=_field(chosen, "source_row"),
        wordings=wordings,
    )
