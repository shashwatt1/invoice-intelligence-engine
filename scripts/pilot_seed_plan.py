"""
Pilot seed plan — what `scripts/pilot_seed.py --dry-run` runs.

Computes, without writing anything, every row the pilot seed would create:
the same source normalisation, identity resolution, commercial derivation,
canonical-description policy and source-snapshot construction the real
stages use — the stages' own `plan_*` functions — chained in memory, so
each stage sees what the earlier ones would have produced rather than the
empty tables a rolled-back rehearsal leaves behind.

The database is only read, inside a READ ONLY transaction: which Product
Master rows already exist (so the plan is as idempotent as the seed), the
legacy case mappings the commercial decision may read as dissent, and the
store identifiers. Nothing is added to a session, flushed or committed.

Every planned row is then checked against the table it would be inserted
into — NOT NULL, column length, unique keys, references — so a row that
would make the real seed fail is reported here instead of rolling it back.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import cast

from sqlalchemy import Table, UniqueConstraint, select, text

from app.models.product_master import (
    COMMERCIAL_CONFLICT,
    MAX_UNITS_ACCOUNTED_FOR,
    MIN_UNITS_ACCOUNTED_FOR,
    STATE_REVIEW_REQUIRED,
    VALID_COMMERCIAL_BASES,
    VALID_COST_BASES,
    VALID_DERIVATIONS,
    VALID_DESCRIPTION_ROLES,
    VALID_IDENTIFIER_TYPES,
    VALID_IDENTITY_BASES,
    VALID_PACK_BASES,
    VALID_STATES,
    MasterCommercialMapping,
    MasterCommercialReview,
    MasterPackComposition,
    MasterProduct,
    MasterProductDescription,
    MasterProductIdentifier,
)
from app.services.master_commercial_review_service import review_status
from app.services.product_master.candidates import build_candidates
from scripts import (
    backfill_commercial_source_snapshot as snapshot_stage,
)
from scripts import (
    seed_product_master_commercial as commercial_stage,
)
from scripts import (
    seed_product_master_descriptions as description_stage,
)
from scripts import (
    seed_product_master_identity as identity_stage,
)
from scripts.analyze_product_master import load_raw_records
from scripts.preview_product_master_seed import _load_store_index, to_master_rows

# Stands in for the id Postgres would generate for a product that does not
# exist yet. Never sent to the database.
PLANNED_ID_PREFIX = "planned:"

MAX_EXAMPLES = 20

PRODUCT_FIELDS = ("id", "canonical_key", "canonical_upc", "canonical_description")
DESCRIPTION_FIELDS = ("product_id", "description", "normalized_description", "role",
                      "source_system", "source_file", "source_sheet", "source_row")
MAPPING_FIELDS = ("id", "product_id", "store_id", "source_snapshot", "approval_state",
                  "commercial_unit_basis", "units_accounted_for")


def _detach(obj, names) -> SimpleNamespace:
    """A plain copy, so nothing the plan does can dirty a session object."""
    return SimpleNamespace(**{name: getattr(obj, name) for name in names})


@dataclass
class ExistingState:
    """What the target database already holds, as the seed would read it."""

    products: dict = field(default_factory=dict)            # canonical_key -> id
    commercial_products: dict = field(default_factory=dict)  # canonical_key -> (id, upc)
    identifiers: set = field(default_factory=set)
    descriptions: set = field(default_factory=set)
    pack_compositions: set = field(default_factory=set)
    commercial_pairs: set = field(default_factory=set)
    governed: dict = field(default_factory=dict)
    product_rows: list = field(default_factory=list)
    description_rows: list = field(default_factory=list)
    mapping_rows: list = field(default_factory=list)          # (mapping, product)
    reviews: int = 0


async def read_existing_state(session) -> ExistingState:
    """Every statement here is a SELECT, issued inside a READ ONLY transaction."""
    await session.execute(text("SET TRANSACTION READ ONLY"))
    state = ExistingState(
        products=await identity_stage.load_existing_products(session),
        identifiers=await identity_stage.load_existing_identifiers(session),
        descriptions=await identity_stage.load_existing_descriptions(session),
        pack_compositions=await identity_stage.load_existing_pack_compositions(session),
        commercial_products=await commercial_stage.load_master_products(session),
        governed=dict(await commercial_stage.load_governed_units(session)),
        commercial_pairs=await commercial_stage.load_existing_pairs(session),
    )
    state.product_rows = [
        _detach(p, PRODUCT_FIELDS)
        for p in (await session.execute(select(MasterProduct))).scalars().all()
    ]
    state.description_rows = [
        _detach(d, DESCRIPTION_FIELDS)
        for d in (await session.execute(select(MasterProductDescription))).scalars().all()
    ]
    state.mapping_rows = [
        (_detach(m, MAPPING_FIELDS), _detach(p, ("id", "canonical_key")))
        for m, p in (await session.execute(
            select(MasterCommercialMapping, MasterProduct)
            .join(MasterProduct, MasterProduct.id == MasterCommercialMapping.product_id)
        )).all()
    ]
    state.reviews = len((await session.execute(select(MasterCommercialReview.id))).all())

    if session.new or session.dirty or session.deleted:
        raise RuntimeError("the plan left objects in the session; refusing to continue")
    await session.rollback()
    return state


async def plan_pipeline(target, store: str | None) -> dict:
    """Read the target, then plan the whole seed in memory."""
    from app.core.config import get_settings
    from app.database.session import get_session_factory
    from scripts.pilot_seed_guard import seed_target_host

    # The same gate every stage passes before it may touch this database.
    host = seed_target_host(get_settings().database_url,
                            SimpleNamespace(pilot_target=target))

    records = load_raw_records()
    async with get_session_factory()() as session:
        state = await read_existing_state(session)
    store_index, store_error = _load_store_index()
    return build_plan(records, store, state, store_index, store_error, host=host)


# ---------------------------------------------------------------------------
# The plan itself — pure
# ---------------------------------------------------------------------------

def build_plan(records, store, state: ExistingState, store_index, store_error, *,
               host: str) -> dict:
    """Chain the four stages' planners in memory, then validate the result."""
    # 1. identity — the same inputs and planners the identity stage uses.
    graph, products = identity_stage.identity_inputs(records, store)
    identity = identity_stage.new_identity_report(graph, products, dry_run=True,
                                                  host=host, store=store)
    identity["already_present"]["products"] = len(set(state.products) & set(products))
    planned_products = identity_stage.plan_products(products, state.products)
    product_ids = dict(state.products)
    for fields in planned_products:
        product_ids[fields["canonical_key"]] = PLANNED_ID_PREFIX + fields["canonical_key"]
    identity["products_created"] = len(planned_products)

    identifiers, present, collapsed = identity_stage.plan_identifiers(
        products, product_ids, state.identifiers)
    identity["already_present"]["identifiers"] = present
    identity["identifiers_created"] = len(identifiers)
    identity["duplicate_candidates_collapsed"] += collapsed

    source_descriptions, present = identity_stage.plan_descriptions(
        products, product_ids, state.descriptions)
    identity["already_present"]["descriptions"] = present
    identity["descriptions_created"] = len(source_descriptions)

    packs, present, skipped = identity_stage.plan_pack_compositions(
        graph, product_ids, state.pack_compositions)
    identity["already_present"]["pack_compositions"] = present
    identity["pack_compositions_skipped_unseedable_end"] = skipped
    identity["pack_compositions_created"] = len(packs)

    # 2. commercial — sees the products stage 1 would have created.
    commercial_graph = build_candidates(to_master_rows(records, store))
    grouped, unattached, store_tally = commercial_stage.group_commercial_candidates(
        commercial_graph, store_index)
    commercial = commercial_stage.new_commercial_report(
        commercial_graph, grouped, unattached, store_tally, store_error,
        dry_run=True, host=host)
    commercial["legacy_case_mappings_read"] = sum(len(v) for v in state.governed.values())
    commercial_products = dict(state.commercial_products)
    for fields in planned_products:
        commercial_products[fields["canonical_key"]] = (
            product_ids[fields["canonical_key"]], fields["canonical_upc"])
    mappings, _review = commercial_stage.plan_commercial_mappings(
        grouped, commercial_products, state.governed, state.commercial_pairs, commercial)

    # 3. canonical descriptions — over existing and planned products and
    #    the source descriptions stage 1 would have written.
    all_products = list(state.product_rows) + [
        SimpleNamespace(id=product_ids[f["canonical_key"]], canonical_key=f["canonical_key"],
                        canonical_upc=f["canonical_upc"], canonical_description=None)
        for f in planned_products
    ]
    all_descriptions = list(state.description_rows) + [
        SimpleNamespace(**row) for row in source_descriptions]
    descriptions = description_stage.new_description_report(dry_run=True, host=host)
    canonical = description_stage.plan_canonical_descriptions(
        all_products, all_descriptions, descriptions)
    canonical_rows = [fields for _product, fields in canonical]

    # 4. source snapshots — over existing and planned mappings.
    key_by_id = {p.id: p.canonical_key for p in all_products}
    snapshot_graph = build_candidates(to_master_rows(records, None))
    by_key, rows_by_reference = snapshot_stage.snapshot_inputs(
        snapshot_graph, to_master_rows(records, None))
    planned_mapping_rows = [
        (SimpleNamespace(**fields, source_snapshot={}),
         SimpleNamespace(id=fields["product_id"], canonical_key=key_by_id[fields["product_id"]]))
        for fields in mappings
    ]
    snapshot = snapshot_stage.new_snapshot_report(dry_run=True)
    snapshots = snapshot_stage.plan_snapshots(
        list(state.mapping_rows) + planned_mapping_rows, by_key, rows_by_reference, snapshot)

    planned = {
        "master_products": planned_products,
        "master_product_identifiers": identifiers,
        "master_product_descriptions": source_descriptions + canonical_rows,
        "master_pack_compositions": packs,
        "master_commercial_mappings": mappings,
        "master_commercial_reviews": [],
    }
    stages = {"identity": identity, "commercial": commercial,
              "descriptions": descriptions, "source_snapshot": snapshot}
    return summarise(
        records=records, source_rows=len(to_master_rows(records, store)),
        commercial_graph=commercial_graph, state=state,
        planned=planned, canonical_rows=canonical_rows, source_descriptions=source_descriptions,
        planned_mapping_rows=planned_mapping_rows, snapshots=snapshots,
        product_ids=product_ids, store_index=store_index, stages=stages)


