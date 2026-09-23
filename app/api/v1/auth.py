"""
Auth Endpoints — app/api/v1/auth.py

    POST /auth/login    Username + password -> httpOnly session cookie
    POST /auth/logout   Clears the session cookie
    GET  /auth/me        The authenticated caller's identity

No public registration: accounts are created only via
scripts/create_user.py (the first ADMIN) or ADMIN user management
(app/api/v1/users.py) after that. The token is set as an httpOnly,
SameSite=Lax cookie — never returned in the JSON body — so it is never
reachable from page JavaScript (XSS) and the frontend's axios client
never needs to store or attach it manually; the browser does that.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user
from app.core.exceptions import AuthenticationError
from app.core.security import ACCESS_TOKEN_COOKIE_NAME, create_access_token, verify_password
from app.database.session import get_db
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, UserOut
from app.schemas.base import APIResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id, username=user.username, role=UserRole(user.role),
        is_active=user.is_active, created_at=user.created_at, updated_at=user.updated_at,
    )


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )


@router.post(
    "/login",
    response_model=APIResponse[UserOut],
    summary="Log in with username and password",
    responses={401: {"description": "Invalid credentials or inactive account"}},
)
async def login(
    body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)
) -> APIResponse[UserOut]:
    # body.username is already normalized (LoginRequest's validator).
    user = await UserRepository(db).get_by_username(body.username)
    # Same message whether the username doesn't exist or the password is
    # wrong — never tell an attacker which one it was.
    if user is None or not verify_password(body.password, user.password_hash):
        raise AuthenticationError(message="Incorrect username or password.")
    if not user.is_active:
        raise AuthenticationError(message="This account is inactive. Contact an administrator.")
    _set_session_cookie(response, create_access_token(user.id))
    return APIResponse(data=user_out(user))


@router.post("/logout", response_model=APIResponse[dict], summary="Log out")
async def logout(response: Response) -> APIResponse[dict]:
    response.delete_cookie(ACCESS_TOKEN_COOKIE_NAME, path="/")
    return APIResponse(data={"logged_out": True})


@router.get(
    "/me",
    response_model=APIResponse[UserOut],
    summary="The authenticated caller's identity",
    responses={401: {"description": "Not authenticated"}},
)
async def me(user: User = Depends(get_current_user)) -> APIResponse[UserOut]:
    return APIResponse(data=user_out(user))
