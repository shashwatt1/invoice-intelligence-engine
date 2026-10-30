"""
Per-user physical-store access for inventory — app/services/store_access.py

The single place inventory asks "may this account use this store?". Every
inventory route and every invoice-linked inventory operation goes through it;
hiding stores in the browser is never the control.

The rule, checked fresh on every request (nothing is cached):
  * the account is active (get_current_user also refuses inactive accounts);
  * its role is MANAGER or above (USER never has inventory, grant or not);
  * the store is a physical store (a source identity is never an inventory
    store);
  * a MANAGER holds an active grant for that store (user_store_access); an
    ADMIN needs no grant for any ACTIVE physical store (an administrator
    administers every store). An administrator's explicit grant to an inactive
    store is still honoured, so history granted before a store was
    deactivated stays reachable.

Refusals never reveal whether a store exists: an unknown store, a source
identity and a store the account has no grant for all get the same 404.
Invoice-linked callers map that to their own uniform "not found".

Granting and revoking stay audited actions (a reason, immutable history) and
change nothing for an ADMIN, who does not need them.

Grants and revocations are made only by an active ADMIN (the ADMIN API or
scripts/grant_store_access.py), each with a reason, and are recorded as
immutable history (see app.models.user_store_access). Both lock the target
account's row first, so concurrent grants and revocations for one account are
serialized; the partial unique index is the backstop for a second active
grant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError, RecordNotFoundError, ValidationError
from app.models.store import KIND_PHYSICAL, STATUS_ACTIVE, Store
from app.models.user import ROLE_RANK, User, UserRole
from app.models.user_store_access import UserStoreAccess
from app.repositories.store_repository import StoreRepository

# The role that may use inventory at all (matches require_manager on the routes).
INVENTORY_MINIMUM_ROLE = UserRole.MANAGER
REASON_MAX_LENGTH = 500
MAX_BULK_GRANT = 100            # stores per bulk grant request


def has_inventory_role(user) -> bool:
    """Active and MANAGER or above. Unknown roles fail closed."""
    return bool(getattr(user, "is_active", False)) and (
        ROLE_RANK.get(str(getattr(user, "role", None)), 0) >= ROLE_RANK[INVENTORY_MINIMUM_ROLE.value]
    )


def is_admin(user) -> bool:
    """An active ADMIN: the one role with implicit access to every active physical store."""
    return has_inventory_role(user) and str(getattr(user, "role", None)) == UserRole.ADMIN.value


def _implicit_access(user, store: Store | None) -> bool:
    """An administrator's access without a grant: any ACTIVE, physical store (never a source identity)."""
    return store is not None and is_admin(user) and store.kind == KIND_PHYSICAL and store.status == STATUS_ACTIVE


def store_not_found(store_id: uuid.UUID) -> RecordNotFoundError:
    """The one refusal for an unknown, non-physical or ungranted store: it reveals nothing about the store."""
    return RecordNotFoundError(message="Store not found.", detail={"store_id": str(store_id)})


def _require_inventory_role(user) -> None:
    if not has_inventory_role(user):
        raise PermissionDeniedError(message="Inventory is available to managers and administrators.",
                                    detail={"required_role": INVENTORY_MINIMUM_ROLE.value})


async def _active_grant(session: AsyncSession, user_id: uuid.UUID, store_id: uuid.UUID) -> UserStoreAccess | None:
    return (await session.execute(
        select(UserStoreAccess).where(UserStoreAccess.user_id == user_id, UserStoreAccess.store_id == store_id,
                                      UserStoreAccess.revoked_at.is_(None))
    )).scalars().first()


async def has_store_access(session: AsyncSession, user, store_id: uuid.UUID | None) -> bool:
    """Whether `user` may use inventory for `store_id` now. Never raises for an ungranted store."""
    if store_id is None or not has_inventory_role(user):
        return False
    store = await StoreRepository(session).get(store_id)
    if store is None or store.kind != KIND_PHYSICAL:
        return False
    return _implicit_access(user, store) or await _active_grant(session, user.id, store_id) is not None


async def ensure_store_access(session: AsyncSession, user, store_id: uuid.UUID) -> Store:
    """
    The store, if `user` may use its inventory. A role below MANAGER is a 403
    before anything is looked up; everything else that is not allowed is the
    same 404 (store_not_found).
    """
    _require_inventory_role(user)
    store = await StoreRepository(session).get(store_id)
    if store is None or store.kind != KIND_PHYSICAL:
        raise store_not_found(store_id)
    if not _implicit_access(user, store) and await _active_grant(session, user.id, store_id) is None:
        raise store_not_found(store_id)
    return store


async def accessible_stores(session: AsyncSession, user) -> list[Store]:
    """
    The physical stores `user` may use in inventory, by label. A MANAGER: the stores they are granted. An
    ADMIN: every active physical store (plus any inactive one they were explicitly granted). Empty below MANAGER.
    """
    return [store for store, _ in await visible_physical_stores(session, user)]


