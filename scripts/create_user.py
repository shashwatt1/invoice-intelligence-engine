#!/usr/bin/env python
"""
Account bootstrap CLI — scripts/create_user.py

    python scripts/create_user.py

The only way an account is ever created except through ADMIN user
management (POST /api/v1/users, itself ADMIN-only) — there is no public
registration endpoint. This is how the very first ADMIN comes to exist.

Interactively prompts for username, role, then password (hidden —
getpass, never echoed to the terminal, confirmed twice), hashes the
password with the same bcrypt used by login, and writes the account.
Refuses a duplicate username (case-insensitive) or an invalid role.
Never prints the password back, never writes it to a file or log — only
the bcrypt hash is persisted, by app.core.security.hash_password, the
identical function app.api.v1.auth.login verifies against.

Non-interactive use (e.g. scripted bootstrap in CI) via flags:

    python scripts/create_user.py --username shashwatt1 --role ADMIN
        (still prompts for the password — never accept it as a CLI
        argument, which would leak it into shell history and `ps`)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.models.user import InvalidUsernameError, UserRole, validate_username  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def _validate_username(raw: str) -> str:
    try:
        return validate_username(raw)
    except InvalidUsernameError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


def _validate_role(raw: str) -> UserRole:
    try:
        return UserRole(raw.strip().upper())
    except ValueError:
        valid = ", ".join(r.value for r in UserRole)
        print(f"'{raw}' is not a valid role. Must be one of: {valid}.", file=sys.stderr)
        sys.exit(1)


def _read_password() -> str:
    while True:
        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
            continue
        confirm = getpass.getpass("Confirm Password: ")
        if password != confirm:
            print("Passwords did not match. Try again.", file=sys.stderr)
            continue
        return password


async def _create(username: str, password: str, role: UserRole) -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = UserRepository(session)
        if await repo.get_by_username(username) is not None:
            print(f"An account with username '{username}' already exists.", file=sys.stderr)
            sys.exit(1)
        user = await repo.create(username=username, password_hash=hash_password(password), role=role.value)
        await session.commit()
        # The id, username and role are safe to print; the password
        # never is, in any form, at any point in this script.
        print(f"Created {role.value} account: {user.username} (id: {user.id})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", help="Skip the interactive username prompt.")
    parser.add_argument("--role", help="Skip the interactive role prompt (USER, MANAGER, or ADMIN).")
    args = parser.parse_args()

    username = _validate_username(args.username if args.username else input("Username: "))
    role = _validate_role(args.role if args.role else input(f"Role ({'/'.join(r.value for r in UserRole)}): "))
    password = _read_password()

    asyncio.run(_create(username, password, role))


if __name__ == "__main__":
    main()
