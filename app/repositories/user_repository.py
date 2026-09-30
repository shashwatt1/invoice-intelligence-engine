"""
User Repository — app/repositories/user_repository.py

All database read/write operations for the User aggregate. Per the
package convention: accepts an AsyncSession, flushes, never commits.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserSecurityEvent, normalize_username


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_username(self, username: str) -> User | None:
        """Case-insensitive — matches the functional unique index on lower(username)."""
        result = await self._session.execute(
            select(User).where(func.lower(User.username) == normalize_username(username))
        )
        return result.scalar_one_or_none()

    async def create(self, *, username: str, password_hash: str, role: str) -> User:
        user = User(username=normalize_username(username), password_hash=password_hash, role=role)
        self._session.add(user)
        await self._session.flush()
        return user

    async def list_all(self) -> list[User]:
        result = await self._session.execute(select(User).order_by(User.created_at))
        return list(result.scalars())

    async def set_active(self, user: User, is_active: bool) -> None:
        user.is_active = is_active
        await self._session.flush()

    async def set_role(self, user: User, role: str) -> None:
        user.role = role
        await self._session.flush()

    async def set_password_hash(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        await self._session.flush()

    async def record_security_event(self, event: UserSecurityEvent) -> UserSecurityEvent:
        """Append one security event. Never updated or deleted afterwards."""
        self._session.add(event)
        await self._session.flush()
        return event
