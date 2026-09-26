"""
Tests — the pilot seed's dry run is a plan, not a rolled-back write.

`pilot_seed.py --dry-run` used to run every stage and roll each one back:
thousands of remote INSERTs, and every stage after the first saw empty
tables because its predecessor had already been undone. It now runs the
stages' own planning logic in memory, each stage fed the one before, and
reads the database only inside a READ ONLY transaction.

These pin four things:

  * the dry run issues no INSERT/UPDATE/DELETE, adds nothing to a session,
    flushes nothing and commits nothing;
  * later stages plan against the products and mappings earlier stages
    would have created;
  * the plan's counts agree with each other, and with what the real seed
    actually writes from the same starting state;
  * the real seed's write path is the one it always was.

Everything is offline: a small synthetic source set, an in-memory session
(tests/seed_fakes.py), and a store index built in the test.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.product_master import (
    DESC_CANONICAL,
    STATE_REVIEW_REQUIRED,
    MasterCommercialMapping,
    MasterCommercialReview,
    MasterPackComposition,
    MasterProduct,
    MasterProductDescription,
    MasterProductIdentifier,
)
from app.models.store import SOURCE_ITEM_SALES, TYPE_STORE_CODE
from app.services.master_commercial_review_service import review_status
from app.services.product_master.candidates import MasterSourceRow
from app.services.product_master.identifiers import DISTRIBUTOR_WORKBOOK, ITEM_SALES_SUMMARY
from app.services.product_master.stores import StoreIdentifierIndex, StoreRecord
from scripts import backfill_commercial_source_snapshot as snapshot_stage
from scripts import pilot_seed
from scripts import pilot_seed_plan as planner
from scripts import seed_product_master_commercial as commercial_stage
from scripts import seed_product_master_identity as identity_stage
from scripts.pilot_seed_guard import TARGET_PILOT, SeedTarget
from tests.seed_fakes import FakeSeedDatabase

ROOT = Path(__file__).resolve().parent.parent
PLAN_SOURCE = ROOT / "scripts" / "pilot_seed_plan.py"

STORE_CODE = "47708760"
STORE_ID = str(uuid.uuid4())
TARGET = SeedTarget(TARGET_PILOT, "db.abcdefgh.supabase.co", "postgres", "0024")

TABLES = (MasterProduct, MasterProductIdentifier, MasterProductDescription,
          MasterPackComposition, MasterCommercialMapping, MasterCommercialReview)


def distributor(**overrides) -> MasterSourceRow:
    base = {"source_system": "distributor_price_sheet", "profile": DISTRIBUTOR_WORKBOOK,
            "source_file": "zink.xlsx", "source_sheet": "Sheet1", "source_row": 1,
            "store_context": STORE_CODE}
    base.update(overrides)
    return MasterSourceRow(**base)


def source_rows() -> list[MasterSourceRow]:
    return [
        # A case with an explicit package column: two products and a pack
        # composition, and — from Sheet1 — a commercial statement.
        distributor(source_sheet="Zink - Tiki", source_row=43,
                    raw_identifier="01820096721 4", raw_unit_identifier="0-18200-00334-9",
                    package_notation="18/12OZ CANS", description="MICHELOB ULTRA"),
        distributor(source_row=60, raw_identifier="018200967214",
                    description="MICHELOB ULTRA 18/12 CAN", commercial_units_statement="1.0",
                    commercial_statement_evidence="Sheet1 items/case column",
                    case_cost="25.50"),
        # A second product, many sellable units per case.
        distributor(source_row=61, raw_identifier="087692000570",
                    description="TWISTED TEA 24/12", commercial_units_statement="24.0",
                    commercial_statement_evidence="Sheet1 items/case column"),
        # A commercial statement for a store the index does not know.
        distributor(source_row=62, raw_identifier="087692000570",
                    description="TWISTED TEA 24/12", commercial_units_statement="24.0",
                    commercial_statement_evidence="Sheet1 items/case column",
                    store_context="99999999"),
        # No identifier at all: an unresolved candidate, never seeded.
        MasterSourceRow(source_system="item_sales_summary", profile=ITEM_SALES_SUMMARY,
                        source_file="sales.xlsx", source_sheet="data", source_row=7,
                        description="MYSTERY ITEM"),
    ]


def store_index(*codes: str) -> StoreIdentifierIndex:
    return StoreIdentifierIndex([
        (SOURCE_ITEM_SALES, TYPE_STORE_CODE, code, StoreRecord(STORE_ID, "unresolved", "Test"))
        for code in codes
    ])


@pytest.fixture
def sources(monkeypatch):
    """Point every stage, and the planner, at the synthetic source set."""
    records = ["synthetic"]
    index = {"value": store_index(STORE_CODE)}
    state = SimpleNamespace(records=records, index=index, rows=source_rows)
    for module in (identity_stage, commercial_stage, snapshot_stage, planner):
        monkeypatch.setattr(module, "load_raw_records", lambda: records)
        monkeypatch.setattr(module, "to_master_rows", lambda _records, _store: state.rows())
    for module in (commercial_stage, planner):
        monkeypatch.setattr(module, "_load_store_index", lambda: (index["value"], None))
    return state


@pytest.fixture
def db(monkeypatch) -> FakeSeedDatabase:
    import app.database.session as session_module

    database = FakeSeedDatabase()
    monkeypatch.setattr(session_module, "get_session_factory", database.get_session_factory)
    return database


def plan_against(db: FakeSeedDatabase) -> dict:
    return asyncio.run(planner.plan_pipeline(TARGET, None))


def run_real_seed(db: FakeSeedDatabase) -> None:
    """The real stages, in the orchestrator's order, writing (to the fake)."""
    results = asyncio.run(pilot_seed.run_stages(TARGET, False, None))
    assert not any(r.get("error") or r.get("errors") for r in results.values()), results


