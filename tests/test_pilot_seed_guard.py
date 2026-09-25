"""
Tests — the pilot seed guard.

The seed commands populate a developer's own Postgres. Exactly one path
leads anywhere else, and these tests pin its four conditions: it was asked
for, the database is the one the operator named, the schema is the revision
the seed was written for, and the tables it writes exist.

Everything here is offline. The guard's refusals are all reached before it
opens a connection, which is the point — a wrong target never gets touched.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.pilot_seed_guard import (
    EXPECTED_REVISION,
    TARGET_LOCAL,
    TARGET_PILOT,
    SeedTarget,
    resolve_seed_target,
    seed_target_host,
)

ROOT = Path(__file__).resolve().parent.parent
PILOT_SEED = ROOT / "scripts" / "pilot_seed.py"
GUARD = ROOT / "scripts" / "pilot_seed_guard.py"

PILOT_URL = "postgresql+asyncpg://u:p@db.abcdefgh.supabase.co:5432/postgres"
PILOT_SYNC = "postgresql+psycopg2://u:p@db.abcdefgh.supabase.co:5432/postgres"
LOCAL_URL = "postgresql+asyncpg://u:p@localhost:5432/invoice_db"
LOCAL_SYNC = "postgresql+psycopg2://u:p@localhost:5432/invoice_db"


def opts(**overrides) -> SimpleNamespace:
    base = {"pilot_seed": False, "expect_database": None, "expect_host_contains": None}
    base.update(overrides)
    return SimpleNamespace(**base)


def asked_for_pilot(**overrides) -> SimpleNamespace:
    return opts(**{"pilot_seed": True, "expect_database": "postgres",
                   "expect_host_contains": "supabase", **overrides})


class TestTheDefaultIsLocalOnly:
    """Without the opt-in, nothing has changed: local or nothing."""

    def test_a_local_database_is_permitted(self):
        target = resolve_seed_target(LOCAL_URL, LOCAL_SYNC, opts())
        assert target.target == TARGET_LOCAL

    def test_a_remote_database_is_refused_when_the_pilot_was_not_requested(self):
        with pytest.raises(SystemExit) as exit_info:
            resolve_seed_target(PILOT_URL, PILOT_SYNC, opts())
        assert "REFUSING TO SEED" in str(exit_info.value)

    def test_the_refusal_names_the_missing_opt_in_rather_than_a_workaround(self):
        with pytest.raises(SystemExit) as exit_info:
            resolve_seed_target(PILOT_URL, PILOT_SYNC, opts())
        assert "--pilot-seed" in str(exit_info.value)


class TestTheOptInMustNameItsTarget:
    """`--pilot-seed` alone is not enough — a typo has to fail, not proceed."""

    @pytest.mark.parametrize("missing", [
        {"expect_database": None},
        {"expect_host_contains": None},
        {"expect_database": None, "expect_host_contains": None},
    ])
    def test_the_opt_in_without_a_stated_identity_is_refused(self, missing):
        with pytest.raises(SystemExit) as exit_info:
            resolve_seed_target(PILOT_URL, PILOT_SYNC, asked_for_pilot(**missing))
        assert "REFUSING TO SEED" in str(exit_info.value)

    def test_a_host_that_does_not_match_the_stated_fragment_is_refused(self):
        other = "postgresql+asyncpg://u:p@prod.internal.example:5432/postgres"
        with pytest.raises(SystemExit) as exit_info:
            resolve_seed_target(other, other, asked_for_pilot())
        assert "supabase" in str(exit_info.value)

    def test_an_arbitrary_production_host_is_not_seedable_by_naming_it(self):
        """
        Naming a host is necessary, not sufficient: the guard still has to
        reach that database and confirm its name and revision. Here the
        connection is what fails, and a failure is a stop.
        """
        prod = "postgresql+asyncpg://u:p@prod-db.amazonaws.com:5432/app"
        prod_sync = "postgresql+psycopg2://u:p@prod-db.amazonaws.com:5432/app"
        with pytest.raises(Exception) as exc_info:  # noqa: PT011 — any failure is a stop
            resolve_seed_target(prod, prod_sync,
                                asked_for_pilot(expect_database="app",
                                                expect_host_contains="amazonaws"))
        assert not isinstance(exc_info.value, SeedTarget)

    def test_the_opt_in_against_a_local_host_is_refused(self):
        with pytest.raises(SystemExit) as exit_info:
            resolve_seed_target(LOCAL_URL, LOCAL_SYNC,
                                asked_for_pilot(expect_host_contains="localhost"))
        assert "local" in str(exit_info.value).lower()


class TestIdentityAndRevisionAreVerifiedBeforeWriting:
    """The two checks that need the database, with the database stubbed."""

    def _patch(self, monkeypatch, *, database: str, revision: str | None,
               problems: list[str] | None = None):
        import scripts.pilot_seed_guard as guard

        monkeypatch.setattr(guard, "_identity",
                            lambda dsn: ("db.abcdefgh.supabase.co", database, revision))
        monkeypatch.setattr(guard, "_check_schema", lambda dsn: problems or [])

    def test_the_verified_pilot_is_permitted(self, monkeypatch):
        self._patch(monkeypatch, database="postgres", revision=EXPECTED_REVISION)
        target = resolve_seed_target(PILOT_URL, PILOT_SYNC, asked_for_pilot())
        assert target.target == TARGET_PILOT
        assert target.database == "postgres"
        assert target.revision == EXPECTED_REVISION

    def test_a_different_database_name_is_refused(self, monkeypatch):
        self._patch(monkeypatch, database="something_else", revision=EXPECTED_REVISION)
        with pytest.raises(SystemExit) as exit_info:
            resolve_seed_target(PILOT_URL, PILOT_SYNC, asked_for_pilot())
        assert "something_else" in str(exit_info.value)

    @pytest.mark.parametrize("revision", ["0023", "0021", None])
    def test_an_unexpected_alembic_revision_is_refused(self, monkeypatch, revision):
        self._patch(monkeypatch, database="postgres", revision=revision)
        with pytest.raises(SystemExit) as exit_info:
            resolve_seed_target(PILOT_URL, PILOT_SYNC, asked_for_pilot())
        message = str(exit_info.value)
        assert "REFUSING TO SEED" in message and EXPECTED_REVISION in message

    def test_a_missing_product_master_table_is_refused(self, monkeypatch):
        self._patch(monkeypatch, database="postgres", revision=EXPECTED_REVISION,
                    problems=["missing table master_commercial_mappings"])
        with pytest.raises(SystemExit) as exit_info:
            resolve_seed_target(PILOT_URL, PILOT_SYNC, asked_for_pilot())
        assert "master_commercial_mappings" in str(exit_info.value)

    def test_a_missing_evidence_column_is_refused(self, monkeypatch):
        self._patch(monkeypatch, database="postgres", revision=EXPECTED_REVISION,
                    problems=["missing column master_commercial_mappings.source_snapshot"])
        with pytest.raises(SystemExit) as exit_info:
            resolve_seed_target(PILOT_URL, PILOT_SYNC, asked_for_pilot())
        assert "source_snapshot" in str(exit_info.value)


class TestTheStagesCannotBeTalkedIntoARemoteWrite:
    """
    Each seed stage asks the guard which host it may write to. Only a
    `SeedTarget` the guard itself produced unlocks the pilot; nothing a
    stage's own command line can carry does.
    """

    def test_a_stage_without_a_verified_target_falls_back_to_the_local_guard(self):
        assert seed_target_host(LOCAL_URL, opts()) == "localhost"

    def test_a_stage_without_a_verified_target_refuses_a_remote_host(self):
        with pytest.raises(SystemExit):
            seed_target_host(PILOT_URL, opts())

    def test_the_opt_in_flags_alone_do_not_unlock_a_stage(self):
        """The flags are parsed by the orchestrator, not honoured by a stage."""
        with pytest.raises(SystemExit):
            seed_target_host(PILOT_URL, asked_for_pilot())

    def test_a_forged_target_object_does_not_unlock_a_stage(self):
        forged = SimpleNamespace(target=TARGET_PILOT, host="db.abcdefgh.supabase.co")
        with pytest.raises(SystemExit):
            seed_target_host(PILOT_URL, opts(pilot_target=forged))

    def test_a_verified_target_unlocks_the_stage(self):
        verified = SeedTarget(TARGET_PILOT, "db.abcdefgh.supabase.co", "postgres", "0024")
        assert seed_target_host(PILOT_URL, opts(pilot_target=verified)) == \
            "db.abcdefgh.supabase.co"

    def test_a_local_seed_target_does_not_unlock_a_remote_stage(self):
        local = SeedTarget(TARGET_LOCAL, "localhost", "", None)
        with pytest.raises(SystemExit):
            seed_target_host(PILOT_URL, opts(pilot_target=local))


class TestTheGuardHasNoWayAround:
    def test_there_is_no_override_flag(self):
        sources = GUARD.read_text() + PILOT_SEED.read_text()
        for forbidden in ("--force", "--allow-remote", "--no-guard", "--skip-guard",
                          "--override", "--i-know-what", "--unsafe", "--yes"):
            assert forbidden not in sources, forbidden

    def test_the_local_only_guard_is_still_present_and_unmodified(self):
        """Phase E adds a verified path; it does not remove the old refusal."""
        from scripts.seed_product_master_identity import assert_local_database

        with pytest.raises(SystemExit):
            assert_local_database(PILOT_URL)
        assert assert_local_database(LOCAL_URL) == "localhost"


class TestTheSeedWritesOnlyTheMaster:
    """
    What the pilot seed is allowed to touch. These read the orchestrator and
    its stages rather than a database, so they hold before it is ever run.
    """

    FORBIDDEN_TABLES = (
        "product_case_mappings", "invoices", "invoice_items", "documents",
        "processing_logs", "master_commercial_reviews",
    )

    def _stage_sources(self) -> dict[str, str]:
        import scripts.pilot_seed as orchestrator

        return {module: (ROOT / (module.replace(".", "/") + ".py")).read_text()
                for _, module, _ in orchestrator.STAGES}

    @pytest.mark.parametrize("table", FORBIDDEN_TABLES)
    def test_no_stage_writes_a_table_outside_the_master(self, table):
        writing = re.compile(
            rf"\b(insert\s+into|update|delete\s+from)\s+{table}\b", re.IGNORECASE)
        for module, source in self._stage_sources().items():
            assert not writing.search(source), f"{module} writes {table}"

    @pytest.mark.parametrize("model", [
        "ProductCaseMapping", "Invoice", "InvoiceItem", "Document",
        "ProcessingLog", "MasterCommercialReview",
    ])
    def test_no_stage_constructs_a_model_outside_the_master(self, model):
        constructing = re.compile(rf"(?<!Master){model}\s*\(")
        for module, source in self._stage_sources().items():
            assert not constructing.search(source), f"{module} constructs {model}"

    def test_no_stage_approves_rejects_or_proposes(self):
        for module, source in self._stage_sources().items():
            for forbidden in ("STATE_APPROVED", "STATE_REJECTED", "STATE_PENDING",
                              "approved_by", "proposed_by", ".approve(", ".reject(",
                              ".propose("):
                assert forbidden not in source, f"{module} mentions {forbidden}"

    def test_the_orchestrator_runs_identity_before_the_stages_that_depend_on_it(self):
        import scripts.pilot_seed as orchestrator

        names = [name for name, _, _ in orchestrator.STAGES]
        assert names.index("identity") < names.index("commercial")
        assert names.index("commercial") < names.index("source_snapshot")

    def test_the_orchestrator_stops_at_the_first_failing_stage(self):
        """A stage that rolled back must not be followed by stages assuming its rows."""
        source = PILOT_SEED.read_text()
        tree = ast.parse(source)
        breaks = [node for node in ast.walk(tree) if isinstance(node, ast.Break)]
        assert breaks, "the stage loop never breaks on error"
        assert 'outcome.get("error")' in source

    def test_the_orchestrator_supports_a_rehearsal(self):
        assert "--dry-run" in PILOT_SEED.read_text()
