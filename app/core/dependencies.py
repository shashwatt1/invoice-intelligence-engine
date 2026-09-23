"""
Authentication / authorization FastAPI dependencies — app/core/dependencies.py

Every protected endpoint depends on one of these, never re-implements
its own check. The database row is the only source of truth for role
and active state — re-read on every request, never trusted from the
JWT's own claims or from anything the client supplies (a request body
`role`/`actor`/`uploaded_by` is never authorization evidence; see P2's
now-replaced trust model in app.services.document_lifecycle history).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import ACCESS_TOKEN_COOKIE_NAME, decode_access_token
from app.database.session import get_db
from app.models.user import ROLE_RANK, User, UserRole
from app.repositories.user_repository import UserRepository


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    """
    The authenticated user for this request, from the httpOnly session
    cookie — never from a header/body field a client could set itself.
    Raises AuthenticationError (401) for a missing, invalid, expired
    token, or an account that no longer exists or was deactivated since
    the token was issued (is_active is checked here, fresh, every time).
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        raise AuthenticationError(message="Not authenticated. Please log in.")
    user_id = decode_access_token(token)
    if user_id is None:
        raise AuthenticationError(message="Session expired or invalid. Please log in again.")
    user = await UserRepository(db).get(user_id)
    if user is None or not user.is_active:
        raise AuthenticationError(message="This account is inactive or no longer exists.")
    return user


# Any authenticated, active account — every role qualifies. Distinct
# name from get_current_user only for readability at call sites that
# want to say "this just needs a login" rather than "give me the user".
require_authenticated_user = get_current_user
require_user = get_current_user


def _require_role(minimum: UserRole) -> Callable[..., Coroutine[Any, Any, User]]:
    async def _dependency(user: User = Depends(get_current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[minimum.value]:
            raise PermissionDeniedError(
                message=f"This action requires the {minimum.value} role.",
                detail={"required_role": minimum.value, "actual_role": user.role},
            )
        return user
    return _dependency


require_manager = _require_role(UserRole.MANAGER)   # MANAGER or ADMIN
require_admin = _require_role(UserRole.ADMIN)       # ADMIN only
