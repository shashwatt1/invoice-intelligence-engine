"""
Recycle Bin — app/models/recycle_bin.py

Two append-only audit tables (migration 0037).

recycle_bin_entries — one row per time a document went into the bin:
who binned it, when, why, and the status it had. It closes exactly once:
RESTORED (back to that status), RESTORED_AS_REFERENCE (an administrator
restored it as a read-only reference copy because an active document holds
the same invoice) or PURGED (permanently deleted by an administrator). It
keeps a snapshot of what the document was (file name and hash, invoice
number, vendor, store), so a PURGED row still explains what was deleted after
the document itself is gone (document_id becomes NULL). An administrator's
direct delete of a document that was never binned is recorded the same way,
as an entry created already PURGED.

bin_deletion_requests — a non-administrator asking an administrator to
delete a binned document permanently, with a reason. It is decided exactly
once: APPROVED (the administrator deleted it), REJECTED (it stays in the
bin), or CANCELLED (withdrawn by the requester, or closed because the
document was restored or deleted another way).

Neither table is ever edited outside those transitions, nor deleted from.
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ENTRY_IN_BIN = "IN_BIN"
ENTRY_RESTORED = "RESTORED"
ENTRY_RESTORED_AS_REFERENCE = "RESTORED_AS_REFERENCE"
ENTRY_PURGED = "PURGED"
ENTRY_STATUSES = frozenset({ENTRY_IN_BIN, ENTRY_RESTORED, ENTRY_RESTORED_AS_REFERENCE, ENTRY_PURGED})
ENTRY_CLOSED = ENTRY_STATUSES - {ENTRY_IN_BIN}

REQUEST_PENDING = "PENDING"
REQUEST_APPROVED = "APPROVED"
REQUEST_REJECTED = "REJECTED"
REQUEST_CANCELLED = "CANCELLED"
REQUEST_STATUSES = frozenset({REQUEST_PENDING, REQUEST_APPROVED, REQUEST_REJECTED, REQUEST_CANCELLED})


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


ENTRY_CHECKS = {
    "ck_recycle_bin_entries_status": f"status IN ({_in(ENTRY_STATUSES)})",
    "ck_recycle_bin_entries_reason": "length(trim(reason)) > 0",
    "ck_recycle_bin_entries_closed": (
        f"(status = '{ENTRY_IN_BIN}' AND closed_at IS NULL AND closed_by IS NULL AND closed_by_role IS NULL "
        "AND closed_by_user_id IS NULL AND close_note IS NULL) OR "
        f"(status <> '{ENTRY_IN_BIN}' AND closed_at IS NOT NULL AND closed_by IS NOT NULL "
        "AND closed_by_role IS NOT NULL AND closed_at >= binned_at)"),
    # Only a permanently deleted document leaves its entry without one.
    "ck_recycle_bin_entries_document": f"status = '{ENTRY_PURGED}' OR document_id IS NOT NULL",
}
REQUEST_CHECKS = {
    "ck_bin_deletion_requests_status": f"status IN ({_in(REQUEST_STATUSES)})",
    "ck_bin_deletion_requests_reason": "length(trim(reason)) > 0",
    "ck_bin_deletion_requests_decided": (
        f"(status = '{REQUEST_PENDING}' AND decided_at IS NULL AND decided_by IS NULL AND decided_by_role IS NULL "
        "AND decided_by_user_id IS NULL AND decision_note IS NULL) OR "
        f"(status <> '{REQUEST_PENDING}' AND decided_at IS NOT NULL AND decided_by IS NOT NULL "
        "AND decided_by_role IS NOT NULL AND decided_at >= requested_at)"),
}


class RecycleBinEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recycle_bin_entries"

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True,
        doc="NULL only once the document was permanently deleted (PURGED).")
    # What the document was, kept so a PURGED entry still explains itself.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True,
                                                         doc="Snapshot; no foreign key.")
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    store_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stores.id", ondelete="SET NULL"), nullable=True)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    previous_status: Mapped[str] = mapped_column(String(50), nullable=False)

    binned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    binned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    binned_by: Mapped[str] = mapped_column(String(64), nullable=False)
    binned_by_role: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ENTRY_IN_BIN)
    # A new upload of the same file while this entry was in the bin.
    reuploaded_as_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closed_by_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    close_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        *(CheckConstraint(sql, name=name) for name, sql in ENTRY_CHECKS.items()),
        # A document is in the bin at most once at a time.
        Index("uq_recycle_bin_entries_open", "document_id", unique=True,
              postgresql_where=text(f"status = '{ENTRY_IN_BIN}'")),
        Index("idx_recycle_bin_entries_status", "status", "binned_at"),
        Index("idx_recycle_bin_entries_document", "document_id"),
        Index("idx_recycle_bin_entries_file_hash", "file_hash"),
    )


class BinDeletionRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bin_deletion_requests"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recycle_bin_entries.id", ondelete="RESTRICT"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by_role: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=REQUEST_PENDING)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_by_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        *(CheckConstraint(sql, name=name) for name, sql in REQUEST_CHECKS.items()),
        # At most one request waits for an administrator per bin entry.
        Index("uq_bin_deletion_requests_pending", "entry_id", unique=True,
              postgresql_where=text(f"status = '{REQUEST_PENDING}'")),
        Index("idx_bin_deletion_requests_status", "status", "requested_at"),
    )


class ImmutableBinHistoryError(RuntimeError):
    """A write that would edit or delete Recycle Bin history outside an allowed transition."""


ENTRY_CLOSE_FIELDS = frozenset({"status", "closed_at", "closed_by_user_id", "closed_by", "closed_by_role",
                                "close_note"})
REQUEST_DECIDE_FIELDS = frozenset({"status", "decided_at", "decided_by_user_id", "decided_by",
                                   "decided_by_role", "decision_note"})


def _changed(target) -> set[str]:
    return {a.key for a in inspect(target).attrs if a.history.has_changes()} - {"updated_at"}


@event.listens_for(RecycleBinEntry, "before_update")
def _entries_change_only_by_closing(_mapper, connection, target: RecycleBinEntry) -> None:
    """While in the bin, an entry may record a re-upload or close once; a closed entry never changes."""
    table = RecycleBinEntry.__table__
    stored = connection.execute(select(table.c.status).where(table.c.id == target.id)).scalar()
    changed = _changed(target)
    if stored != ENTRY_IN_BIN:
        raise ImmutableBinHistoryError(f"A {stored} bin entry cannot be changed.")
    if target.status == ENTRY_IN_BIN:
        if changed - {"reuploaded_as_document_id"}:
            raise ImmutableBinHistoryError(f"An open bin entry may only record a re-upload (tried {sorted(changed)}).")
    elif target.status not in ENTRY_CLOSED or changed - ENTRY_CLOSE_FIELDS - {"reuploaded_as_document_id"}:
        raise ImmutableBinHistoryError(f"Closing a bin entry may not change {sorted(changed - ENTRY_CLOSE_FIELDS)}.")


@event.listens_for(BinDeletionRequest, "before_update")
def _requests_change_only_by_decision(_mapper, connection, target: BinDeletionRequest) -> None:
    table = BinDeletionRequest.__table__
    stored = connection.execute(select(table.c.status).where(table.c.id == target.id)).scalar()
    changed = _changed(target)
    if stored != REQUEST_PENDING or target.status == REQUEST_PENDING or changed - REQUEST_DECIDE_FIELDS:
        raise ImmutableBinHistoryError("A deletion request changes only once, from PENDING to its decision.")


@event.listens_for(RecycleBinEntry, "before_delete")
@event.listens_for(BinDeletionRequest, "before_delete")
def _bin_history_is_never_deleted(_mapper, _connection, _target) -> None:
    raise ImmutableBinHistoryError("Recycle Bin history is never deleted.")