async def visible_physical_stores(session: AsyncSession, user) -> list[tuple[Store, bool]]:
    """
    Every physical store the Inventory page lists for `user`: [(store, usable)], by label.

      * MANAGER: only the stores they are granted. A store they hold no grant for is not
        listed, and nothing about it is revealed.
      * ADMIN: every ACTIVE physical store, with no grant needed, plus any inactive physical
        store they were explicitly granted. `usable` is true for all of them.
      * below MANAGER: nothing.

    `usable` is kept (rather than dropped) so callers state outright that a listed store may be
    opened; the same rule decides every store route (ensure_store_access). Whether a store has
    invoices, receipts or movements is irrelevant: a configured physical store is listed. A
    source identity (for example the unresolved Item Sales code) is never a physical store.
    """
    if not has_inventory_role(user):
        return []
    granted = set((await session.execute(
        select(UserStoreAccess.store_id).where(UserStoreAccess.user_id == user.id,
                                               UserStoreAccess.revoked_at.is_(None))
    )).scalars().all())
    listed: list[tuple[Store, bool]] = []
    for store in await StoreRepository(session).list():
        if store.kind != KIND_PHYSICAL:
            continue
        if store.id in granted or _implicit_access(user, store):
            listed.append((store, True))
    return sorted(listed, key=lambda pair: (pair[0].label.lower(), str(pair[0].id)))


# ---- administration -------------------------------------------------------------------------

def _require_admin_actor(actor) -> None:
    if not (getattr(actor, "is_active", False) and getattr(actor, "role", None) == UserRole.ADMIN.value):
        raise PermissionDeniedError(message="Only an active administrator can change store access.",
                                    detail={"required_role": UserRole.ADMIN.value})


def _reason(value: str | None, field: str) -> str:
    reason = (value or "").strip()
    if not reason:
        raise ValidationError(message="Give a reason; it is kept with the store-access record.",
                              detail={"field": field, "reason": "required"})
    if len(reason) > REASON_MAX_LENGTH:
        raise ValidationError(message=f"The reason is limited to {REASON_MAX_LENGTH} characters.",
                              detail={"field": field, "reason": "too_long"})
    return reason


async def _locked_account(session: AsyncSession, user_id: uuid.UUID) -> User:
    """The target account, row-locked until the transaction ends: serializes grants/revocations for it."""
    target = (await session.execute(select(User).where(User.id == user_id).with_for_update())).scalars().first()
    if target is None:
        raise RecordNotFoundError(message="User not found.", detail={"user_id": str(user_id)})
    return target


async def grant_store_access(
    session: AsyncSession, *, user_id: uuid.UUID, store_id: uuid.UUID, actor, reason: str | None,
) -> tuple[UserStoreAccess, bool]:
    """
    Grant `user_id` access to a physical store. Returns (grant, created). An
    existing active grant is returned unchanged (created=False): nothing is
    written. Flushes; the caller commits.
    """
    _require_admin_actor(actor)
    why = _reason(reason, "reason")
    target = await _locked_account(session, user_id)
    if not has_inventory_role(target):
        raise ValidationError(
            message="Store access can only be granted to an active manager or administrator account.",
            detail={"field": "user_id", "reason": "account_not_eligible", "role": target.role,
                    "is_active": target.is_active},
        )
    store = await StoreRepository(session).get(store_id)
    if store is None:
        raise RecordNotFoundError(message="Store not found.", detail={"store_id": str(store_id)})
    if store.kind != KIND_PHYSICAL:
        raise ValidationError(message="Only a physical store can be granted; this is a source identity.",
                              detail={"field": "store_id", "reason": "source_identity"})
    existing = await _active_grant(session, target.id, store.id)
    if existing is not None:
        return existing, False
    grant = UserStoreAccess(
        user_id=target.id, username=target.username, store_id=store.id, granted_at=datetime.now(UTC),
        granted_by_user_id=actor.id, granted_by=actor.username, granted_by_role=actor.role, grant_reason=why,
    )
    try:
        # Added inside the savepoint: if the unique index refuses it, the rollback discards it too.
        async with session.begin_nested():
            session.add(grant)
            await session.flush()
    except IntegrityError as exc:
        if "uq_user_store_access_active" not in str(exc.orig):
            raise
        # A concurrent grant won the race; the account lock makes this a backstop only.
        existing = await _active_grant(session, target.id, store.id)
        if existing is None:
            raise
        return existing, False
    return grant, True