def committed_counts(db: FakeSeedDatabase) -> dict[str, int]:
    return {model.__tablename__: len(db.rows(model)) for model in TABLES}


# ---------------------------------------------------------------------------


class TestTheDryRunWritesNothing:

    def test_no_add_flush_commit_or_write_statement_is_attempted(self, sources, db):
        plan_against(db)
        assert db.writes == []
        assert committed_counts(db) == dict.fromkeys(committed_counts(db), 0)

    def test_every_statement_is_a_select_inside_a_read_only_transaction(self, sources, db):
        plan_against(db)
        assert db.statements[0] == "SET TRANSACTION READ ONLY"
        assert all(s.startswith("SELECT") for s in db.statements[1:]), db.statements

    def test_a_populated_database_is_left_exactly_as_it_was(self, sources, db):
        run_real_seed(db)
        before = committed_counts(db)
        writes_before = len(db.writes)
        snapshots = [dict(m.source_snapshot) for m in db.rows(MasterCommercialMapping)]

        plan_against(db)

        assert committed_counts(db) == before
        assert len(db.writes) == writes_before
        assert [dict(m.source_snapshot) for m in db.rows(MasterCommercialMapping)] == snapshots

    def test_the_planner_has_no_write_call_in_it(self):
        source = PLAN_SOURCE.read_text()
        for forbidden in (r"\.add\(", r"\.add_all\(", r"\.flush\(", r"\.commit\(",
                          r"\.merge\(", r"\.delete\(", r"\binsert\(", r"\bupdate\(",
                          r"INSERT\s+INTO", r"UPDATE\s+\w+\s+SET", r"DELETE\s+FROM"):
            assert not re.search(forbidden, source), forbidden

    def test_the_dry_run_flag_never_reaches_the_writing_stages(self, monkeypatch, tmp_path):
        """--dry-run takes the plan branch; run_stages is not called at all."""
        monkeypatch.setattr(pilot_seed, "resolve_seed_target", lambda *a, **k: TARGET)
        monkeypatch.setattr(pilot_seed, "ROOT", tmp_path)
        monkeypatch.setattr(pilot_seed, "REPORT", tmp_path / "report.json")

        async def must_not_run(*_args, **_kwargs):
            raise AssertionError("the dry run called the writing stages")

        monkeypatch.setattr(pilot_seed, "run_stages", must_not_run)

        async def fake_plan(target, store):
            return {**_minimal_plan(), "stages": {}}

        monkeypatch.setattr(planner, "plan_pipeline", fake_plan)
        monkeypatch.setattr(sys, "argv", [
            "pilot_seed.py", "--pilot-seed", "--expect-database", "postgres",
            "--expect-host-contains", "supabase", "--dry-run"])
        pilot_seed.main()
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["dry_run"] is True and "plan" in report


