"""
Auth / user-management schemas — app/schemas/auth.py

Never includes password_hash anywhere, and every password field is
write-only (accepted, never echoed back in any response).

Username normalization is deliberately NOT the same on every schema:
LoginRequest only lowercases/strips (app.models.user.normalize_username)
— a malformed login username must fail to match a row and read as an
ordinary "incorrect username or password", not a distinguishable 422
that would let a caller tell "bad format" apart from "wrong password".
CreateUserRequest enforces the full format (validate_username) because
creating an account is where a bad username should actually be refused.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.user import InvalidUsernameError, UserRole, normalize_username, validate_username


class LoginRequest(BaseModel):
    username: str
    password: str = Field(min_length=1, max_length=200)

    @field_validator("username")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return normalize_username(value)


class UserOut(BaseModel):
    """A user as shown back to a client. Never the password hash."""

    id: uuid.UUID
    username: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CreateUserRequest(BaseModel):
    username: str
    password: str = Field(min_length=8, max_length=200)
    role: UserRole

    @field_validator("username")
    @classmethod
    def _validate(cls, value: str) -> str:
        try:
            return validate_username(value)
        except InvalidUsernameError as exc:
            raise ValueError(str(exc)) from exc


class ChangeUserRoleRequest(BaseModel):
    role: UserRole


class SetUserActiveRequest(BaseModel):
    is_active: bool
