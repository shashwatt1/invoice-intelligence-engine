"""
app/database/__init__.py — Database package init.

Re-exports the public surface area of the database layer so that
other packages import from app.database rather than sub-modules.
"""

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.database.session import get_db, get_engine, get_session_factory

__all__ = [
    "Base",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "get_db",
    "get_engine",
    "get_session_factory",
]