async def grant_stores(
    session: AsyncSession, *, user_id: uuid.UUID, store_ids: list[uuid.UUID], actor, reason: str | None,
) -> tuple[list[UserStoreAccess], list[UserStoreAccess]]:
    """
    Grant `user_id` several physical stores at once, all or nothing. Returns
    (created, already_active).

    Everything is checked before anything is written: an active ADMIN actor,
    the reason, 1 to MAX_BULK_GRANT store ids with no duplicates, an eligible
    account (locked, as for every grant), and that every store exists and is
    physical. Any problem refuses the whole request, naming every offending
    store, and writes nothing. Each store is then granted through
    grant_store_access, so each new grant is its own audit row with the same
    rules; an existing active grant is returned unchanged. Flushes; the caller
    commits once, so an unexpected failure rolls every grant back.
    """
    _require_admin_actor(actor)
    why = _reason(reason, "reason")
    ids = list(store_ids)
    if not ids or len(ids) > MAX_BULK_GRANT:
        raise ValidationError(message=f"Choose between 1 and {MAX_BULK_GRANT} stores.",
                              detail={"field": "store_ids", "reason": "count", "max": MAX_BULK_GRANT})
    duplicates = sorted({str(i) for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValidationError(message="Each store may be listed only once; nothing was granted.",
                              detail={"field": "store_ids", "reason": "duplicate_store_ids", "store_ids": duplicates})
    target = await _locked_account(session, user_id)
    if not has_inventory_role(target):
        raise ValidationError(
            message="Store access can only be granted to an active manager or administrator account.",
            detail={"field": "user_id", "reason": "account_not_eligible", "role": target.role,
                    "is_active": target.is_active},
        )
    found = {s.id: s for s in (await session.execute(select(Store).where(Store.id.in_(ids)))).scalars().all()}
    invalid = {str(i): ("unknown" if i not in found else "source_identity")
               for i in ids if i not in found or found[i].kind != KIND_PHYSICAL}
    if invalid:
        raise ValidationError(
            message=f"{len(invalid)} of the selected stores cannot be granted; nothing was granted.",
            detail={"field": "store_ids", "reason": "invalid_stores", "stores": invalid},
        )
    created: list[UserStoreAccess] = []
    existing: list[UserStoreAccess] = []
    for store in sorted(found.values(), key=lambda s: (s.label.lower(), str(s.id))):
        grant, was_created = await grant_store_access(session, user_id=target.id, store_id=store.id,
                                                      actor=actor, reason=why)
        (created if was_created else existing).append(grant)
    return created, existing


async def grant_all_physical_stores(
    session: AsyncSession, *, user_id: uuid.UUID, actor, reason: str | None,
) -> tuple[list[UserStoreAccess], list[UserStoreAccess]]:
    """
    Grant an ADMINISTRATOR account every physical store it does not already
    hold, one audited grant per store with the same reason. Returns
    (created, already_active). Never offered for a MANAGER: managers are
    granted store by store. Repeatable: stores already granted are left
    untouched. Flushes; the caller commits, so it is all or nothing.
    """
    _require_admin_actor(actor)
    _reason(reason, "reason")
    target = await _locked_account(session, user_id)
    if not (target.is_active and target.role == UserRole.ADMIN.value):
        raise ValidationError(
            message="Granting every physical store is only available for an active administrator account.",
            detail={"field": "user_id", "reason": "account_not_administrator", "role": target.role,
                    "is_active": target.is_active},
        )
    created: list[UserStoreAccess] = []
    existing: list[UserStoreAccess] = []
    stores = [s for s in await StoreRepository(session).list() if s.kind == KIND_PHYSICAL]
    for store in sorted(stores, key=lambda s: (s.label.lower(), str(s.id))):
        grant, was_created = await grant_store_access(session, user_id=target.id, store_id=store.id,
                                                      actor=actor, reason=reason)
        (created if was_created else existing).append(grant)
    return created, existing


async def revoke_store_access(
    session: AsyncSession, *, user_id: uuid.UUID, store_id: uuid.UUID, actor, reason: str | None,
) -> UserStoreAccess:
    """
    Revoke the account's active grant for the store, once. A revoked grant is
    never changed again; with no active grant this is a 404 and nothing is
    written. Flushes; the caller commits.
    """
    _require_admin_actor(actor)
    why = _reason(reason, "reason")
    target = await _locked_account(session, user_id)
    grant = (await session.execute(
        select(UserStoreAccess).where(UserStoreAccess.user_id == target.id, UserStoreAccess.store_id == store_id,
                                      UserStoreAccess.revoked_at.is_(None))
        .with_for_update()
    )).scalars().first()
    if grant is None:
        raise RecordNotFoundError(message="No active store access to revoke.",
                                  detail={"user_id": str(user_id), "store_id": str(store_id)})
    grant.revoked_at = max(datetime.now(UTC), grant.granted_at)
    grant.revoked_by_user_id = actor.id
    grant.revoked_by = actor.username
    grant.revoked_by_role = actor.role
    grant.revoke_reason = why
    await session.flush()
    return grant


async def store_access_history(session: AsyncSession, user_id: uuid.UUID) -> list[UserStoreAccess]:
    """Every grant for the account, active and revoked, oldest first."""
    return list((await session.execute(
        select(UserStoreAccess).where(UserStoreAccess.user_id == user_id)
        .order_by(UserStoreAccess.granted_at, UserStoreAccess.id)
    )).scalars().all())
