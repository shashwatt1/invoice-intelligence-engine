"""
Per-user physical-store access — app/models/user_store_access.py

One row is one grant: an account may use inventory for one physical store.
Grants are audit history as well as the access rule, so a row is never
edited or deleted:

  * a grant records who granted it, in which role, when and why;
  * a revocation stamps the row once, with who, in which role, when and why;
    a revoked row can never change again;
  * granting the same store again after a revocation inserts a new row.

At most one active (unrevoked) grant exists per account and store (partial
unique index). Access itself is decided in app.services.store_access; the
account's role and active state are checked there on every request, so a
demoted or deactivated account loses access immediately even though its
grant rows remain as history.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# The only columns a revocation may set, once, on an active grant.
REVOCATION_FIELDS = frozenset({"revoked_at", "revoked_by_user_id", "revoked_by", "revoked_by_role", "revoke_reason"})


class UserStoreAccess(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An account's access to one physical store's inventory: granted once, revoked at most once."""

    __tablename__ = "user_store_access"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        doc="The account granted access. RESTRICT: grant history is never removed with an account.",
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, doc="The account's username when granted.")
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    granted_by: Mapped[str] = mapped_column(String(64), nullable=False, doc="The granting account's username.")
    granted_by_role: Mapped[str] = mapped_column(String(16), nullable=False)
    grant_reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Set once, when the grant is revoked; the row itself is kept.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_by_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("length(trim(grant_reason)) > 0", name="ck_user_store_access_grant_reason"),
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_by IS NULL AND revoked_by_role IS NULL AND revoke_reason IS NULL "
            "AND revoked_by_user_id IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_by_role IS NOT NULL "
            "AND revoke_reason IS NOT NULL AND length(trim(revoke_reason)) > 0 AND revoked_at >= granted_at)",
            name="ck_user_store_access_revocation",
        ),
        # One active grant per account and store; revoked grants stay as history.
        Index("uq_user_store_access_active", "user_id", "store_id", unique=True,
              postgresql_where=text("revoked_at IS NULL")),
        Index("idx_user_store_access_user", "user_id"),
        Index("idx_user_store_access_store", "store_id"),
    )

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class ImmutableGrantError(RuntimeError):
    """A write that would edit grant history (anything but a single revocation of an active grant)."""


@event.listens_for(UserStoreAccess, "before_update")
def _grants_are_append_only(_mapper, connection, target: UserStoreAccess) -> None:
    """
    Refuse any update except stamping the revocation fields on an active grant.
    The service already locks and checks; this keeps grant history immutable
    whatever code path writes the row.

    Whether the row is already revoked is read from the database row itself,
    in the flush's own transaction, never from the ORM's loaded state: a
    change to another revocation field (say revoke_reason) leaves revoked_at
    untouched, so its attribute history alone cannot tell.
    """
    table = UserStoreAccess.__table__
    stored_revoked_at = connection.execute(
        select(table.c.revoked_at).where(table.c.id == target.id)
    ).scalar()
    if stored_revoked_at is not None:
        raise ImmutableGrantError("A revoked store-access grant cannot be changed.")
    state = inspect(target)
    changed = {attr.key for attr in state.attrs if attr.history.has_changes()} - {"updated_at"}
    if changed - REVOCATION_FIELDS:
        raise ImmutableGrantError(f"Only a revocation may change a store-access grant (tried: {sorted(changed)}).")


@event.listens_for(UserStoreAccess, "before_delete")
def _grants_are_never_deleted(_mapper, _connection, _target: UserStoreAccess) -> None:
    raise ImmutableGrantError("Store-access grants are history and are never deleted; revoke instead.")