def summarise(*, records, source_rows, commercial_graph, state, planned, canonical_rows,
              source_descriptions, planned_mapping_rows, snapshots, product_ids,
              store_index, stages) -> dict:
    identity, commercial = stages["identity"], stages["commercial"]
    descriptions, snapshot = stages["descriptions"], stages["source_snapshot"]

    existing = {
        "master_products": len(state.product_rows),
        "master_product_identifiers": len(state.identifiers),
        "master_product_descriptions": len(state.description_rows),
        "master_pack_compositions": len(state.pack_compositions),
        "master_commercial_mappings": len(state.mapping_rows),
        "master_commercial_reviews": state.reviews,
    }
    would_create = {table: len(rows) for table, rows in planned.items()}
    totals_after = {table: existing[table] + would_create[table] for table in existing}

    # Review position of every mapping after the seed, as the dashboard
    # derives it.
    all_mappings = [m for m, _p in state.mapping_rows] + [m for m, _p in planned_mapping_rows]
    statuses = Counter(review_status(m) for m in all_mappings)
    approvals = Counter(m.approval_state for m in all_mappings)
    conflict_origin = Counter(
        conflict_origin_of(m) for m, _p in planned_mapping_rows
        if m.commercial_unit_basis == COMMERCIAL_CONFLICT)

    snapshotted = {id(mapping) for mapping, _s in snapshots}
    with_snapshot = sum(1 for m in all_mappings if m.source_snapshot or id(m) in snapshotted)
    empty_snapshots = sum(1 for _m, s in snapshots if not s.get("source_rows"))

    validation = validate(planned, state, product_ids, store_index, commercial)
    consistency = {
        "products_match_identity_report":
            would_create["master_products"] == identity["products_created"],
        "identifiers_match_identity_report":
            would_create["master_product_identifiers"] == identity["identifiers_created"],
        "descriptions_are_source_plus_canonical":
            would_create["master_product_descriptions"]
            == identity["descriptions_created"] + descriptions["canonical_set"],
        "packs_match_identity_report":
            would_create["master_pack_compositions"] == identity["pack_compositions_created"],
        "mappings_match_commercial_report":
            would_create["master_commercial_mappings"] == commercial["mappings_created"],
        "every_mapping_has_a_review_status": sum(statuses.values()) == len(all_mappings),
        "snapshot_examined_every_mapping": snapshot["examined"] == len(all_mappings),
        "snapshot_outcomes_add_up":
            snapshot["written"] + snapshot["already_present"] + snapshot["no_source_found"]
            == snapshot["examined"],
        "canonical_descriptions_do_not_exceed_products":
            descriptions["canonical_set"] <= totals_after["master_products"],
        "no_reviews_created": would_create["master_commercial_reviews"] == 0,
        "every_planned_mapping_awaits_review": all(
            m.approval_state == STATE_REVIEW_REQUIRED for m, _p in planned_mapping_rows),
    }

    blocking = list(validation["would_fail"])
    blocking += [f"internal consistency check failed: {name}"
                 for name, ok in consistency.items() if not ok]
    if commercial["store_lookup_error"]:
        blocking.append(
            f"store identifiers could not be read ({commercial['store_lookup_error']}); "
            "the seed would resolve no store and create no commercial mappings")
    if commercial["candidates_examined"] and not commercial["store_resolution"].get(
            "source_store_resolved"):
        codes = sorted({str(c.get("store_context")) for c in commercial_graph.commercial_candidates})
        blocking.append(
            f"none of the {commercial['candidates_examined']} commercial candidates resolved "
            f"to a store (source store codes {codes} are not in store_identifiers); the seed "
            "would create 0 commercial mappings")
    if snapshot["no_source_found"] or empty_snapshots:
        blocking.append(
            f"source_snapshot incomplete: {snapshot['no_source_found']} mapping(s) with no "
            f"source found, {empty_snapshots} with an empty snapshot")

    return {
        "mode": "plan — no database writes",
        "source": {
            "raw_records": len(records),
            "master_source_rows": source_rows,
            "identity_candidates": identity["candidates_total"],
            "identity_candidates_seedable": identity["candidates_seedable"],
            "commercial_candidates": commercial["candidates_examined"],
            "distinct_product_store_pairs": commercial["distinct_product_store_pairs"],
        },
        "existing_rows": existing,
        "would_create": would_create,
        "would_create_descriptions_by_role": {
            "source_and_invoice": len(source_descriptions),
            "canonical": len(canonical_rows),
        },
        "would_update": {
            "master_products.canonical_description": descriptions["canonical_set"],
            "master_commercial_mappings.source_snapshot": snapshot["written"],
        },
        "expected_totals_after": totals_after,
        "skipped": {
            "unresolved_identity_candidates": identity["skipped_unresolved_candidates"],
            "conflicting_identity_candidates": identity["skipped_conflicts"],
            "duplicate_identifiers_collapsed": identity["duplicate_candidates_collapsed"],
            "pack_compositions_unseedable_end":
                identity["pack_compositions_skipped_unseedable_end"],
            "commercial_store_unresolved": commercial["skipped_store_unresolved"],
            "commercial_no_master_product": commercial["skipped_no_master_product"],
            "canonical_description_left_unset": descriptions["unresolved"],
        },
        "commercial": {
            "review_status": dict(statuses),
            "approval_state": dict(approvals),
            "commercial_unit_basis": commercial["basis_counts"],
            # Source-internal conflicts come from the reference data and hold
            # anywhere; legacy-dissent conflicts exist only where the target
            # database has governed product_case_mappings to dissent with.
            "conflict_origin": {"source_internal": conflict_origin.get(ORIGIN_SOURCE, 0),
                                "legacy_dissent": conflict_origin.get(ORIGIN_LEGACY, 0)},
            "cost_basis": commercial["cost_basis_counts"],
            "with_resolved_units": commercial["with_resolved_units"],
            "without_resolved_units": commercial["without_resolved_units"],
            "store_resolution": commercial["store_resolution"],
            "legacy_case_mappings_read": commercial["legacy_case_mappings_read"],
        },
        "source_snapshot": {
            "mappings_after_seed": len(all_mappings),
            "with_snapshot_after_seed": with_snapshot,
            "no_source_found": snapshot["no_source_found"],
            "empty_snapshots": empty_snapshots,
            "coverage_complete": with_snapshot == len(all_mappings) and not empty_snapshots,
            "fields": snapshot["fields"],
        },
        "legacy_tables_written": [],
        "validation": {**validation, "consistency": consistency, "blocking": blocking},
        "ready_for_live_seed": not blocking,
        "stages": stages,
    }