class TestLaterStagesSeeEarlierStagesPlans:

    def test_commercial_plans_against_products_identity_would_create(
            self, sources, db, monkeypatch):
        seen = {}
        original = commercial_stage.plan_commercial_mappings

        def spy(grouped, products, governed, existing, report):
            seen["products"] = dict(products)
            return original(grouped, products, governed, existing, report)

        monkeypatch.setattr(commercial_stage, "plan_commercial_mappings", spy)
        plan = plan_against(db)

        assert db.rows(MasterProduct) == []            # nothing exists yet...
        assert seen["products"]                        # ...but commercial saw products
        assert all(str(pid).startswith(planner.PLANNED_ID_PREFIX)
                   for pid, _upc in seen["products"].values())
        assert plan["would_create"]["master_commercial_mappings"] == 2
        assert plan["skipped"]["commercial_no_master_product"] == 0

    def test_canonical_descriptions_consider_planned_products_and_descriptions(
            self, sources, db):
        plan = plan_against(db)
        descriptions = plan["stages"]["descriptions"]
        assert descriptions["products_examined"] == plan["would_create"]["master_products"]
        assert plan["would_create_descriptions_by_role"]["canonical"] >= 1

    def test_snapshots_are_built_for_the_mappings_commercial_would_create(self, sources, db):
        plan = plan_against(db)
        snapshot = plan["source_snapshot"]
        assert snapshot["mappings_after_seed"] == 2
        assert snapshot["with_snapshot_after_seed"] == 2
        assert snapshot["coverage_complete"] is True

    def test_a_rolled_back_rehearsal_would_have_seen_none_of_this(self, sources, db):
        """The old behaviour, for contrast: each stage alone on an empty DB."""
        report, _review = asyncio.run(commercial_stage.run_seed(SimpleNamespace(
            dry_run=True, store=None, verbose=False, pilot_target=TARGET)))
        assert report["mappings_created"] == 0
        assert report["skipped_no_master_product"] == report["distinct_product_store_pairs"]


class TestThePlanIsInternallyConsistent:

    def test_every_consistency_check_holds(self, sources, db):
        plan = plan_against(db)
        assert all(plan["validation"]["consistency"].values()), plan["validation"]["consistency"]

    def test_totals_after_are_existing_plus_created(self, sources, db):
        plan = plan_against(db)
        for table, total in plan["expected_totals_after"].items():
            assert total == plan["existing_rows"][table] + plan["would_create"][table]

    def test_descriptions_are_source_rows_plus_canonical_rows(self, sources, db):
        plan = plan_against(db)
        by_role = plan["would_create_descriptions_by_role"]
        assert plan["would_create"]["master_product_descriptions"] == \
            by_role["source_and_invoice"] + by_role["canonical"]

    def test_skips_are_counted_not_seeded(self, sources, db):
        plan = plan_against(db)
        assert plan["skipped"]["unresolved_identity_candidates"] == 1
        assert plan["skipped"]["commercial_store_unresolved"] == 1
        assert plan["source"]["identity_candidates"] == \
            plan["source"]["identity_candidates_seedable"] \
            + plan["skipped"]["unresolved_identity_candidates"] \
            + plan["skipped"]["conflicting_identity_candidates"]

    def test_nothing_is_approved_proposed_or_reviewed(self, sources, db):
        plan = plan_against(db)
        assert plan["would_create"]["master_commercial_reviews"] == 0
        assert plan["commercial"]["approval_state"] == {STATE_REVIEW_REQUIRED: 2}
        assert plan["legacy_tables_written"] == []


