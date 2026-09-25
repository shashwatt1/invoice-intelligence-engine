"""
Source-store resolution — app/services/product_master/stores.py

Resolves a reference workbook's store code to the Store record the
application already knows it by, using nothing but the `store_identifiers`
table.

Two questions are kept apart, because Phase 2A showed how easily they are
conflated:

    A. SOURCE STORE RESOLUTION     "47708760" -> Store 51e39a69
       A deterministic lookup. Already established. No inference.

    B. PHYSICAL STORE IDENTIFICATION   Store 51e39a69 -> a real location
       A human decision backed by evidence. Unanswered for both codes.

(A) does not require (B). An unresolved Store record is not a missing
store — it is a stable identity meaning "the location the Item Sales
export calls 47708760", and source-system-scoped reference data belongs to
it. So the resolver reports both facts separately and never lets one stand
in for the other.

What this module deliberately cannot do: fuzzy matching, product-catalogue
overlap, invoice store assignment, customer-name or address similarity, ZIP
matching, filename inference. Phase 2A disqualified the invoice-assignment
signal specifically — those assignments were recorded as
"reference-store assignment, not a physical identity declaration" — and
measured catalogue overlap as unfit (90% vs 91% between two different
stores). The only admissible input here is an exact `store_identifiers`
row.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.store import IDENTITY_CONFIRMED, SOURCE_ITEM_SALES, TYPE_STORE_CODE

# What the lookup concluded. Recorded rather than collapsed into a boolean,
# so an unmatched code can never read as a resolved one.
OUTCOME_RESOLVED = "RESOLVED"
OUTCOME_UNKNOWN_SOURCE_CODE = "UNKNOWN_SOURCE_CODE"
OUTCOME_AMBIGUOUS = "AMBIGUOUS"
OUTCOME_NO_SOURCE_CODE = "NO_SOURCE_CODE"


@dataclass(frozen=True)
class StoreRecord:
    """One row of the store_identifiers -> stores relationship."""

    store_id: str
    identity_status: str
    display_name: str | None = None


@dataclass(frozen=True)
class StoreResolution:
    """
    The outcome of resolving one source store code.

    `source_store_resolved` and `physical_store_identity_confirmed` are
    separate fields on purpose: the first says the code maps to a Store
    record, the second says someone confirmed what that Store physically
    is. A code can be fully resolved while its store remains unidentified,
    which is the normal case today.
    """

    source_code: str | None
    outcome: str
    store_id: str | None = None
    store_identity_status: str | None = None
    store_display_name: str | None = None

    @property
    def source_store_resolved(self) -> bool:
        return self.outcome == OUTCOME_RESOLVED

    @property
    def physical_store_identity_confirmed(self) -> bool:
        return self.store_identity_status == IDENTITY_CONFIRMED


class StoreIdentifierIndex:
    """
    An in-memory view of `store_identifiers`, keyed exactly as the table's
    own unique constraint keys it.

    Built from rows read elsewhere so the lookup itself stays pure and
    testable without a database.
    """

    def __init__(self, rows: list[tuple[str, str, str, StoreRecord]]) -> None:
        self._index: dict[tuple[str, str, str], list[StoreRecord]] = {}
        for source_system, identifier_type, identifier_value, record in rows:
            key = (source_system, identifier_type, str(identifier_value).strip())
            self._index.setdefault(key, []).append(record)

    def __len__(self) -> int:
        return len(self._index)

    def resolve(
        self,
        source_code: str | None,
        *,
        source_system: str = SOURCE_ITEM_SALES,
        identifier_type: str = TYPE_STORE_CODE,
    ) -> StoreResolution:
        """Exact lookup. No fallback, no nearest match, no guess."""
        if source_code is None or not str(source_code).strip():
            return StoreResolution(source_code=None, outcome=OUTCOME_NO_SOURCE_CODE)

        code = str(source_code).strip()
        matches = self._index.get((source_system, identifier_type, code), [])

        if not matches:
            return StoreResolution(source_code=code, outcome=OUTCOME_UNKNOWN_SOURCE_CODE)
        if len({m.store_id for m in matches}) > 1:
            # The table's unique constraint makes this unreachable today.
            # It is handled anyway so that a constraint change could never
            # turn an ambiguity into a silently chosen store.
            return StoreResolution(source_code=code, outcome=OUTCOME_AMBIGUOUS)

        record = matches[0]
        return StoreResolution(
            source_code=code,
            outcome=OUTCOME_RESOLVED,
            store_id=record.store_id,
            store_identity_status=record.identity_status,
            store_display_name=record.display_name,
        )


def resolve_commercial_candidates(
    candidates: list[dict], index: StoreIdentifierIndex,
) -> dict[str, int]:
    """
    Annotate commercial candidates in place with their store resolution.

    Returns the tally the preview reports. Commercial candidates are only
    annotated here — this phase does not create commercial mappings.
    """
    tally = {
        "total": len(candidates),
        "source_store_resolved": 0,
        "source_store_unknown": 0,
        "ambiguous": 0,
        "no_source_code": 0,
        "physical_identity_confirmed": 0,
        "physical_identity_unresolved": 0,
    }
    for candidate in candidates:
        resolution = index.resolve(candidate.get("store_context"))
        candidate["store_id"] = resolution.store_id
        candidate["store_resolution_outcome"] = resolution.outcome
        candidate["source_store_resolved"] = resolution.source_store_resolved
        candidate["store_identity_status"] = resolution.store_identity_status
        candidate["physical_store_identity_confirmed"] = (
            resolution.physical_store_identity_confirmed
        )

        if resolution.outcome == OUTCOME_RESOLVED:
            tally["source_store_resolved"] += 1
            if resolution.physical_store_identity_confirmed:
                tally["physical_identity_confirmed"] += 1
            else:
                tally["physical_identity_unresolved"] += 1
        elif resolution.outcome == OUTCOME_AMBIGUOUS:
            tally["ambiguous"] += 1
        elif resolution.outcome == OUTCOME_NO_SOURCE_CODE:
            tally["no_source_code"] += 1
        else:
            tally["source_store_unknown"] += 1
    return tally


def load_store_identifier_index(dsn: str) -> StoreIdentifierIndex:
    """
    Read `store_identifiers` in a READ ONLY session.

    Kept separate from the lookup so the resolution logic can be tested
    without a database, and so this is the only place that touches one.
    """
    import psycopg2

    connection = psycopg2.connect(dsn, connect_timeout=5)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """SELECT si.source_system, si.identifier_type, si.identifier_value,
                      si.store_id, st.identity_status, st.display_name
               FROM store_identifiers si
               JOIN stores st ON st.id = si.store_id"""
        )
        rows = [
            (source_system, identifier_type, identifier_value,
             StoreRecord(str(store_id), identity_status, display_name))
            for source_system, identifier_type, identifier_value,
                store_id, identity_status, display_name in cursor.fetchall()
        ]
    finally:
        connection.close()
    return StoreIdentifierIndex(rows)