# ---------------------------------------------------------------------------
# Validation — what would make the real INSERTs fail, and what would be wrong
# ---------------------------------------------------------------------------

MODELS = {
    "master_products": MasterProduct,
    "master_product_identifiers": MasterProductIdentifier,
    "master_product_descriptions": MasterProductDescription,
    "master_pack_compositions": MasterPackComposition,
    "master_commercial_mappings": MasterCommercialMapping,
}

DOMAINS: dict[str, dict[str, Collection]] = {
    "master_products": {"identity_basis": VALID_IDENTITY_BASES,
                        "identity_state": VALID_STATES},
    "master_product_identifiers": {"identifier_type": VALID_IDENTIFIER_TYPES,
                                   "derivation": VALID_DERIVATIONS,
                                   "evidence_state": VALID_STATES},
    "master_product_descriptions": {"role": VALID_DESCRIPTION_ROLES,
                                    "evidence_state": VALID_STATES},
    "master_pack_compositions": {"composition_basis": VALID_PACK_BASES,
                                 "evidence_state": VALID_STATES},
    "master_commercial_mappings": {"commercial_unit_basis": VALID_COMMERCIAL_BASES,
                                   "cost_basis": VALID_COST_BASES | {None},
                                   "approval_state": {STATE_REVIEW_REQUIRED}},
}