class TestThePlanMatchesTheRealSeed:
    """The same starting state, planned and then actually written."""

    def test_counts_match_what_the_real_seed_writes(self, sources, db):
        plan = plan_against(db)
        run_real_seed(db)
        assert committed_counts(db) == plan["would_create"]

    def test_every_planned_row_is_the_row_the_real_seed_writes(self, sources, db):
        plan_rows = _planned_rows(db)
        run_real_seed(db)
        key_of = {p.id: p.canonical_key for p in db.rows(MasterProduct)}
        for model in TABLES[:-1]:
            written = sorted(_row_signature(o, model, key_of) for o in db.rows(model))
            assert written == plan_rows[model.__tablename__], model.__tablename__

    def test_review_positions_and_snapshots_match(self, sources, db):
        plan = plan_against(db)
        run_real_seed(db)
        mappings = db.rows(MasterCommercialMapping)
        assert dict(Counter(review_status(m) for m in mappings)) == \
            plan["commercial"]["review_status"]
        assert sum(1 for m in mappings if m.source_snapshot.get("source_rows")) == \
            plan["source_snapshot"]["with_snapshot_after_seed"]
        assert sum(1 for p in db.rows(MasterProduct) if p.canonical_description) == \
            plan["would_update"]["master_products.canonical_description"]

    def test_after_the_real_seed_the_plan_would_create_nothing(self, sources, db):
        run_real_seed(db)
        plan = plan_against(db)
        assert set(plan["would_create"].values()) == {0}
        assert plan["existing_rows"] == committed_counts(db)
        assert plan["ready_for_live_seed"] is True


class TestTheCleanPilotBaseline:
    """
    The pilot starts with no legacy product_case_mappings. A governed mapping
    is admissible only to dissent, so where none exists there is nothing to
    dissent with: the local 410 READY / 4 CONFLICT carried two conflicts that
    came from 98 local legacy rows, and the clean pilot's 412 / 2 is correct.
    Source-internal conflicts come from the reference data and stay.
    """

    MICHELOB = "018200967214"
    TWELVE_OR_TWENTY_FOUR = "652682012217"

    def _rows(self):
        return [
            distributor(source_row=60, raw_identifier=self.MICHELOB,
                        description="MICHELOB ULTRA 18/12 CAN",
                        commercial_units_statement="1.0",
                        commercial_statement_evidence="Sheet1 items/case column"),
            distributor(source_row=70, raw_identifier=self.TWELVE_OR_TWENTY_FOUR,
                        description="SELTZER 12PK", commercial_units_statement="12.0",
                        commercial_statement_evidence="Sheet1 items/case column"),
            distributor(source_sheet="Sheet2", source_row=71,
                        raw_identifier=self.TWELVE_OR_TWENTY_FOUR, description="SELTZER 12PK",
                        commercial_units_statement="24.0",
                        commercial_statement_evidence="Sheet2 items/case column"),
        ]

    def _govern(self, db, upc: str, *units: int) -> None:
        from app.models.product_case_mapping import ProductCaseMapping

        for value in units:
            db.committed[ProductCaseMapping].append(
                SimpleNamespace(item_code=upc[:-1], units_per_case=value))

    def test_without_legacy_mappings_no_conflict_is_manufactured(self, sources, db):
        sources.rows = self._rows
        plan = plan_against(db)
        commercial = plan["commercial"]
        assert commercial["legacy_case_mappings_read"] == 0
        assert commercial["review_status"] == {"READY_FOR_REVIEW": 1, "CONFLICT": 1}
        assert commercial["conflict_origin"] == {"source_internal": 1, "legacy_dissent": 0}

    def test_a_source_internal_conflict_stays_conflict(self, sources, db):
        sources.rows = self._rows
        plan_rows = plan_against(db)["commercial"]
        assert plan_rows["commercial_unit_basis"]["CONFLICT"] == 1
        run_real_seed(db)
        conflicted = [m for m in db.rows(MasterCommercialMapping)
                      if m.commercial_unit_basis == "CONFLICT"]
        assert [m.pdi_item_code for m in conflicted] == [self.TWELVE_OR_TWENTY_FOUR[:-1]]
        assert conflicted[0].units_accounted_for is None

    def test_legacy_dissent_appears_only_where_legacy_data_exists(self, sources, db):
        """The local environment's extra conflict, reproduced by its cause."""
        sources.rows = self._rows
        self._govern(db, self.MICHELOB, 1, 18)
        plan = plan_against(db)
        assert plan["commercial"]["review_status"] == {"CONFLICT": 2}
        assert plan["commercial"]["conflict_origin"] == {
            "source_internal": 1, "legacy_dissent": 1}

    def test_legacy_data_is_only_read_never_written(self, sources, db):
        from app.models.product_case_mapping import ProductCaseMapping

        sources.rows = self._rows
        self._govern(db, self.MICHELOB, 1, 18)
        plan_against(db)
        run_real_seed(db)
        assert len(db.rows(ProductCaseMapping)) == 2
        assert not any("ProductCaseMapping" in entry for entry in db.writes)


