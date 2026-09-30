"""
User Model — app/models/user.py

The authenticated identity behind every protected request. Exactly
three roles (see UserRole) — no DEVELOPER role, no role inferred from
username or anything else client-supplied. The database row is the
single source of truth for a user's role; app.core.dependencies reads
it fresh on every request via the JWT's subject (user id, immutable),
never trusting the token's own claims for anything beyond identifying
which row to load.

`username` is the only human-facing authentication identifier — an
internal application, so no email, no verification, no reset flow.
`id` (UUID) remains the actual identity everywhere else: JWT subject,
document ownership FK, audit trail, authorization. Username can change
in the future without touching any of those relationships.
"""

from __future__ import annotations

import re
import uuid
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(StrEnum):
    """
    Exactly three roles. Rank order (ROLE_RANK, below) determines who
    may act as who: ADMIN outranks MANAGER outranks USER. There is no
    DEVELOPER role — ADMIN is the combined master/technical role.
    """

    USER = "USER"
    MANAGER = "MANAGER"
    ADMIN = "ADMIN"


# Higher number = more privilege. Shared by app.core.dependencies (API
# authorization) and app.services.document_lifecycle (STOP/MOVE-TO-BIN
# ownership) so both read the exact same hierarchy.
ROLE_RANK: dict[str, int] = {
    UserRole.USER.value: 1,
    UserRole.MANAGER.value: 2,
    UserRole.ADMIN.value: 3,
}

# Case-insensitive identity: "Shashwatt1", "shashwatt1" and "SHASHWATT1"
# are the same account. Normalize (strip + lower) before every lookup,
# comparison, or write — this is the one function that defines what a
# valid, canonical username looks like, shared by the Pydantic schemas
# (app.schemas.auth), the repository, and the bootstrap CLI so none of
# them can drift from another.
USERNAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,62}[a-z0-9])?$")
MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 64


class InvalidUsernameError(ValueError):
    pass


def normalize_username(raw: str) -> str:
    """
    Canonical form of a username: stripped, lowercased — nothing more.
    Used for LOOKUP (login): a malformed username should simply fail to
    match any row and read as ordinary "incorrect username or password",
    never as a distinct validation error that would let a caller tell
    "wrong format" apart from "wrong password".
    """
    return raw.strip().lower()


def validate_username(raw: str) -> str:
    """
    Normalizes AND enforces the format a username must have to be
    CREATED (bootstrap CLI, ADMIN user management): 3-64 characters,
    lowercase letters/digits/'.'/'_'/'-', never leading or trailing on a
    separator. Raises InvalidUsernameError otherwise.
    """
    value = normalize_username(raw)
    if len(value) < MIN_USERNAME_LENGTH or len(value) > MAX_USERNAME_LENGTH:
        raise InvalidUsernameError(
            f"username must be {MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} characters."
        )
    if not USERNAME_PATTERN.match(value):
        raise InvalidUsernameError(
            "username may only contain lowercase letters, digits, '.', '_', '-', "
            "and must start and end with a letter or digit."
        )
    return value


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    An authenticated account. Created only via the scripts/create_user.py
    bootstrap CLI or ADMIN user management — there is no public
    registration endpoint.
    """

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc=(
            "Login identity, always stored normalized (stripped, lowercased) — see "
            "normalize_username. Case-insensitive uniqueness is enforced by a "
            "functional unique index on lower(username) (see migration 0021), not "
            "relied on from this column alone."
        ),
    )
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False,
        doc="bcrypt hash — never the plaintext password, never logged, never returned by any API.",
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'USER'"),
        doc="One of UserRole. Explicit, stored, authoritative — never inferred from username or request data.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
        doc="False deactivates the account: it can no longer authenticate or use any protected API.",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role!r} is_active={self.is_active}>"


SECURITY_EVENT_PASSWORD_RESET = "PASSWORD_RESET"


class UserSecurityEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    One administrative security action on an account, append-only.

    Today the only action is PASSWORD_RESET: an ADMIN set another account's
    password. It records who (from the authenticated session), whose account
    and when — never the password, its confirmation or its hash. Usernames are
    kept as they were at the time, so the record survives a later rename.
    """

    __tablename__ = "user_security_events"

    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    target_username: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    actor_username: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        CheckConstraint("action IN ('PASSWORD_RESET')", name="ck_user_security_events_action"),
        Index("idx_user_security_events_target", "target_user_id"),
        Index("idx_user_security_events_created", "created_at"),
    )

