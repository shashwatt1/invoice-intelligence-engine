"""
Which database a command-line tool is really connected to — app/services/db_target.py

Shared by the operator tools that can write (scripts/revalidate_invoices.py, scripts/merge_vendors.py). The target is read
from the connection in use, never from a settings file: the host and name from the engine's URL (never the password) and the
name the server itself reports. `--expect-database host/db` is compared with it before anything is read for an apply.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text


@dataclass(frozen=True)
class Target:
    host: str
    port: int | None
    database: str            # from the connection URL
    connected_database: str  # what the server says it is (select current_database())

    @property
    def expect_flag(self) -> str:
        return f"{self.host}/{self.database}"

    def describe(self) -> str:
        port = f":{self.port}" if self.port else ""
        return f"host={self.host}{port}  database={self.database}  (server reports: {self.connected_database})"


async def actual_target(session) -> Target:
    """
    The database this session is really connected to: host and name from the engine's URL (never the password) and
    the name the server itself reports. Both come from the connection in use, not from a settings file.
    """
    url = session.get_bind().url
    connected = (await session.execute(text("select current_database()"))).scalar_one()
    return Target(host=(url.host or ""), port=url.port, database=(url.database or ""), connected_database=connected)


def target_mismatch(expected: str | None, target: Target) -> str | None:
    """Why `expected` (host/db, or host:port/db) is not this target; None when it is. Pure."""
    if not expected or "/" not in expected:
        return "--expect-database must look like host/db (the dry run prints it)."
    where, name = expected.rsplit("/", 1)
    host, _, port = where.partition(":")
    if not host or not name:
        return "--expect-database must look like host/db (the dry run prints it)."
    problems = []
    if host.lower() != target.host.lower():
        problems.append(f"host {target.host!r}")
    if port and port != str(target.port):
        problems.append(f"port {target.port}")
    if name != target.database or name != target.connected_database:
        problems.append(f"database {target.database!r} (server reports {target.connected_database!r})")
    return None if not problems else f"The connected database is not the one expected; it differs in: {', '.join(problems)}."