class TestBlockersAreReportedBeforeAnyWrite:

    def test_no_store_resolving_blocks_the_live_seed(self, sources, db):
        sources.index["value"] = store_index("some-other-code")
        plan = plan_against(db)
        assert plan["ready_for_live_seed"] is False
        assert any("resolved to a store" in b for b in plan["validation"]["blocking"])
        assert plan["would_create"]["master_commercial_mappings"] == 0

    def test_an_unreadable_store_index_blocks_the_live_seed(self, sources, db, monkeypatch):
        monkeypatch.setattr(planner, "_load_store_index",
                            lambda: (StoreIdentifierIndex([]), "OperationalError"))
        plan = plan_against(db)
        assert any("could not be read" in b for b in plan["validation"]["blocking"])

    def test_a_value_too_long_for_its_column_would_fail(self):
        planned = _empty_planned()
        planned["master_products"] = [{
            "canonical_key": "x" * 200, "canonical_upc": None, "identity_basis": "UPC_A",
            "identity_state": "AUTO_MATCHED", "canonical_description": None}]
        result = planner.validate(planned, planner.ExistingState(), {}, store_index(),
                                  {"skipped_store_unresolved": 0})
        assert any("longer than column" in line for line in result["would_fail"])

    def test_a_missing_required_value_would_fail(self):
        planned = _empty_planned()
        planned["master_products"] = [{
            "canonical_key": "upc12:1", "canonical_upc": None, "identity_basis": None,
            "identity_state": "AUTO_MATCHED", "canonical_description": None}]
        result = planner.validate(planned, planner.ExistingState(), {}, store_index(),
                                  {"skipped_store_unresolved": 0})
        assert any("NOT NULL" in line for line in result["would_fail"])

    def test_a_duplicate_unique_key_would_fail(self):
        product = {"canonical_key": "upc12:1", "canonical_upc": None, "identity_basis": "UPC_A",
                   "identity_state": "AUTO_MATCHED", "canonical_description": None}
        planned = _empty_planned()
        planned["master_products"] = [product, dict(product)]
        result = planner.validate(planned, planner.ExistingState(), {}, store_index(),
                                  {"skipped_store_unresolved": 0})
        assert any("duplicate uq_master_products_canonical_key" in line
                   for line in result["would_fail"])

    def test_a_reference_to_an_unknown_product_or_store_would_fail(self):
        planned = _empty_planned()
        planned["master_commercial_mappings"] = [{
            "product_id": "planned:nowhere", "store_id": "not-a-store", "pdi_item_code": "1",
            "commercial_unit_basis": "UNKNOWN", "units_accounted_for": None,
            "case_cost": None, "cost_basis": None, "approval_state": STATE_REVIEW_REQUIRED,
            "evidence": {}, "source_system": "distributor_price_sheet"}]
        result = planner.validate(planned, planner.ExistingState(), {}, store_index(),
                                  {"skipped_store_unresolved": 0})
        joined = " ".join(result["would_fail"])
        assert "unknown product reference" in joined and "unknown store reference" in joined


