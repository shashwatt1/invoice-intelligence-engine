"""
EDI export ledger — app/models/edi_export.py

One row per PDI file actually delivered (downloaded), written once and never
updated: the exact bytes, their SHA-256, who exported it and when, the
commercial resolution that produced each line, and the build that ran. The
answer to "which EDI file was actually delivered for this invoice?" is a row
here, not a fresh regeneration that may differ.

A ledger, not an event store: current invoice state stays where it is. The
file is small fixed-width text, so it is kept in the row; moving large
artifacts to object storage is a later, separate step.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

FORMAT_PDI_FIXED_WIDTH = "PDI_FIXED_WIDTH"


class EdiExport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "edi_exports"

    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True,
        doc="The invoice exported. Kept as history if the invoice is later deleted.",
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    export_number: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="1, 2, … per invoice, in delivery order.",
    )
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    format_version: Mapped[str] = mapped_column(String(16), nullable=False)
    exported_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    exported_by: Mapped[str] = mapped_column(String(64), nullable=False, doc="The signed-in account's username.")
    exported_by_role: Mapped[str] = mapped_column(String(16), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, doc="The delivered file, byte for byte (CRLF kept).")
    resolution_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        doc="Per item code: the resolution path, units, mapping id and scope that produced the line.",
    )
    build: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        doc="git SHA, Alembic revision and resolution settings at export time.",
    )

    __table_args__ = (
        UniqueConstraint("invoice_id", "export_number", name="uq_edi_exports_invoice_number"),
        Index("idx_edi_exports_invoice", "invoice_id"),
        Index("idx_edi_exports_created", "created_at"),
    )
