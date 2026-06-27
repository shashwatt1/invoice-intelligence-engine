"""
SQLAlchemy Declarative Base — app/database/base.py

Defines the shared declarative base and a mixin with common columns
(id, created_at, updated_at) that all business models inherit from.

Design decisions:
- UUIDs are used as primary keys instead of integers. This avoids
  sequential ID leakage and is required for distributed systems.
- gen_random_uuid() is called server-side, not by Python, so IDs are
  generated atomically with the INSERT statement.
- Timestamps use TIMESTAMPTZ (timezone-aware) to avoid ambiguity.
- The `TimestampMixin` keeps models DRY — no table repeats these columns.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Shared declarative base for all ORM models.

    All SQLAlchemy models in this application must inherit from this class.
    It provides the metadata registry that Alembic reads for migrations.
    """

    pass


class TimestampMixin:
    """
    Mixin that adds created_at and updated_at to any model.

    - created_at: Set once at INSERT time by the database server.
    - updated_at: Updated automatically on every UPDATE by the database server.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        doc="Timestamp when the record was first created (UTC).",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        doc="Timestamp when the record was last modified (UTC).",
    )


class UUIDPrimaryKeyMixin:
    """
    Mixin that adds a UUID primary key generated server-side.

    Using gen_random_uuid() means the database generates the key,
    keeping insertion atomic and avoiding round-trips for ID fetching.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        doc="Universally unique identifier for this record.",
    )