class TestTheRealRunIsUnchanged:

    def test_the_write_mode_runs_the_stages_and_never_plans(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pilot_seed, "resolve_seed_target", lambda *a, **k: TARGET)
        monkeypatch.setattr(pilot_seed, "ROOT", tmp_path)
        monkeypatch.setattr(pilot_seed, "REPORT", tmp_path / "report.json")
        calls = []

        async def fake_stages(target, dry_run, store):
            calls.append((target, dry_run, store))
            return {"identity": {}}

        async def must_not_plan(*_args, **_kwargs):
            raise AssertionError("the write mode planned instead of writing")

        monkeypatch.setattr(pilot_seed, "run_stages", fake_stages)
        monkeypatch.setattr(planner, "plan_pipeline", must_not_plan)
        monkeypatch.setattr(sys, "argv", [
            "pilot_seed.py", "--pilot-seed", "--expect-database", "postgres",
            "--expect-host-contains", "supabase"])
        pilot_seed.main()
        assert calls == [(TARGET, False, None)]
        assert json.loads((tmp_path / "report.json").read_text())["dry_run"] is False

    def test_the_stage_order_and_entry_points_are_as_before(self):
        assert pilot_seed.STAGES == (
            ("identity", "scripts.seed_product_master_identity", "run_seed"),
            ("commercial", "scripts.seed_product_master_commercial", "run_seed"),
            ("descriptions", "scripts.seed_product_master_descriptions", "run"),
            ("source_snapshot", "scripts.backfill_commercial_source_snapshot", "run"),
        )

    def test_the_real_seed_commits_each_stage_and_writes_only_the_master(self, sources, db):
        run_real_seed(db)
        assert db.writes.count("commit") == 4
        written = {entry.split()[1] for entry in db.writes if entry.startswith("add ")}
        assert written <= {"MasterProduct", "MasterProductIdentifier",
                           "MasterProductDescription", "MasterPackComposition",
                           "MasterCommercialMapping"}
        assert not any(entry.startswith("execute") for entry in db.writes)

    def test_seeded_mappings_await_review_and_carry_evidence(self, sources, db):
        run_real_seed(db)
        mappings = db.rows(MasterCommercialMapping)
        assert mappings
        assert {m.approval_state for m in mappings} == {STATE_REVIEW_REQUIRED}
        assert all(m.source_snapshot.get("source_rows") for m in mappings)
        assert any(d.role == DESC_CANONICAL for d in db.rows(MasterProductDescription))


# ---------------------------------------------------------------------------


def _minimal_plan() -> dict:
    empty: dict = {}
    return {
        "source": empty, "existing_rows": empty, "would_create": empty,
        "would_create_descriptions_by_role": empty, "would_update": empty,
        "expected_totals_after": empty, "skipped": empty, "commercial": empty,
        "source_snapshot": empty,
        "validation": {"would_fail": [], "warnings": [], "blocking": []},
        "ready_for_live_seed": True,
    }


def _empty_planned() -> dict[str, list]:
    return {table: [] for table in planner.MODELS} | {"master_commercial_reviews": []}


REFERENCE_COLUMNS = {"product_id", "parent_product_id", "child_product_id"}


def _row_signature(obj, model, key_of) -> str:
    values = {}
    for column in model.__table__.columns:
        if column.key in ("id", "created_at", "updated_at"):
            continue
        value = getattr(obj, column.key)
        if column.key in REFERENCE_COLUMNS:
            value = key_of.get(value, value)
        values[column.key] = value
    return json.dumps(values, sort_keys=True, default=str)


