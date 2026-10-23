#!/usr/bin/env python
"""
Administrative store-access CLI (inventory) — scripts/grant_store_access.py

    python scripts/grant_store_access.py grant  --actor ADMIN --username NAME --store-id UUID --reason "..."
    python scripts/grant_store_access.py revoke --actor ADMIN --username NAME --store-id UUID --reason "..."
    python scripts/grant_store_access.py list   --username NAME

The CLI counterpart to the ADMIN store-access endpoints
(/api/v1/users/{id}/store-access) for operators with database access but no
browser session, and the explicit bootstrap for the first grants: nothing is
granted by default to any role. It uses the same service
(app.services.store_access), so the same rules and the same audit record apply:

  * --actor must name an existing, active ADMIN account; it is recorded as the
    granting or revoking administrator (an administrator may grant their own
    account, and that is recorded as self-granted);
  * the account granted must be an active MANAGER or ADMIN; the store must be a
    physical store (never a source identity);
  * --reason is required and kept with the record;
  * grants and revocations are history: a revoked grant never changes again,
    granting again inserts a new row, and revoking with no active grant
    changes nothing.

Inventory itself stays off until INVENTORY_ENABLED is set; grants can be made
before that.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.exceptions import InvoiceBaseException  # noqa: E402
from app.database.session import get_session_factory  # noqa: E402
from app.models.user import UserRole, normalize_username  # noqa: E402
from app.repositories.store_repository import StoreRepository  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.services import store_access  # noqa: E402


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


async def _account(repo: UserRepository, username: str, label: str):
    user = await repo.get_by_username(username)
    if user is None:
        _fail(f"No account with username '{username}' was found ({label}).")
    return user


async def _run(args: argparse.Namespace) -> None:
    factory = get_session_factory()
    async with factory() as session:
        users = UserRepository(session)
        target = await _account(users, normalize_username(args.username), "account")
        if args.command == "list":
            for grant in await store_access.store_access_history(session, target.id):
                store = await StoreRepository(session).get(grant.store_id)
                state = (f"revoked {grant.revoked_at:%Y-%m-%d} by {grant.revoked_by}: {grant.revoke_reason}"
                         if grant.revoked_at else "ACTIVE")
                print(f"{store.label if store else grant.store_id} | granted {grant.granted_at:%Y-%m-%d} by "
                      f"{grant.granted_by} ({grant.granted_by_role}): {grant.grant_reason} | {state}")
            return
        actor = await _account(users, normalize_username(args.actor), "actor")
        if actor.role != UserRole.ADMIN.value or not actor.is_active:
            _fail(f"--actor '{actor.username}' must be an active ADMIN account.")
        try:
            store_id = uuid.UUID(args.store_id)
        except ValueError:
            _fail(f"'{args.store_id}' is not a store id (UUID).")
        try:
            if args.command == "grant":
                grant, created = await store_access.grant_store_access(
                    session, user_id=target.id, store_id=store_id, actor=actor, reason=args.reason)
                await session.commit()
                print(f"{'Granted' if created else 'Already granted (unchanged)'}: '{target.username}' -> store "
                      f"{grant.store_id}, by '{grant.granted_by}'.")
            else:
                grant = await store_access.revoke_store_access(
                    session, user_id=target.id, store_id=store_id, actor=actor, reason=args.reason)
                await session.commit()
                print(f"Revoked: '{target.username}' -> store {grant.store_id}, by '{grant.revoked_by}'.")
        except InvoiceBaseException as exc:
            await session.rollback()
            _fail(f"Refused: {exc.message}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("grant", "revoke", "list"))
    parser.add_argument("--username", required=True, help="The account whose store access changes or is listed.")
    parser.add_argument("--actor", help="The active ADMIN account recorded as acting (grant/revoke).")
    parser.add_argument("--store-id", help="The physical store's id (grant/revoke).")
    parser.add_argument("--reason", help="Why (grant/revoke); kept with the record.")
    args = parser.parse_args()
    if args.command != "list":
        missing = [flag for flag, value in (("--actor", args.actor), ("--store-id", args.store_id),
                                            ("--reason", args.reason)) if not value]
        if missing:
            parser.error(f"{args.command} needs {', '.join(missing)}")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
