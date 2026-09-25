"""
Seed target guard — scripts/pilot_seed_guard.py

Decides, for every Product Master seed command, which database it is
allowed to write to.

Default is local development and nothing else. Seeding the pilot is a
deliberate, one-time operation and therefore has to be asked for
explicitly AND prove it is pointed at the intended database:

    --pilot-seed --expect-database <name> --expect-host-contains <fragment>

All four conditions must hold before a pilot write is permitted:

  1. `--pilot-seed` was passed;
  2. the connected database's identity matches what the operator said to
     expect — they name it, this verifies it, so a mistyped URL cannot
     quietly seed something else;
  3. the Alembic revision is exactly the one the seed was built for;
  4. the Product Master schema the seed writes to actually exists.

There is deliberately no flag that skips these. A host that matches
neither the local allow-list nor the operator's stated expectation is
refused, which is what stops an arbitrary production database being
seeded by accident.

Nothing here prints a URL, a password or any credential.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

# The revision this seed generation was written against. A pilot seed
# against a different schema is refused rather than adapted.
EXPECTED_REVISION = "0024"

# Tables the seed writes; all must exist before it runs.
REQUIRED_TABLES = (
    "master_products",
    "master_product_identifiers",
    "master_product_descriptions",
    "master_pack_compositions",
    "master_commercial_mappings",
)
REQUIRED_COLUMNS = (
    ("master_commercial_mappings", "source_snapshot"),
    ("master_commercial_mappings", "proposed_units_accounted_for"),
)

TARGET_LOCAL = "local"
TARGET_PILOT = "pilot"


@dataclass(frozen=True)
class SeedTarget:
    target: str
    host: str
    database: str
    revision: str | None


def add_pilot_arguments(parser) -> None:
    """The opt-in flags. Absent, a command stays local-only."""
    parser.add_argument(
        "--pilot-seed", action="store_true",
        help="Permit writing to the pilot database. Requires --expect-database "
             "and --expect-host-contains, which are verified before any write.",
    )
    parser.add_argument("--expect-database", default=None,
                        help="The database name you expect to be connected to.")
    parser.add_argument("--expect-host-contains", default=None,
                        help="A fragment the host must contain, e.g. 'supabase'.")


def _identity(dsn: str) -> tuple[str, str, str | None]:
    """Connect read-only and report who we are actually talking to."""
    import psycopg2

    connection = psycopg2.connect(dsn, connect_timeout=10)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT current_database()")
        database = cursor.fetchone()[0]
        cursor.execute("SELECT version_num FROM alembic_version")
        row = cursor.fetchone()
        revision = row[0] if row else None
    finally:
        connection.close()
    host = urlparse(dsn).hostname or ""
    return host, database, revision


def _check_schema(dsn: str) -> list[str]:
    import psycopg2

    problems: list[str] = []
    connection = psycopg2.connect(dsn, connect_timeout=10)
    connection.set_session(readonly=True, autocommit=True)
    try:
        cursor = connection.cursor()
        for table in REQUIRED_TABLES:
            cursor.execute(
                "SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
            if not cursor.fetchone()[0]:
                problems.append(f"missing table {table}")
        for table, column in REQUIRED_COLUMNS:
            cursor.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s", (table, column))
            if not cursor.fetchone()[0]:
                problems.append(f"missing column {table}.{column}")
    finally:
        connection.close()
    return problems


def resolve_seed_target(async_url: str, sync_url: str, args) -> SeedTarget:
    """
    Decide where this command may write, or exit.

    Returns the verified target; never returns for a database it is not
    allowed to touch.
    """
    host = (urlparse(async_url.replace("postgresql+asyncpg://", "postgresql://"))
            .hostname or "").lower()
    dsn = sync_url.replace("postgresql+psycopg2://", "postgresql://")
    wants_pilot = bool(getattr(args, "pilot_seed", False))

    if not wants_pilot:
        if host not in LOCAL_HOSTS:
            sys.exit(
                f"REFUSING TO SEED: host {host!r} is not local and --pilot-seed was not "
                "given. This command seeds local development databases unless a pilot "
                "seed is explicitly requested and verified."
            )
        return SeedTarget(TARGET_LOCAL, host, "", None)

    expected_database = getattr(args, "expect_database", None)
    expected_fragment = (getattr(args, "expect_host_contains", None) or "").lower()
    if not expected_database or not expected_fragment:
        sys.exit(
            "REFUSING TO SEED: --pilot-seed requires --expect-database and "
            "--expect-host-contains. Naming the target is what makes a mistyped "
            "connection string fail instead of seeding the wrong database."
        )
    if host in LOCAL_HOSTS:
        sys.exit(
            f"REFUSING TO SEED: --pilot-seed was given but the host is local ({host!r}). "
            "Run without --pilot-seed to seed local development."
        )
    if expected_fragment not in host:
        sys.exit(
            f"REFUSING TO SEED: host does not contain {expected_fragment!r}. "
            "The connected database is not the one you said to expect."
        )

    actual_host, database, revision = _identity(dsn)
    if database != expected_database:
        sys.exit(
            f"REFUSING TO SEED: connected to database {database!r}, expected "
            f"{expected_database!r}."
        )
    if revision != EXPECTED_REVISION:
        sys.exit(
            f"REFUSING TO SEED: Alembic revision is {revision!r}, expected "
            f"{EXPECTED_REVISION!r}. Migrate first; this seed is written for that schema."
        )
    problems = _check_schema(dsn)
    if problems:
        sys.exit("REFUSING TO SEED: Product Master schema incomplete — "
                 + "; ".join(problems))

    return SeedTarget(TARGET_PILOT, actual_host, database, revision)


def seed_target_host(async_url: str, args) -> str:
    """
    The host a seed stage is permitted to write to.

    Default path: the local-only guard, unchanged and with no override.
    The pilot path is only available when `resolve_seed_target` has already
    verified this connection — the orchestrator attaches the verified
    `SeedTarget` to `args`, and nothing on any stage's own command line can
    produce one.
    """
    from scripts.seed_product_master_identity import assert_local_database

    verified = getattr(args, "pilot_target", None)
    if isinstance(verified, SeedTarget) and verified.target == TARGET_PILOT:
        return verified.host
    return assert_local_database(async_url)