def _planned_rows(db) -> dict[str, list[str]]:
    """The plan's rows in the same signature the committed rows are read in."""
    captured: dict = {}
    original = planner.summarise

    def capture(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    planner.summarise = capture
    try:
        plan_against(db)
    finally:
        planner.summarise = original

    planned = captured["planned"]
    snapshots = {id(m): s for m, s in captured["snapshots"]}
    key_of = {pid: key for key, pid in captured["product_ids"].items()}
    canonical = {row["product_id"]: row["description"] for row in captured["canonical_rows"]}

    def as_object(table, row):
        obj = SimpleNamespace(**row)
        if table == "master_products":
            obj.canonical_description = canonical.get(
                planner.PLANNED_ID_PREFIX + row["canonical_key"])
        return obj

    out: dict[str, list[str]] = {}
    for model in TABLES[:-1]:
        table = model.__tablename__
        objects = [as_object(table, row) for row in planned[table]]
        for obj in objects:
            for column in model.__table__.columns:
                if not hasattr(obj, column.key):
                    setattr(obj, column.key, None)
        if table == "master_commercial_mappings":
            # Planned mapping rows are built in the same order as the rows.
            for obj, (mapping, _p) in zip(objects, captured["planned_mapping_rows"], strict=True):
                obj.source_snapshot = snapshots.get(id(mapping), {})
        out[table] = sorted(_row_signature(o, model, key_of) for o in objects)
    return out


# ---------------------------------------------------------------------------
# Failure propagation — a failed stage stops the seed, and the seed says so
# ---------------------------------------------------------------------------

CALLS: list[str] = []


def _stage(name: str, outcome):
    async def run(_args):
        CALLS.append(name)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return run


fake_identity_errors = _stage("identity", {"errors": ["synthetic failure"]})
fake_identity_error = _stage("identity", {"error": "synthetic failure"})
fake_identity_raises = _stage("identity", RuntimeError("stage blew up"))
fake_identity_ok = _stage("identity", {"errors": []})
fake_commercial_ok = _stage("commercial", ({"errors": []}, []))
fake_descriptions_ok = _stage("descriptions", {"errors": []})
fake_snapshot_ok = _stage("source_snapshot", {"error": None})

HERE = __name__


def _stages(identity: str) -> tuple:
    return (
        ("identity", HERE, identity),
        ("commercial", HERE, "fake_commercial_ok"),
        ("descriptions", HERE, "fake_descriptions_ok"),
        ("source_snapshot", HERE, "fake_snapshot_ok"),
    )


@pytest.fixture
def calls():
    CALLS.clear()
    yield CALLS
    CALLS.clear()


def _write_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pilot_seed, "resolve_seed_target", lambda *a, **k: TARGET)
    monkeypatch.setattr(pilot_seed, "ROOT", tmp_path)
    monkeypatch.setattr(pilot_seed, "REPORT", tmp_path / "report.json")
    monkeypatch.setattr(sys, "argv", [
        "pilot_seed.py", "--pilot-seed", "--expect-database", "postgres",
        "--expect-host-contains", "supabase"])


class TestAFailedStageStopsTheSeed:

    @pytest.mark.parametrize("identity", ["fake_identity_errors", "fake_identity_error"])
    def test_later_stages_are_not_called(self, monkeypatch, calls, identity):
        monkeypatch.setattr(pilot_seed, "STAGES", _stages(identity))
        results = asyncio.run(pilot_seed.run_stages(TARGET, False, None))
        assert calls == ["identity"]
        assert list(results) == ["identity"]
        assert pilot_seed.stage_failure(results["identity"]) == "synthetic failure"

    def test_the_cli_reports_failure_and_exits_non_zero(
            self, monkeypatch, tmp_path, calls, capsys):
        monkeypatch.setattr(pilot_seed, "STAGES", _stages("fake_identity_errors"))
        _write_mode(monkeypatch, tmp_path)
        with pytest.raises(SystemExit) as exit_info:
            pilot_seed.main()
        assert exit_info.value.code == 1
        assert calls == ["identity"]
        out = capsys.readouterr().out
        assert "STOPPING: stage 'identity' failed — synthetic failure" in out
        assert "SEED FAILED at stage 'identity'" in out
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["failed_stage"] == "identity"
        assert report["not_run"] == ["commercial", "descriptions", "source_snapshot"]
        assert report["stages"]["identity"]["errors"] == ["synthetic failure"]

    def test_an_exception_in_a_stage_is_not_swallowed(self, monkeypatch, tmp_path, calls):
        monkeypatch.setattr(pilot_seed, "STAGES", _stages("fake_identity_raises"))
        _write_mode(monkeypatch, tmp_path)
        with pytest.raises(RuntimeError, match="stage blew up"):
            pilot_seed.main()
        assert calls == ["identity"]

    def test_empty_error_fields_are_success_and_every_stage_runs(
            self, monkeypatch, tmp_path, calls, capsys):
        monkeypatch.setattr(pilot_seed, "STAGES", _stages("fake_identity_ok"))
        _write_mode(monkeypatch, tmp_path)
        pilot_seed.main()                         # no SystemExit
        assert calls == ["identity", "commercial", "descriptions", "source_snapshot"]
        assert "Seed completed" in capsys.readouterr().out
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["failed_stage"] is None and report["not_run"] == []


