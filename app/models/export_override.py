"""
Export override ledger — app/models/export_override.py

An ADMIN's recorded decision to let ONE exact state of an invoice be exported although its critical checks
(completeness, financial reconciliation) fail. The override never changes the invoice: its status, its
`blocking_checks`, its confidence and its validation log are untouched. It only records who allowed the export,
why, and exactly what was failing at that moment (`validation_snapshot`), so the failed results survive whatever
happens next.

It covers a fingerprint of the invoice as it was reviewed (`state_sha256`). Any correction, revalidation that
changes a figure, or reprocess changes the fingerprint and the override stops applying by itself; it must be
granted again after another look. Rows are written once; the only later change is revocation (who, when, why).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ExportOverride(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "export_overrides"

    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True,
        doc="The invoice the override covers. Kept as history if the invoice is later deleted.",
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, doc="Why the export is allowed; required.")
    blocked_checks: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, doc="The critical checks that were failing when the override was granted.",
    )
    state_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="Fingerprint of the invoice's figures, lines and failing checks as reviewed; the override applies "
            "only while the invoice still matches it.",
    )
    validation_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        doc="The latest stored validation report at grant time, verbatim: failed checks, completeness, "
            "confidence and review reasons, plus the invoice status.",
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    granted_by: Mapped[str] = mapped_column(String(64), nullable=False, doc="The signed-in account's username.")
    granted_by_role: Mapped[str] = mapped_column(String(16), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_export_overrides_invoice", "invoice_id", "created_at"),
    )
