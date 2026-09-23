"""
User Management Endpoints — app/api/v1/users.py

    GET    /users              List every account
    POST   /users              Create an account (any role, including ADMIN)
    PATCH  /users/{id}/role    Change an account's role
    PATCH  /users/{id}/active  Activate / deactivate an account

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
from app.core.security import hash_password
from app.database.session import get_db
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import ChangeUserRoleRequest, CreateUserRequest, SetUserActiveRequest, UserOut
from app.schemas.base import APIResponse

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
