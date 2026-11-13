"""
User Management Endpoints — app/api/v1/users.py

    GET    /users              List every account
    POST   /users              Create an account (any role, including ADMIN)
    PATCH  /users/{id}/role    Change an account's role
    PATCH  /users/{id}/active  Activate / deactivate an account
    POST   /users/{id}/reset-password  Set another account's password

ADMIN-only, entirely — require_admin gates the whole router, which is
also what makes "only ADMIN may create or promote ADMIN" and "MANAGER
cannot change roles" true structurally rather than by an extra check
that could be forgotten on one endpoint. No endpoint here ever returns
password_hash, and there is still no public registration endpoint —
this is the only way an account comes to exist after the bootstrap CLI.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import user_out
from app.core.dependencies import require_admin
from app.core.exceptions import RecordNotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import hash_password
from app.database.session import get_db
from app.models.user import SECURITY_EVENT_PASSWORD_RESET, User, UserSecurityEvent
from app.repositories.store_repository import StoreRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    ChangeUserRoleRequest,
    CreateUserRequest,
    ResetPasswordRequest,
    SetUserActiveRequest,
    UserOut,
)
from app.schemas.base import APIResponse
from app.schemas.store_access import (
    StoreAccessBulkGrantRequest,
    StoreAccessGrantAllRequest,
    StoreAccessGrantAllResult,
    StoreAccessGrantRequest,
    StoreAccessGrantResult,
    StoreAccessOut,
    StoreAccessRevokeRequest,
)
from app.services import store_access

logger = get_logger(__name__)

router = APIRouter(prefix="/users", tags=["Users"], dependencies=[Depends(require_admin)])


@router.get("", response_model=APIResponse[list[UserOut]], summary="List every account")
async def list_users(db: AsyncSession = Depends(get_db)) -> APIResponse[list[UserOut]]:
    users = await UserRepository(db).list_all()
    return APIResponse(data=[user_out(u) for u in users])


@router.post(
    "", response_model=APIResponse[UserOut], summary="Create an account",
    responses={409: {"description": "Username already registered"}},
)
async def create_user(
    body: CreateUserRequest, db: AsyncSession = Depends(get_db)
) -> APIResponse[UserOut]:
    repo = UserRepository(db)
    if await repo.get_by_username(body.username) is not None:
        raise ValidationError(
            message="An account with this username already exists.",
            detail={"field": "username"},
        )
    user = await repo.create(
        username=body.username, password_hash=hash_password(body.password), role=body.role.value,
    )
    await db.commit()
    await db.refresh(user)
    return APIResponse(data=user_out(user))


async def _get_or_404(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await UserRepository(db).get(user_id)
    if user is None:
        raise RecordNotFoundError(message="User not found.", detail={"user_id": str(user_id)})
    return user


@router.patch(
    "/{user_id}/role", response_model=APIResponse[UserOut], summary="Change an account's role",
)
async def change_user_role(
    user_id: uuid.UUID, body: ChangeUserRoleRequest, db: AsyncSession = Depends(get_db)
) -> APIResponse[UserOut]:
    user = await _get_or_404(db, user_id)
    await UserRepository(db).set_role(user, body.role.value)
    await db.commit()
    await db.refresh(user)
    return APIResponse(data=user_out(user))


@router.patch(
    "/{user_id}/active", response_model=APIResponse[UserOut],
    summary="Activate or deactivate an account",
)
async def set_user_active(
    user_id: uuid.UUID, body: SetUserActiveRequest, db: AsyncSession = Depends(get_db)
) -> APIResponse[UserOut]:
    user = await _get_or_404(db, user_id)
    await UserRepository(db).set_active(user, body.is_active)
    await db.commit()
    await db.refresh(user)
    return APIResponse(data=user_out(user))


def _password_refusal(field: str, reason: str, message: str) -> ValidationError:
    # Only the field and the reason — never the submitted value.
    return ValidationError(message=message, detail={"field": field, "reason": reason})


def _checked_new_password(body: ResetPasswordRequest) -> str:
    """The new password, if it meets the account password policy and matches its confirmation."""
    password, confirmation = body.new_password, body.confirm_password
    if not isinstance(password, str) or not password.strip():
        raise _password_refusal("new_password", "required", "Enter a new password.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise _password_refusal("new_password", "too_short",
                                f"The password must be at least {PASSWORD_MIN_LENGTH} characters.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise _password_refusal("new_password", "too_long",
                                f"The password must be at most {PASSWORD_MAX_LENGTH} characters.")
    if not isinstance(confirmation, str) or confirmation != password:
        raise _password_refusal("confirm_password", "mismatch", "The two passwords do not match.")
    return password


@router.post(
    "/{user_id}/reset-password", response_model=APIResponse[UserOut],
    summary="Reset another account's password",
    description=(
        "An ADMIN sets a new password for another account. Only the password changes — username, "
        "role, active state and id are untouched. The acting administrator is the authenticated "
        "session; the reset is recorded without the password. Resetting your own password here is "
        "refused. Existing sessions of the account are ended: its session number is raised, so it must "
        "sign in again with the new password (one active session per account, migration 0042)."
    ),
    responses={404: {"description": "User not found"},
               422: {"description": "Own account, or the password is missing, too short/long or unconfirmed"}},
)
async def reset_user_password(
    user_id: uuid.UUID,
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> APIResponse[UserOut]:
    if user_id == actor.id:
        raise ValidationError(
            message=("You cannot reset your own password here — this is for another account. "
                     "Ask another administrator to reset it."),
            detail={"field": "user_id", "reason": "own_account"},
        )
    target = await _get_or_404(db, user_id)
    password = _checked_new_password(body)

    repo = UserRepository(db)
    await repo.set_password_hash(target, hash_password(password))
    await repo.record_security_event(UserSecurityEvent(
        action=SECURITY_EVENT_PASSWORD_RESET,
        target_user_id=target.id, target_username=target.username,
        actor_user_id=actor.id, actor_username=actor.username, actor_role=actor.role,
    ))
    await db.commit()
    await db.refresh(target)
    logger.info("user_password_reset", target_user_id=str(target.id), target_username=target.username,
                actor_user_id=str(actor.id), actor_username=actor.username, actor_role=actor.role)
    return APIResponse(data=user_out(target))


# ---- per-user physical-store access for inventory ------------------------------------------

@router.get(
    "/{user_id}/store-access", response_model=APIResponse[list[StoreAccessOut]],
    summary="An account's store-access history (inventory)",
    description="Every grant for the account, active and revoked, oldest first, exactly as recorded.",
    responses={404: {"description": "User not found"}},
)
async def list_store_access(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> APIResponse[list[StoreAccessOut]]:
    await _get_or_404(db, user_id)
    grants = await store_access.store_access_history(db, user_id)
    stores = {sid: await StoreRepository(db).get(sid) for sid in {g.store_id for g in grants}}
    return APIResponse(data=[StoreAccessOut.from_grant(g, stores.get(g.store_id)) for g in grants])


@router.post(
    "/{user_id}/store-access", response_model=APIResponse[StoreAccessGrantResult],
    summary="Grant an account access to one physical store's inventory",
    description=(
        "Grants an active MANAGER or ADMIN account access to a physical store's inventory, with a reason. "
        "The acting administrator is the authenticated session and is recorded with the grant; granting "
        "your own account is allowed and recorded as such. An existing active grant is returned unchanged "
        "(created=false). USER accounts, inactive accounts and source identities are refused."
    ),
    responses={404: {"description": "User or store not found"},
               422: {"description": "Account not eligible, store is a source identity, or no reason"}},
)
async def grant_store_access(
    user_id: uuid.UUID, body: StoreAccessGrantRequest, db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> APIResponse[StoreAccessGrantResult]:
    grant, created = await store_access.grant_store_access(
        db, user_id=user_id, store_id=body.store_id, actor=actor, reason=body.reason)
    await db.commit()
    store = await StoreRepository(db).get(grant.store_id)
    if created:
        logger.info("store_access_granted", grant_id=str(grant.id), user_id=str(grant.user_id),
                    username=grant.username, store_id=str(grant.store_id), actor_user_id=str(actor.id),
                    actor_username=actor.username, actor_role=actor.role, self_granted=actor.id == grant.user_id)
    return APIResponse(data=StoreAccessGrantResult(grant=StoreAccessOut.from_grant(grant, store), created=created))


@router.post(
    "/{user_id}/store-access/grant-all-physical", response_model=APIResponse[StoreAccessGrantAllResult],
    summary="Grant an administrator account every physical store's inventory",
    description=(
        "For an active ADMIN account only: one audited grant per physical store it does not already hold, "
        "all with the given reason, in one transaction. Stores already granted are left unchanged, so it "
        "can be repeated. MANAGER accounts are refused: they are granted store by store."
    ),
    responses={404: {"description": "User not found"},
               422: {"description": "Account is not an active administrator, or no reason"}},
)
async def grant_all_physical_stores(
    user_id: uuid.UUID, body: StoreAccessGrantAllRequest, db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> APIResponse[StoreAccessGrantAllResult]:
    created, existing = await store_access.grant_all_physical_stores(
        db, user_id=user_id, actor=actor, reason=body.reason)
    await db.commit()
    stores = {s.id: s for s in await StoreRepository(db).list()}
    logger.info("store_access_granted_all_physical", user_id=str(user_id), actor_user_id=str(actor.id),
                actor_username=actor.username, created=len(created), already_granted=len(existing))
    return APIResponse(data=StoreAccessGrantAllResult(
        created=[StoreAccessOut.from_grant(g, stores.get(g.store_id)) for g in created],
        already_granted=[StoreAccessOut.from_grant(g, stores.get(g.store_id)) for g in existing],
    ))


@router.post(
    "/{user_id}/store-access/bulk", response_model=APIResponse[StoreAccessGrantAllResult],
    summary="Grant an account several physical stores' inventory at once (all or nothing)",
    description=(
        "Grants an active MANAGER or ADMIN account 1 to 100 physical stores, each listed once, with one "
        "reason. Every store is checked before anything is written: if any is unknown or a source "
        "identity, nothing is granted and every offending store is named (422, reason invalid_stores). "
        "Each new grant is its own audit row (the acting administrator, role and reason) and log entry; "
        "a store the account already holds is returned unchanged under already_granted."
    ),
    responses={404: {"description": "User not found"},
               422: {"description": "No reason, 0 or more than 100 stores, duplicate ids, account not "
                                    "eligible, or invalid stores (nothing granted)"}},
)
async def grant_store_access_bulk(
    user_id: uuid.UUID, body: StoreAccessBulkGrantRequest, db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> APIResponse[StoreAccessGrantAllResult]:
    created, existing = await store_access.grant_stores(
        db, user_id=user_id, store_ids=body.store_ids, actor=actor, reason=body.reason)
    await db.commit()
    stores = {s.id: s for s in await StoreRepository(db).list()}
    for grant in created:                      # one entry per new grant, exactly as the single-store route
        logger.info("store_access_granted", grant_id=str(grant.id), user_id=str(grant.user_id),
                    username=grant.username, store_id=str(grant.store_id), actor_user_id=str(actor.id),
                    actor_username=actor.username, actor_role=actor.role, self_granted=actor.id == grant.user_id,
                    bulk=True)
    logger.info("store_access_granted_bulk", user_id=str(user_id), actor_user_id=str(actor.id),
                actor_username=actor.username, requested=len(body.store_ids), created=len(created),
                already_granted=len(existing))
    return APIResponse(data=StoreAccessGrantAllResult(
        created=[StoreAccessOut.from_grant(g, stores.get(g.store_id)) for g in created],
        already_granted=[StoreAccessOut.from_grant(g, stores.get(g.store_id)) for g in existing],
    ))


@router.post(
    "/{user_id}/store-access/{store_id}/revoke", response_model=APIResponse[StoreAccessOut],
    summary="Revoke an account's active access to one physical store's inventory",
    description=(
        "Stamps the active grant as revoked, once, with the acting administrator and a reason. The grant "
        "row is kept as history and never changes again; access ends on the account's next request. "
        "With no active grant this is a 404 and nothing is written."
    ),
    responses={404: {"description": "User not found, or no active grant for that store"}},
)
async def revoke_store_access(
    user_id: uuid.UUID, store_id: uuid.UUID, body: StoreAccessRevokeRequest, db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> APIResponse[StoreAccessOut]:
    grant = await store_access.revoke_store_access(
        db, user_id=user_id, store_id=store_id, actor=actor, reason=body.reason)
    await db.commit()
    store = await StoreRepository(db).get(grant.store_id)
    logger.info("store_access_revoked", grant_id=str(grant.id), user_id=str(grant.user_id),
                username=grant.username, store_id=str(grant.store_id), actor_user_id=str(actor.id),
                actor_username=actor.username, actor_role=actor.role)
    return APIResponse(data=StoreAccessOut.from_grant(grant, store))
