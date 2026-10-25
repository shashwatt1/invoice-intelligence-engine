"""
Read-only Product Master evidence for the resolver — app/services/product_master/resolution_evidence.py

The resolver (resolution.py) takes Product Master and crosswalk lookups as
plain callables so it never opens a database session; this module builds those
callables, READ-ONLY, from two sources:

  LiveProductMaster        a DB-API connection the caller has already put in a
                           READ ONLY session; SELECT statements only.
  SeedPreviewProductMaster analysis/master-data/product_master_identifiers.csv
                           (+ product_master_candidates.csv for descriptions):
                           the seed PREVIEW export. It is not the live Product
                           Master and is only ever labelled as a stand-in.

Evidence states pass through untouched — AUTO_MATCHED is never promoted to
CONFIRMED here or anywhere in the resolver. A supplier crosswalk match is a
SUPPLIER_ITEM_ID identifier recorded under the template's supplier scope
(source_distributor); the resolver trusts only CONFIRMED ones, which are the
rows an approved review wrote (identity_review_service).

Nothing here writes: no insert, update or delete statement and no commit.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from app.services.product_master.resolution import PMMatch

TRUSTED_FOR_VERIFIED = "CONFIRMED"
EVIDENCE_STATES = ("CONFIRMED", "AUTO_MATCHED", "REVIEW_REQUIRED", "CONFLICT", "UNRESOLVED")

_GTIN_SQL = (
    "SELECT p.id::text, i.evidence_state, i.source_system, p.canonical_upc "
    "FROM master_products p JOIN master_product_identifiers i ON i.product_id = p.id "
    "WHERE p.canonical_upc = %s"
)
_DESCRIPTION_SQL = "SELECT description FROM master_product_descriptions WHERE product_id::text = %s"
_CROSSWALK_SQL = (
    "SELECT p.id::text, i.evidence_state, i.source_system, p.canonical_upc "
    "FROM master_product_identifiers i JOIN master_products p ON p.id = i.product_id "
    "WHERE i.identifier_type = 'SUPPLIER_ITEM_ID' AND i.source_distributor = %s AND i.normalized_value = %s"
)


class LiveProductMaster:
    """Lookups over the live Product Master through a READ ONLY DB-API connection."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def _descriptions(self, product_id: str) -> tuple[str, ...]:
        cursor = self._connection.cursor()
        cursor.execute(_DESCRIPTION_SQL, (product_id,))
        return tuple(row[0] for row in cursor.fetchall() if row[0])

    def gtin(self, gtin: str) -> tuple[PMMatch, ...]:
        cursor = self._connection.cursor()
        cursor.execute(_GTIN_SQL, (gtin,))
        rows = cursor.fetchall()
        descriptions = {pid: self._descriptions(pid) for pid in {r[0] for r in rows}}
        return tuple(PMMatch(pid, state, system, upc, descriptions[pid]) for pid, state, system, upc in rows)

    def crosswalk(self, scope: str, supplier_item_id: str) -> tuple[PMMatch, ...]:
        cursor = self._connection.cursor()
        cursor.execute(_CROSSWALK_SQL, (scope, supplier_item_id))
        return tuple(PMMatch(*row) for row in cursor.fetchall())


class SeedPreviewProductMaster:
    """The seed PREVIEW export as a lookup. A labelled stand-in, never the live Product Master."""

    LABEL = "seed PREVIEW export (not the live Product Master)"

    def __init__(self, identifiers_csv: Path, candidates_csv: Path | None = None) -> None:
        self._by_upc: dict[str, list[PMMatch]] = defaultdict(list)
        self._crosswalk: dict[tuple[str, str], list[PMMatch]] = defaultdict(list)
        descriptions: dict[str, tuple[str, ...]] = {}
        if candidates_csv and candidates_csv.exists():
            with candidates_csv.open(newline="") as handle:
                for row in csv.DictReader(handle):
                    text = row.get("descriptions") or ""
                    descriptions[row["canonical_key"]] = tuple(d.strip() for d in text.split(" | ") if d.strip())
        self.rows = 0
        with identifiers_csv.open(newline="") as handle:
            for row in csv.DictReader(handle):
                self.rows += 1
                key = row["canonical_key"]
                upc = key[4:] if key.startswith("upc:") else None
                match = PMMatch(key, row["evidence_state"], row["source_system"], upc, descriptions.get(key, ()))
                if upc:
                    self._by_upc[upc].append(match)
                if row["identifier_type"] == "SUPPLIER_ITEM_ID" and row.get("source_distributor"):
                    self._crosswalk[(row["source_distributor"], row["normalized_value"])].append(match)
        self.canonical_upcs = len(self._by_upc)
        self.scoped_supplier_ids = len(self._crosswalk)

    def gtin(self, gtin: str) -> tuple[PMMatch, ...]:
        return tuple(self._by_upc.get(gtin, ()))

    def crosswalk(self, scope: str, supplier_item_id: str) -> tuple[PMMatch, ...]:
        return tuple(self._crosswalk.get((scope, supplier_item_id), ()))


def classify_matches(matches: Sequence[PMMatch]) -> str:
    """The best evidence state a lookup returned, or 'NOT_FOUND'. For reporting only."""
    if not matches:
        return "NOT_FOUND"
    states = {m.evidence_state for m in matches}
    return next((s for s in EVIDENCE_STATES if s in states), sorted(states)[0])


def evidence_profile(lookups: Sequence[tuple[str, Sequence[PMMatch]]]) -> dict[str, int]:
    """Counts of the best evidence state per looked-up GTIN."""
    return dict(Counter(classify_matches(matches) for _gtin, matches in lookups))
