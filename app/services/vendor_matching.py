"""
Vendor name matching — app/services/vendor_matching.py

Pure comparison of vendor names, used for two things: refusing to create a vendor that already exists under another
spelling, and judging whether the vendor an operator chose agrees with the vendor the document prints.

A name is reduced to its distinctive words: lower case, "&" read as "and", periods dropped so initials join ("T.J." is "tj"), other punctuation removed, and the words every
supplier carries dropped ("inc", "llc", "company", "distributing", "beverage" ...). Two names match when those words are
equal, or when one set of words is contained in the other ("PEPSICO" in "PEPSICO PEPSI BEVERAGES COMPANY"). A shared tax
id matches regardless of spelling. Nothing here is fuzzy: a name that shares no distinctive word with the other does not
match, which is what the mismatch warning exists to say.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

GENERIC_WORDS = frozenset({
    "inc", "incorporated", "llc", "l", "c", "co", "corp", "corporation", "company", "companies", "ltd", "limited", "lp",
    "llp", "the", "and", "of", "distributing", "distribution", "distributors", "distributor", "beverage", "beverages",
    "bev", "wholesale", "supply", "enterprises", "group", "foods", "food",
})
_NON_WORD = re.compile(r"[^a-z0-9]+")


def name_words(name: str | None) -> frozenset[str]:
    """The distinctive words of a vendor name (all of them if nothing distinctive is left)."""
    tokens = [t for t in _NON_WORD.split((name or "").casefold().replace(".", "").replace("&", " and ")) if t]
    joined: list[str] = []          # "T J" and "T.J." are the same initials
    run = ""
    for t in tokens:
        if len(t) == 1 and t.isalpha():
            run += t
            continue
        if run:
            joined.append(run)
            run = ""
        joined.append(t)
    if run:
        joined.append(run)
    tokens = joined
    distinctive = [t for t in tokens if t not in GENERIC_WORDS]
    return frozenset(distinctive or tokens)


def normalize_vendor_name(name: str | None) -> str:
    """A stable comparison form: the distinctive words, sorted."""
    return " ".join(sorted(name_words(name)))


def names_match(a: str | None, b: str | None) -> bool:
    wa, wb = name_words(a), name_words(b)
    if not wa or not wb:
        return False
    if wa == wb:
        return True
    smaller, larger = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return smaller <= larger and any(len(word) >= 3 for word in smaller)


def tax_ids_match(a: str | None, b: str | None) -> bool:
    clean = lambda v: re.sub(r"[^0-9a-z]", "", (v or "").casefold())  # noqa: E731
    return bool(clean(a)) and clean(a) == clean(b)


def vendor_agrees(selected_names: Iterable[str | None], extracted_name: str | None, *,
                  selected_tax_id: str | None = None, extracted_tax_id: str | None = None) -> bool | None:
    """
    Does the vendor a person chose agree with what the document printed? None when the document printed no vendor
    (nothing to compare), True on a shared tax id or a matching name, otherwise False.
    """
    if not (extracted_name or "").strip() and not (extracted_tax_id or "").strip():
        return None
    if tax_ids_match(selected_tax_id, extracted_tax_id):
        return True
    return any(names_match(name, extracted_name) for name in selected_names if name)


@dataclass(frozen=True)
class ExpectedVendor:
    """The vendor a person chose for an invoice, as the names and tax id the printed vendor is compared with."""

    names: tuple[str, ...]
    tax_id: str | None = None

    @classmethod
    def of(cls, vendor) -> ExpectedVendor | None:
        if vendor is None:
            return None
        names = tuple(dict.fromkeys(n for n in (vendor.display_name, vendor.name) if n))
        return cls(names=names, tax_id=vendor.tax_id) if names else None