PRODUCT_REFERENCES = {
    "master_product_identifiers": ("product_id",),
    "master_product_descriptions": ("product_id",),
    "master_pack_compositions": ("parent_product_id", "child_product_id"),
    "master_commercial_mappings": ("product_id",),
}


ORIGIN_SOURCE = "source_internal"
ORIGIN_LEGACY = "legacy_dissent"


def conflict_origin_of(mapping) -> str:
    """Why a planned mapping is CONFLICT, read from its own evidence."""
    stated = {s.get("units") for s in mapping.evidence.get("source_statements", [])
              if s.get("units") is not None}
    return ORIGIN_SOURCE if len(stated) > 1 else ORIGIN_LEGACY


def _existing_keys(state: ExistingState) -> dict[str, set[tuple]]:
    """Existing unique keys, by constraint, in the shape planned rows are checked."""
    as_str = lambda keys: {tuple(str(v) for v in key) for key in keys}  # noqa: E731
    return {
        "uq_master_products_canonical_key": {(str(k),) for k in state.products},
        "uq_master_identifier_source_value": as_str(state.identifiers),
        "uq_master_description_role_source": as_str(state.descriptions),
        "uq_master_pack_composition": as_str(state.pack_compositions),
    }


def validate(planned: dict, state: ExistingState, product_ids: dict, store_index,
             commercial_report: dict) -> dict:
    would_fail: list[str] = []
    warnings: list[str] = []
    existing_keys = _existing_keys(state)
    known_products = {str(pid) for pid in product_ids.values()}
    known_stores = {str(record.store_id)
                    for records in getattr(store_index, "_index", {}).values()
                    for record in records}

    for table, model in MODELS.items():
        rows = planned[table]
        model_table = cast(Table, model.__table__)
        columns = model_table.columns
        problems: Counter = Counter()
        examples: list[str] = []

        def note(kind: str, detail: str, _problems=problems, _examples=examples) -> None:
            _problems[kind] += 1
            if len(_examples) < MAX_EXAMPLES:
                _examples.append(detail)

        for index, row in enumerate(rows):
            for column in columns:
                value = row.get(column.key)
                if column.key not in row:
                    if not (column.nullable or column.primary_key
                            or column.server_default is not None or column.default is not None):
                        note("missing required column", f"{table}[{index}].{column.key}")
                    continue
                if value is None:
                    if not column.nullable:
                        note("NULL in NOT NULL column", f"{table}[{index}].{column.key}")
                    continue
                length = getattr(column.type, "length", None)
                if length and isinstance(value, str) and len(value) > length:
                    note("value longer than column",
                         f"{table}[{index}].{column.key}: {len(value)} > {length}")
            for column_name in PRODUCT_REFERENCES.get(table, ()):
                if row.get(column_name) is not None and str(row[column_name]) not in known_products:
                    note("unknown product reference", f"{table}[{index}].{column_name}")
            if table == "master_commercial_mappings" and str(row["store_id"]) not in known_stores:
                note("unknown store reference", f"{table}[{index}].store_id")

        for constraint in model_table.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            names = [c.key for c in constraint.columns]
            keys = Counter(
                tuple(str(row.get(n)) for n in names) for row in rows
                # Postgres does not consider NULLs equal in a unique key.
                if all(row.get(n) is not None for n in names)
            )
            for key, count in keys.items():
                if count > 1:
                    note(f"duplicate {constraint.name}", f"{table}: {key} x{count}")
            for key in set(keys) & existing_keys.get(str(constraint.name), set()):
                note(f"collides with existing {constraint.name}", f"{table}: {key}")

        for kind, count in problems.items():
            would_fail.append(f"{table}: {count} row(s) — {kind}")
        if examples:
            would_fail.append(f"{table} examples: " + "; ".join(examples))

        # Values the schema would accept but the Product Master vocabulary
        # does not — not a failed INSERT, still not something to seed.
        for column_name, allowed in DOMAINS.get(table, {}).items():
            bad = Counter(row.get(column_name) for row in rows
                          if row.get(column_name) not in allowed)
            if bad:
                warnings.append(f"{table}.{column_name}: unexpected values {dict(bad)}")
        if table == "master_commercial_mappings":
            out_of_range = sum(
                1 for row in rows if row["units_accounted_for"] is not None
                and not MIN_UNITS_ACCOUNTED_FOR <= row["units_accounted_for"]
                <= MAX_UNITS_ACCOUNTED_FOR)
            if out_of_range:
                warnings.append(f"{table}.units_accounted_for: {out_of_range} out of range")
        if table == "master_pack_compositions":
            non_positive = sum(1 for row in rows if (row.get("child_quantity") or 0) < 1)
            if non_positive:
                warnings.append(f"{table}.child_quantity: {non_positive} below 1")

    if commercial_report["skipped_store_unresolved"]:
        warnings.append(
            f"{commercial_report['skipped_store_unresolved']} commercial candidate(s) skipped: "
            "source store code not in store_identifiers")
    return {"would_fail": would_fail, "warnings": warnings}
