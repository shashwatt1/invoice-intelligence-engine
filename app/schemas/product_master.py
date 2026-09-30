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

from app.schemas.processing import SourceIdentityRef


class LegacyMappingRef(BaseModel):
    """A current `product_case_mappings` row — the live EDI authority, shown as context."""

    item_code: str
    units_per_case: int
    description: str | None = None
    source: str


class ProductDescriptionView(BaseModel):
    """One description on record for the product — canonical or as a source wrote it."""

    role: str
    description: str
    source_system: str
    source_file: str | None = None
    source_sheet: str | None = None
    source_row: int | None = None


class ProductNameVariant(BaseModel):
    """One materially distinct source wording, and every row that carried it."""

    description: str
    source_class: str
    references: list[str] = Field(default_factory=list)


class CommercialCandidateRow(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    # What a reviewer reads to recognise the product. A label, never the
    # identity: see app/services/product_master/display_name.py.
    # "Multiple source names" when the sources disagree materially.
    product_name: str | None = None
    product_name_basis: str = "UNAVAILABLE"  # CANONICAL | SOURCE | AMBIGUOUS_SOURCE | UNAVAILABLE
    product_name_source: str | None = None        # source class compared, e.g. "distributor price sheet"
    product_name_reference: str | None = None     # e.g. "Monarch Frontline row 12"
    product_name_variant_count: int = 0           # distinct wordings when AMBIGUOUS_SOURCE
    product_name_variants: list[ProductNameVariant] = Field(default_factory=list)
    canonical_identifier: str | None
    pdi_item_code: str | None
    store_id: uuid.UUID
    store_label: str
    store_identity_status: str
    # The Store Master classification: a source identity is a source system's
    # identifier (e.g. Item Sales 47708760), not a physical store.
    store_kind: str = "physical"
    store_source_identity: SourceIdentityRef | None = None
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
    proposed_at: datetime | None = None
    # Governance: the review version a decision is checked against (the number
    # of review events so far), the latest review event, and whether the row may
    # be part of a multi-select approval — decided by the server, not the client.
    review_version: int = 0
    last_decision: str | None = None
    bulk_eligible: bool = False
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
    # Every description on record for the product, canonical first. Kept
    # beside the chosen display name so it can always be checked.
    descriptions: list[ProductDescriptionView] = Field(default_factory=list)


class ReviewHistoryEntry(BaseModel):
    decision: str
    previous_approval_state: str
    new_approval_state: str
    previous_commercial_unit_basis: str
    new_commercial_unit_basis: str
    previous_units_accounted_for: int | None
    new_units_accounted_for: int | None
    reviewer: str
    reviewer_role: str | None = None
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
    service enforces that, never the client. `note` is the decision basis
    (for a rejection, the reason) and is required; the service enforces it.
    Who decided is never part of the request: it is the authenticated session.
    """

    note: str | None = Field(default=None, max_length=1000,
                             description="The decision basis — required to approve or reject.")
    commercial_unit_basis: str | None = None
    units_accounted_for: int | None = Field(default=None, ge=1, le=9999)
    expected_review_version: int = Field(
        ge=0, description="The review_version the reviewer saw; a stale decision is refused with 409.")


class CommercialReopenRequest(BaseModel):
    """Reopen a decided candidate for reconsideration (MANAGER/ADMIN)."""

    reason: str | None = Field(default=None, max_length=1000, description="Why it is reconsidered — required.")
    expected_review_version: int = Field(ge=0, description="The review_version the reviewer saw.")


class BulkApprovalItemRequest(BaseModel):
    mapping_id: uuid.UUID
    expected_review_version: int = Field(ge=0)


class CommercialBulkApprovalRequest(BaseModel):
    """
    Approve several READY_FOR_REVIEW candidates at once. Each is approved and
    recorded individually; if any changed or needs an individual decision,
    nothing is approved.
    """

    items: list[BulkApprovalItemRequest] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=1000,
                             description="The decision basis, recorded with every approval — required.")


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


class CommercialBulkApprovalResult(BaseModel):
    approved: int
    decisions: list[CommercialReviewDecision]


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
