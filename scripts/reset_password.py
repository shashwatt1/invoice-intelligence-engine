#!/usr/bin/env python
"""
Administrative password reset CLI — scripts/reset_password.py

    python scripts/reset_password.py

Internal app, username/password only — there is no email reset link, no
OTP, no self-service recovery. This script IS the reset mechanism: an
administrator with shell access to the database looks up an existing
account by username and overwrites only its password_hash, using the
same bcrypt hashing login already verifies against
(app.core.security.hash_password / verify_password).

Prompts for username, then a new password (hidden — getpass, never
echoed, confirmed twice). Never prints, logs, or stores the plaintext
password at any point. Does not reveal whether the account's previous
password was correct — this is an unconditional administrative
overwrite, not a challenge-based reset. Touches password_hash only:
id, username, role, is_active, created_at, ownership, invoice history,
proposal history, and mapping history are all untouched.

Non-interactive use (e.g. scripted bootstrap in CI) via flag:

    python scripts/reset_password.py --username vivek
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
from app.models.user import normalize_username  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def _read_new_password() -> str:
    while True:
        password = getpass.getpass("New Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
            continue
        confirm = getpass.getpass("Confirm Password: ")
        if password != confirm:
            print("Passwords did not match. Try again.", file=sys.stderr)
            continue
        return password


async def _reset(username: str, password: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        repo = UserRepository(session)
        user = await repo.get_by_username(username)
        if user is None:
            print(f"No account with username '{username}' was found.", file=sys.stderr)
            sys.exit(1)
        await repo.set_password_hash(user, hash_password(password))
        await session.commit()
        print(f"Password reset successfully for '{user.username}'.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", help="Skip the interactive username prompt.")
    args = parser.parse_args()

    username = normalize_username(args.username if args.username else input("Username: "))
    password = _read_new_password()

    asyncio.run(_reset(username, password))


if __name__ == "__main__":
    main()
