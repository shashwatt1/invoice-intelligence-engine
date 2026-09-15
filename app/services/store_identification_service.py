"""
Store identification — app/services/store_identification_service.py

Which store a document is for, from what the document says — offered
to the operator, never decided for them.

Matching is deterministic and exact, against the store master only:
  * an identifier value a source system uses for a store, printed
    verbatim on the document (a store code, a customer number once one
    is known);
  * a customer/business name attached to a store as an identifier or
    as its confirmed name, present verbatim after normalisation;
  * a street line attached to a store, present verbatim — counted only
    together with the store's postal code, because streets repeat.

Nothing is inferred from similarity, from the vendor, or from numbers
that merely resemble another invoice's. A document that matches
nothing needs a person; a document that matches two stores needs a
person; a document that matches one store still needs that person to
say yes. Evidence attached as `verified: false` (observed on documents,
not yet confirmed by anyone) counts as a candidate, and says so.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store import (
    SOURCE_DOCUMENT,
    TYPE_ADDRESS_LINE,
    TYPE_CUSTOMER_NAME,
    TYPE_POSTAL_CODE,
    Store,
    StoreIdentifier,
)
from app.repositories.store_repository import StoreRepository

# Shortest name/street we will match verbatim — shorter strings match by accident.
MIN_TEXT_MATCH = 6
MIN_CODE_MATCH = 5


@dataclass
class StoreCandidate:
    store_id: str
    label: str
    identity_status: str
    address: str | None
    # each: {"kind", "value", "source_system", "verified"}
    matched_on: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(value: str | None) -> str:
    """Uppercase, punctuation to spaces, single spaces — so 'Wolf St.' == 'WOLF ST'."""
    text = re.sub(r"[^A-Z0-9]+", " ", (value or "").upper())
    return f" {' '.join(text.split())} "


def normalize_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _contains_text(haystack: str, needle: str | None) -> bool:
    n = normalize_text(needle).strip()
    return len(n) >= MIN_TEXT_MATCH and f" {n} " in haystack


def _contains_code(text_tokens: set[str], value: str | None) -> bool:
    v = (value or "").strip()
    return len(v) >= MIN_CODE_MATCH and v in text_tokens


def match_stores(text: str, stores: list[Store]) -> list[StoreCandidate]:
    """
    Pure matching of extracted text against the store master.

    Returns one candidate per store with at least one exact hit, the
    strongest evidence listed first. Empty when nothing matches.
    """
    haystack = normalize_text(text)
    # tokens for code matching: runs of digits/letters as printed, plus digit-only forms
    raw_tokens = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]*", text or ""))
    tokens = raw_tokens | {normalize_digits(t) for t in raw_tokens}

    candidates: list[StoreCandidate] = []
    for store in stores:
        hits: list[dict[str, Any]] = []
        for ident in store.identifiers:
            verified = bool((ident.evidence or {}).get("verified", ident.source_system != SOURCE_DOCUMENT))
            if ident.identifier_type == TYPE_CUSTOMER_NAME:
                if _contains_text(haystack, ident.identifier_value):
                    hits.append({"kind": "customer_name", "value": ident.identifier_value,
                                 "source_system": ident.source_system, "verified": verified})
            elif ident.identifier_type == TYPE_ADDRESS_LINE:
                if _contains_text(haystack, ident.identifier_value) and _postal_present(store, tokens):
                    hits.append({"kind": "address", "value": ident.identifier_value,
                                 "source_system": ident.source_system, "verified": verified})
            elif ident.identifier_type == TYPE_POSTAL_CODE:
                continue                          # only ever counted alongside a street line
            elif _contains_code(tokens, ident.identifier_value):
                hits.append({"kind": ident.identifier_type, "value": ident.identifier_value,
                             "source_system": ident.source_system, "verified": verified})
        # the confirmed name/address on the store itself
        if store.display_name and _contains_text(haystack, store.display_name):
            hits.append({"kind": "display_name", "value": store.display_name,
                         "source_system": "store", "verified": store.identity_status == "confirmed"})
        if store.customer_name and _contains_text(haystack, store.customer_name):
            hits.append({"kind": "customer_name", "value": store.customer_name,
                         "source_system": "store", "verified": store.identity_status == "confirmed"})
        if store.address_line_1 and _contains_text(haystack, store.address_line_1) \
                and _postal_present(store, tokens):
            hits.append({"kind": "address", "value": store.address_line_1,
                         "source_system": "store", "verified": store.identity_status == "confirmed"})
        if hits:
            # de-duplicate identical evidence, verified first
            seen: set[tuple[str, str]] = set()
            unique = []
            for h in sorted(hits, key=lambda h: (not h["verified"], h["kind"])):
                key = (h["kind"], normalize_text(h["value"]))
                if key not in seen:
                    seen.add(key)
                    unique.append(h)
            candidates.append(StoreCandidate(
                store_id=str(store.id), label=store.label, identity_status=store.identity_status,
                address=store.address_summary, matched_on=unique,
            ))
    candidates.sort(key=lambda c: (-sum(h["verified"] for h in c.matched_on), -len(c.matched_on), c.label))
    return candidates


def _postal_present(store: Store, tokens: set[str]) -> bool:
    postal_codes = [i.identifier_value for i in store.identifiers if i.identifier_type == TYPE_POSTAL_CODE]
    if store.postal_code:
        postal_codes.append(store.postal_code)
    for code in postal_codes:
        digits = normalize_digits(code)
        if digits and (digits in tokens or digits[:5] in tokens):
            return True
    return False


async def identify_store(session: AsyncSession, text: str) -> list[StoreCandidate]:
    stores = await StoreRepository(session).list()
    return match_stores(text, stores)


def candidate_ids(candidates: list[StoreCandidate]) -> set[str]:
    return {c.store_id for c in candidates}


__all__ = ["StoreCandidate", "StoreIdentifier", "identify_store", "match_stores", "candidate_ids"]
