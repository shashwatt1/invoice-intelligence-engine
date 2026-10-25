"""
Identifier resolution records — app/models/identifier_resolution.py

Two APPEND-ONLY tables for the shadow identifier-resolution layer (Phase 3C).
Nothing in invoice processing, the mapping queue or the EDI writer reads or
writes them; they exist so shadow runs, rereads and human review decisions
have a durable, auditable history.

    invoice_identifier_observations   what was printed, as each source read it
    invoice_line_resolutions          what the deterministic resolver decided

Append-only is enforced twice: the migration installs triggers that raise on
UPDATE or DELETE, and the ORM refuses a flush that would update or delete a
row (see the listeners at the bottom). A rerun is a new run_id; the current
resolution of a line is its latest row. Raw extraction and OCR text are
never copied over or rewritten — these rows reference them by invoice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, event, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKeyMixin

OBS_SOURCE_LLM = "llm_observation"
OBS_SOURCE_V11 = "v11_field"
OBS_SOURCE_REREAD = "reread"
OBS_SOURCE_OPERATOR = "operator"
VALID_OBSERVATION_SOURCES = frozenset({OBS_SOURCE_LLM, OBS_SOURCE_V11, OBS_SOURCE_REREAD, OBS_SOURCE_OPERATOR})


class AppendOnlyViolationError(RuntimeError):
    """An attempt to change or remove a historical identifier record."""


class InvoiceIdentifierObservation(Base, UUIDPrimaryKeyMixin):
    """One identifier-like value as one source read it. Never updated, never deleted."""

    __tablename__ = "invoice_identifier_observations"

    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False)
    invoice_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoice_items.id", ondelete="RESTRICT"), nullable=True,
        doc="NULL for a section/document value or a printed row missing from v11.",
    )
    run_id: Mapped[uuid.UUID] = mapped_column(nullable=False, doc="One observation or reread run.")
    source: Mapped[str] = mapped_column(String(32), nullable=False, doc="See VALID_OBSERVATION_SOURCES.")
    line_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, doc="Exactly as printed/read. Never normalized.")
    printed_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role_hint: Mapped[str | None] = mapped_column(String(32), nullable=True, doc="Observational only.")
    scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    section_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position: Mapped[str | None] = mapped_column(String(16), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_identifier_obs_invoice", "invoice_id"),
        Index("idx_identifier_obs_item", "invoice_item_id"),
        Index("idx_identifier_obs_run", "run_id"),
    )


class InvoiceLineResolution(Base, UUIDPrimaryKeyMixin):
    """One resolver decision about one invoice line, in one run. Never updated, never deleted."""

    __tablename__ = "invoice_line_resolutions"

    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False)
    invoice_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoice_items.id", ondelete="RESTRICT"), nullable=True,
        doc="NULL for a printed row missing from v11.",
    )
    run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    line_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    chosen_gtin: Mapped[str | None] = mapped_column(String(14), nullable=True)
    would_be_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                                    doc="Shadow answer only; no EDI path reads it.")
    pdi_item_code: Mapped[str | None] = mapped_column(String(11), nullable=True)
    resolver_version: Mapped[str] = mapped_column(String(32), nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, doc="The full LineResolution.")
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict,
        doc="Who/what produced the row: 'resolver' run, or an approved review proposal id.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_line_resolution_invoice", "invoice_id"),
        Index("idx_line_resolution_item_created", "invoice_item_id", "created_at"),
        Index("idx_line_resolution_run", "run_id"),
        Index("idx_line_resolution_status", "status"),
    )


def _refuse(mapper, connection, target) -> None:  # noqa: ARG001 — SQLAlchemy listener signature
    raise AppendOnlyViolationError(f"{target.__tablename__} is append-only; write a new row instead.")


for _model in (InvoiceIdentifierObservation, InvoiceLineResolution):
    event.listen(_model, "before_update", _refuse)
    event.listen(_model, "before_delete", _refuse)
