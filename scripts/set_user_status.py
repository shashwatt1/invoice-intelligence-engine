#!/usr/bin/env python
"""
Administrative account activation/deactivation CLI — scripts/set_user_status.py

    python scripts/set_user_status.py

The CLI counterpart to ADMIN's web "Deactivate"/"Reactivate" control
(PATCH /api/v1/users/{id}/active) for operators with database access
but no browser session — same underlying UserRepository.set_active,
same effect. Deactivating a user makes every future request re-read
is_active=false from the users row (app.core.dependencies.get_current_user
checks it fresh on every request) and immediately reject that account's
login and any already-authenticated request — no token blacklist or
session store needed, since nothing is ever trusted from the token
itself beyond identity. Touches is_active only: password_hash, role,
username, and every other field are untouched.

Non-interactive use via flags:

    python scripts/set_user_status.py --username vivek --status INACTIVE
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.session import get_session_factory  # noqa: E402
from app.models.user import normalize_username  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402

STATUS_TO_ACTIVE = {"ACTIVE": True, "INACTIVE": False}


def _parse_status(raw: str) -> bool:
    key = raw.strip().upper()
    if key not in STATUS_TO_ACTIVE:
        print(f"'{raw}' is not a valid status. Must be one of: ACTIVE, INACTIVE.", file=sys.stderr)
        sys.exit(1)
    return STATUS_TO_ACTIVE[key]


async def _set_status(username: str, is_active: bool) -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = UserRepository(session)
        user = await repo.get_by_username(username)
        if user is None:
            print(f"No account with username '{username}' was found.", file=sys.stderr)
            sys.exit(1)
        await repo.set_active(user, is_active)
        await session.commit()
        state = "ACTIVE" if is_active else "INACTIVE"
        print(f"'{user.username}' is now {state}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", help="Skip the interactive username prompt.")
    parser.add_argument("--status", help="Skip the interactive status prompt (ACTIVE or INACTIVE).")
    args = parser.parse_args()

    username = normalize_username(args.username if args.username else input("Username: "))
    is_active = _parse_status(args.status if args.status else input("Status (ACTIVE/INACTIVE): "))

    asyncio.run(_set_status(username, is_active))


if __name__ == "__main__":
    main()
