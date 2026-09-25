"""
Product Master review schemas — app/schemas/product_master.py

The shapes the review workbench consumes. Deliberately business-facing:
no OCR internals, no model metadata, no raw derivation machinery — a
MANAGER reviewing master data needs the evidence and the decision, not
developer diagnostics.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LegacyMappingRef(BaseModel):
    """A current `product_case_mappings` row — the live EDI authority, shown as context."""

    item_code: str
    units_per_case: int
    description: str | None = None
    source: str


class CommercialCandidateRow(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    canonical_identifier: str | None
    pdi_item_code: str | None
    store_id: uuid.UUID
    store_label: str
    store_identity_status: str
    commercial_unit_basis: str
    units_accounted_for: int | None
    case_cost: float | None
    cost_basis: str | None
    approval_state: str
    evidence_state: str
    is_conflict: bool
    requires_resolution: bool
    review_status: str
    legacy_agreement: str
    conflict_explanation: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    proposed_units_accounted_for: int | None = None
    proposed_by: str | None = None
    proposed_note: str | None = None
    legacy_mappings: list[LegacyMappingRef] = Field(default_factory=list)


class EvidenceView(BaseModel):
    """Why the candidate says what it says, and what may never decide it."""

    notes: str | None = None
    source_statements: list[dict[str, Any]] = Field(default_factory=list)
    governed_units_observed: list[int] = Field(default_factory=list)
    supporting_rows: list[dict[str, Any]] = Field(default_factory=list)
    source_file: str | None = None
    source_sheet: str | None = None
    source_row: int | None = None
    # The raw values as the workbook held them, captured at seed time so the
    # deployed dashboard needs no access to the reference files.
    source_snapshot_rows: list[dict[str, Any]] = Field(default_factory=list)
    admissible_evidence: list[str] = Field(default_factory=list)
    inadmissible_evidence: list[str] = Field(default_factory=list)


class ReviewHistoryEntry(BaseModel):
    decision: str
    previous_approval_state: str
    new_approval_state: str
    previous_commercial_unit_basis: str
    new_commercial_unit_basis: str
    previous_units_accounted_for: int | None
    new_units_accounted_for: int | None
    reviewer: str
    note: str | None
    decided_at: datetime


class CommercialCandidateDetail(BaseModel):
    candidate: CommercialCandidateRow
    evidence: EvidenceView
    history: list[ReviewHistoryEntry] = Field(default_factory=list)


class CommercialReviewRequest(BaseModel):
    """
    A reviewer's decision. `commercial_unit_basis`/`units_accounted_for`
    are required only when the evidence did not settle the candidate — the
    service enforces that, never the client.
    """

    note: str | None = Field(default=None, max_length=1000)
    commercial_unit_basis: str | None = None
    units_accounted_for: int | None = Field(default=None, ge=1, le=9999)


class CommercialProposalRequest(BaseModel):
    """A suggested multiplier from any authenticated account. Not a decision."""

    units_accounted_for: int = Field(ge=1, le=9999)
    note: str | None = Field(default=None, max_length=1000)


class CommercialReviewDecision(BaseModel):
    mapping_id: uuid.UUID
    previous_state: str
    new_state: str
    commercial_unit_basis: str
    units_accounted_for: int | None


class CommercialReviewSummary(BaseModel):
    review_required: int
    pending: int = 0
    approved: int
    rejected: int
    conflicts: int
    total: int


class IdentityUnresolvedRow(BaseModel):
    """A reference row that never produced a canonical product."""

    raw_identifier: str | None
    source_system: str
    source_store_identifier: str | None
    source_file: str
    source_sheet: str
    source_row: int
    description: str | None
    reason: str
    units_statement: str | None = None
