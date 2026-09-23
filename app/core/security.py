"""
Password hashing and JWT session tokens — app/core/security.py

The whole trust boundary for every authenticated request lives here:
bcrypt for password storage, HS256 JWTs (python-jose) for the session.
Deliberately not building session storage / Redis — a signed,
short-lived, httpOnly-cookie JWT is the smallest secure mechanism that
fits this FastAPI app without new infrastructure.

Uses the `bcrypt` package directly rather than passlib's CryptContext
wrapper (pyproject.toml originally declared passlib[bcrypt]): passlib
1.7.4 (its last release, effectively unmaintained) reads
`bcrypt.__about__.__version__` during its own backend self-test, an
attribute bcrypt>=4.1 removed, which crashes every hash/verify call —
confirmed against the installed bcrypt 5.0.0 while bootstrapping the
first account. bcrypt itself (what passlib was wrapping anyway) has no
such issue and is the more directly maintained, equally established
choice.

The token's payload is an identity POINTER only (the user id, `sub`) —
role and is_active are never trusted from the token itself. Every
request that needs them re-reads the users row (see
app.core.dependencies.get_current_user), so a role change or
deactivation takes effect on the very next request, not at the token's
original expiry.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings

ALGORITHM = "HS256"
ACCESS_TOKEN_COOKIE_NAME = "access_token"


def hash_password(password: str) -> str:
    """bcrypt hash. Never store, log, or return the plaintext this came from."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        # A malformed/foreign hash format must fail closed, never raise
        # into a 500 that could hint at why.
        return False


def create_access_token(user_id: uuid.UUID) -> str:
    """
    A JWT whose only claim that matters is `sub` (the user id) — an
    identity pointer, not a cache of role/permissions. Role and
    is_active are read fresh from the database on every request.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {"sub": str(user_id), "iat": now, "exp": expires}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID | None:
    """The user id a valid, unexpired token was issued for, or None."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None