class TestARealIdentityFailure:
    """The real stages, with the connection dropping during the identity write."""

    @pytest.fixture
    def failing_db(self, monkeypatch):
        import app.database.session as session_module

        def make(fail_on: str) -> FakeSeedDatabase:
            database = FakeSeedDatabase(fail_on=fail_on)
            monkeypatch.setattr(session_module, "get_session_factory",
                                database.get_session_factory)
            return database
        return make

    @pytest.fixture
    def commercial_spy(self, monkeypatch):
        called = []
        original = commercial_stage.run_seed

        async def spy(args):
            called.append(args)
            return await original(args)

        monkeypatch.setattr(commercial_stage, "run_seed", spy)
        return called

    @pytest.mark.parametrize("fail_on", [
        "bulk-insert MasterProduct",            # while inserting products
        "bulk-insert MasterProductIdentifier",  # after products, before commit
    ])
    def test_identity_failure_stops_commercial_and_commits_nothing(
            self, sources, failing_db, commercial_spy, fail_on):
        db = failing_db(fail_on)
        results = asyncio.run(pilot_seed.run_stages(TARGET, False, None))
        assert list(results) == ["identity"]
        assert "connection was closed" in pilot_seed.stage_failure(results["identity"])
        assert commercial_spy == []
        assert committed_counts(db) == dict.fromkeys(committed_counts(db), 0)
        assert "commit" not in db.writes

    def test_the_cli_exits_non_zero(self, sources, failing_db, commercial_spy,
                                    monkeypatch, tmp_path):
        failing_db("bulk-insert MasterProduct")
        _write_mode(monkeypatch, tmp_path)
        with pytest.raises(SystemExit) as exit_info:
            pilot_seed.main()
        assert exit_info.value.code == 1
        assert commercial_spy == []


class TestProductsAreInsertedInOneBatch:

    def test_one_bulk_insert_and_no_per_product_flush(self, sources, db):
        plan = plan_against(db)
        run_real_seed(db)
        expected = plan["would_create"]["master_products"]
        product_writes = [w for w in db.writes if "MasterProduct" in w.split()[1:2]
                          or w.startswith("bulk-insert MasterProduct ")]
        assert product_writes == [f"bulk-insert MasterProduct x{expected}"]
        assert len(db.rows(MasterProduct)) == expected

    def test_identifiers_and_packs_point_at_the_inserted_products(self, sources, db):
        run_real_seed(db)
        ids = {p.id for p in db.rows(MasterProduct)}
        assert ids and all(i.product_id in ids for i in db.rows(MasterProductIdentifier))
        assert all(d.product_id in ids for d in db.rows(MasterProductDescription))
        for pack in db.rows(MasterPackComposition):
            assert pack.parent_product_id in ids and pack.child_product_id in ids
        assert all(m.product_id in ids for m in db.rows(MasterCommercialMapping))

    def test_nothing_new_is_inserted_on_a_second_run(self, sources, db):
        run_real_seed(db)
        before = committed_counts(db)
        writes_before = len(db.writes)
        run_real_seed(db)
        assert committed_counts(db) == before
        assert not any(w.startswith("bulk-insert") for w in db.writes[writes_before:])
