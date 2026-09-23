"""
Processing & Read-Model API Schemas — app/schemas/processing.py

Response payloads for the processing pipeline and the read endpoints
that power the frontend (status polling, invoice details, history,
dashboard summary).

Design decisions:
- Money and confidence are floats here: these are display-layer
  contracts. Precision-critical Decimals live in the database and the
  validation layer; the API serializes for human consumption.
- DocumentStatusData.is_terminal saves every client from re-deriving
  the terminal-status set.
- InvoiceDetailData deliberately includes the developer-panel payloads
  (raw OCR text, raw structured output, LLM/validation metadata) so the
  detail view needs exactly one request.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


class ProcessAccepted(BaseModel):
    """Returned by POST /invoices/process (202)."""

    document_id: uuid.UUID
    filename: str
    status: str = Field(description="Initial document status (UPLOADED).")
    status_url: str = Field(description="Poll this endpoint for live progress.")


class InvoiceDeleteResult(BaseModel):
    """Returned by DELETE /invoices/{id}."""

    invoice_id: uuid.UUID
    document_id: uuid.UUID
    deleted: bool = True


class DocumentPhoto(BaseModel):
    """One photo of an intake, with its own OCR outcome."""

    page_number: int
    filename: str
    mime_type: str
    file_size_bytes: int
    source_type: str | None = None
    mean_confidence: float | None = None
    text_chars: int | None = None


REVIEW_NONE = "NONE"                    # the invoice raised no proposals
REVIEW_PENDING = "PENDING"              # at least one proposal awaits a decision
REVIEW_APPROVED = "APPROVED"            # every proposal decided, at least one approved
REVIEW_REJECTED = "REJECTED"            # every proposal decided, all rejected


class InvoiceReviewProposal(BaseModel):
    id: uuid.UUID
    entity_key: str
    field: str
    current_value: Any | None = None
    proposed_value: Any
    status: str
    source: str
    proposed_by: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    revised_from: uuid.UUID | None = Field(
        default=None, description="The proposal this one corrected, when it is a revision."
    )


class InvoiceReviewSummary(BaseModel):
    """
    What Data Review holds for ONE invoice, built from its proposal rows —
    the same rows the review queue and the product history show. This is
    the invoice's review state; the store's identity status is a
    different question and is never folded in here.
    """

    status: str = Field(description="NONE, PENDING, APPROVED or REJECTED (see constants).")
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    proposals: list[InvoiceReviewProposal] = Field(default_factory=list)

    @classmethod
    def from_proposals(cls, rows: list[Any]) -> InvoiceReviewSummary:
        pending = sum(1 for r in rows if r.status == "PENDING")
        approved = sum(1 for r in rows if r.status == "APPROVED")
        rejected = sum(1 for r in rows if r.status == "REJECTED")
        if not rows:
            status = REVIEW_NONE
        elif pending:
            status = REVIEW_PENDING
        elif approved:
            status = REVIEW_APPROVED
        else:
            status = REVIEW_REJECTED
        return cls(
            status=status, pending=pending, approved=approved, rejected=rejected,
            proposals=[InvoiceReviewProposal(
                id=r.id, entity_key=r.entity_key, field=r.field, current_value=r.current_value,
                proposed_value=r.proposed_value, status=r.status, source=r.source,
                proposed_by=r.proposed_by, reviewed_by=r.reviewed_by, reviewed_at=r.reviewed_at,
                review_note=r.review_note,
                revised_from=((r.evidence or {}).get("revised_from") or None),
            ) for r in rows],
        )


class DuplicateDecision(BaseModel):
    """Body of POST /invoices/{id}/items/{sort_order}/duplicate-decision."""

    decision: Literal["same_row", "separate_rows"] = Field(
        description=(
            "'same_row': the flagged row is the earlier row seen again in an overlapping photo — "
            "it is kept for audit, typed 'duplicate', and leaves the subtotal and the EDI. "
            "'separate_rows': two legitimate rows — the flag is cleared, both stay."
        ),
    )
    decided_by: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class DuplicateDecisionResult(BaseModel):
    sort_order: int
    of_sort_order: int
    decision: str
    line_type: str
    status: str
    failed_checks: int
    review_reasons: list[str]
    pdi_export_allowed: bool
    pdi_export_blocked_reason: str | None = None


class CaseMappingRow(BaseModel):
    """One line item's units-per-case state, for the review UI."""

    item_code: str | None = Field(
        default=None, description="Normalized UPC; null when the line has no usable code."
    )
    description: str | None = None
    pack_size: str | None = Field(
        default=None, description="Pack descriptor as printed, shown as evidence."
    )
    units_per_case: int | None = Field(
        default=None, description="Confirmed units per case, when a mapping exists."
    )
    suggested_units_per_case: int | None = Field(
        default=None,
        description="Document-derived suggestion; requires confirmation before use.",
    )
    suggestion_source: str | None = Field(
        default=None,
        description=(
            "Where the displayed value came from: 'database' (confirmed "
            "mapping, the only source ever used for an EDI), 'pack_size' (a "
            "dedicated pack column), 'reference' (derived from the store's own per-unit cost), 'description' (N/M notation in the "
            "description), or 'description_ambiguous' (a form such as "
            "'4/6/16OZ' whose leading number does not settle units-per-case). "
            "Null when nothing could be suggested."
        ),
    )
    suggestion_candidates: list[int] = Field(
        default_factory=list,
        description=(
            "Units-per-case readings a structurally ambiguous description could "
            "support, smallest first (e.g. '4/6/16OZ' -> [4, 24]). Empty when the "
            "packaging is unambiguous. The UI offers these instead of prefilling, "
            "so an ambiguous product cannot be confirmed with a single click."
        ),
    )
    reference_description: str | None = Field(
        default=None,
        description=(
            "Product name in the store's own catalogue, when matched by exact "
            "UPC. A hint for the operator; never used to match automatically."
        ),
    )
    reference_avg_cost: float | None = Field(
        default=None,
        description=(
            "The store's per-selling-unit cost, when known. Evidence behind a "
            "reference-derived suggestion; never written to an EDI."
        ),
    )
    pending_proposal_id: str | None = Field(
        default=None,
        description=(
            "Id of a submitted, not-yet-reviewed proposal for this product. "
            "Present means an operator has confirmed a value and it is awaiting "
            "data-team approval; the export stays blocked until then."
        ),
    )
    pending_value: int | None = Field(
        default=None, description="The units-per-case awaiting review, when one is queued."
    )
    mapped: bool = Field(description="False when this product still needs a mapping.")


class CaseMappingConfirmation(BaseModel):
    """One product's units-per-case, as confirmed by a person."""

    item_code: str = Field(min_length=1, description="Normalized UPC / item code.")
    units_per_case: int = Field(ge=1, le=9999, description="Units contained in one case.")
    description: str | None = Field(
        default=None, description="Product description, stored to identify the row later."
    )


class CaseMappingRequest(BaseModel):
    """Body of POST /invoices/{id}/case-mappings — confirm one or many."""

    mappings: list[CaseMappingConfirmation] = Field(
        min_length=1, description="Products to confirm; several can be resolved at once."
    )


class CaseMappingResult(BaseModel):
    """
    Returned after an operator confirms values: the invoice's refreshed
    state. `saved` counts PROPOSALS submitted for review, not mappings
    written — nothing an operator does here reaches the EDI until a
    reviewer approves it.
    """

    saved: int = Field(description="Proposals submitted for review.")
    case_mappings: list[CaseMappingRow]
    pdi_export_allowed: bool
    pdi_export_blocked_reason: str | None = None


class LineItemCorrection(BaseModel):
    """
    Body of PATCH /invoices/{id}/items/{sort_order}.

    Only the transaction values a person may legitimately need to supply
    when extraction could not associate them. Deliberately narrow: this
    is a correction path for a handful of unreadable figures, not an
    invoice editor. Units-per-case is not here — it has its own confirm
    endpoint and its own authority table.

    Every field is optional; at least one must be given. A field left out
    keeps its extracted value.
    """

    # Range is enforced in the endpoint rather than with a Field
    # constraint: a `ge` on a Decimal field puts a Decimal into the
    # validation error context, which the shared exception handler cannot
    # serialize (it raises TypeError and returns 500 instead of 422).
    unit_price: Decimal | None = Field(
        default=None, description="Net cost per unit, as printed. Must not be negative."
    )
    quantity: Decimal | None = Field(
        default=None, description="Quantity delivered, as printed. Must not be negative."
    )
    line_total: Decimal | None = Field(
        default=None,
        description="Extended total for the line, as printed. Must not be negative.",
    )
    unit_deposit: Decimal | None = Field(
        default=None,
        description=(
            "Per-unit container deposit, as printed. NOT part of product cost "
            "and never written to an EDI — it is what lets reconciliation "
            "prove (cost + deposit) x quantity = line total on layouts that "
            "fold the deposit into the extended total."
        ),
    )

    unit_discount: Decimal | None = Field(
        default=None, description="Per-unit discount, as printed. Must not be negative."
    )
    description: str | None = Field(default=None, min_length=1, max_length=500)
    product_code: str | None = Field(
        default=None, max_length=64, description="UPC / item code as printed. Empty string clears it."
    )
    # Who and why. Optional so the single-value correction path that predates
    # attribution keeps working; the UI always sends them and the history
    # entry records whatever was given.
    corrected_by: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=500)

    # API field name -> ORM column. The column predates the extraction
    # schema's `unit_deposit` naming; the API uses the clearer name and
    # records that name in corrected_fields, so provenance reads the way
    # the operator saw it.
    COLUMNS: ClassVar[dict[str, str]] = {
        "unit_price": "unit_price",
        "quantity": "quantity",
        "line_total": "line_total",
        "unit_deposit": "deposit",
        "unit_discount": "discount",
        "description": "description",
        "product_code": "product_sku",
    }
    NUMERIC: ClassVar[tuple[str, ...]] = ("unit_price", "quantity", "line_total", "unit_deposit", "unit_discount")

    def updates(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field in self.NUMERIC:
            value = getattr(self, field)
            if value is not None:
                out[field] = value
        if self.description is not None:
            out["description"] = self.description.strip()
        if self.product_code is not None:
            out["product_code"] = self.product_code.strip() or None
        return out


class CorrectionEntry(BaseModel):
    """One manual change, as recorded in a row's or the invoice's history."""

    field: str
    old: Any | None = None
    new: Any | None = None
    by: str | None = None
    at: datetime
    note: str | None = None


class CorrectedLineItem(BaseModel):
    """One line item after correction, with its provenance."""

    sort_order: int
    description: str
    product_code: str | None = None
    quantity: float
    unit_price: float | None = None
    line_total: float | None = None
    unit_deposit: float | None = None
    unit_discount: float | None = None
    line_type: str = "product"
    entry_source: str = "extracted"
    corrected_fields: list[str] = Field(
        default_factory=list,
        description="Fields on this line replaced by a person, never by extraction.",
    )
    correction_history: list[CorrectionEntry] = Field(default_factory=list)


class LineItemCreate(BaseModel):
    """
    Body of POST /invoices/{id}/items — a person adds a row the photos
    missed. Invoice-scoped data only: it never becomes master data.
    """

    description: str = Field(min_length=1, max_length=500)
    product_code: str | None = Field(default=None, max_length=64)
    pack_size: str | None = Field(default=None, max_length=64)
    quantity: Decimal
    unit_price: Decimal | None = None
    unit_deposit: Decimal | None = None
    unit_discount: Decimal | None = None
    line_total: Decimal | None = None
    added_by: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class LineItemVoid(BaseModel):
    """Body of DELETE /invoices/{id}/items/{sort_order} — the row is kept, typed 'voided'."""

    voided_by: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class InvoiceTotalsCorrection(BaseModel):
    """
    Body of PATCH /invoices/{id}/totals — a person corrects printed header
    figures the extraction misread. Every field optional; at least one.
    The extracted value is kept in the history entry, never overwritten
    silently.
    """

    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    discount_amount: Decimal | None = None
    deposit_total: Decimal | None = None
    fuel_surcharge: Decimal | None = None
    grand_total: Decimal | None = None
    corrected_by: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)

    FIELDS: ClassVar[tuple[str, ...]] = (
        "subtotal", "tax_amount", "discount_amount", "deposit_total", "fuel_surcharge", "grand_total",
    )

    def updates(self) -> dict[str, Decimal]:
        return {f: getattr(self, f) for f in self.FIELDS if getattr(self, f) is not None}


class InvoiceDateCorrection(BaseModel):
    """
    Body of PATCH /invoices/{id}/date — a person enters the invoice date
    read off the document, or states that it cannot be determined (null).
    The date is never inferred from upload, processing, file or payment
    dates; if the document does not show it, it stays unknown.
    """

    invoice_date: date | None = None
    corrected_by: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class InvoiceDateCorrectionResult(BaseModel):
    invoice_date: date | None = None
    corrected_fields: list[str] = Field(default_factory=list)
    correction_history: list[CorrectionEntry] = Field(default_factory=list)
    status: str
    composite_confidence: float
    failed_checks: int
    review_reasons: list[str] = Field(default_factory=list)
    pdi_export_allowed: bool
    pdi_export_blocked_reason: str | None = None


class InvoiceTotalsCorrectionResult(BaseModel):
    subtotal: float | None = None
    tax_amount: float | None = None
    discount_amount: float | None = None
    deposit_total: float | None = None
    fuel_surcharge: float | None = None
    grand_total: float | None = None
    corrected_fields: list[str] = Field(default_factory=list)
    correction_history: list[CorrectionEntry] = Field(default_factory=list)
    status: str
    composite_confidence: float
    failed_checks: int
    review_reasons: list[str] = Field(default_factory=list)
    pdi_export_allowed: bool
    pdi_export_blocked_reason: str | None = None


class LineItemCorrectionResult(BaseModel):
    """The corrected line plus the invoice's re-judged state."""

    item: CorrectedLineItem
    status: str = Field(description="VALIDATED or REVIEW_REQUIRED after revalidation.")
    composite_confidence: float
    failed_checks: int
    review_reasons: list[str] = Field(default_factory=list)
    pdi_export_allowed: bool
    pdi_export_blocked_reason: str | None = None


# ---------------------------------------------------------------------------
# Master-data proposals / review
# ---------------------------------------------------------------------------


class ProposalRow(BaseModel):
    """One proposal as the review queue lists it."""

    id: uuid.UUID
    store: StoreRef
    entity_type: str
    entity_key: str
    field: str
    proposed_value: Any
    current_value: Any | None = None
    source: str
    source_file: str | None = None
    source_sheet: str | None = None
    source_row: int | None = None
    invoice_id: uuid.UUID | None = None
    description: str | None = Field(
        default=None,
        description="The product as the evidence names it (invoice description, else reference description).",
    )
    invoice_deleted: bool = Field(
        default=False,
        description=(
            "The invoice this proposal was raised on no longer exists. The proposal is "
            "immutable history and stays; the mapping it may have written stays too."
        ),
    )
    proposed_by: str
    status: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    created_at: datetime


class ResultingMapping(BaseModel):
    store: StoreRef
    item_code: str
    units_per_case: int
    source: str
    approved_proposal_id: uuid.UUID | None = None
    updated_at: datetime


class ProposalDetail(ProposalRow):
    """A proposal with its evidence and, when approved, the mapping it produced."""

    evidence: dict[str, Any] | None = None
    reason: str | None = None
    # Present when this proposal is the one an authoritative mapping
    # points at. Says what the reviewer's decision actually created.
    resulting_mapping: ResultingMapping | None = None
    # The mapping's value NOW — may differ from proposed_value if a later
    # proposal superseded this one.
    current_master_value: Any | None = None


class ProposalDecision(BaseModel):
    """Body of POST /proposals/{id}/approve and /reject."""

    reviewed_by: str = Field(
        min_length=1, max_length=128,
        description=(
            "Who is deciding. There is no login yet; the name is recorded as given, "
            "which is the same contract the review CLI's --by uses."
        ),
    )
    note: str | None = Field(default=None, max_length=2000)


class ProposalDecisionResult(BaseModel):
    proposal: ProposalDetail
    applied_to: str | None = Field(
        default=None, description="e.g. 'product_case_mappings:01820011030' on approval."
    )


class BulkProposalDecision(BaseModel):
    """Body of POST /proposals/bulk-approve and /bulk-reject — the review table's fast path."""

    proposal_ids: list[uuid.UUID] = Field(
        min_length=1, max_length=200,
        description="The selected proposals. Decided all-or-nothing: one stale row refuses the batch.",
    )
    reviewed_by: str = Field(min_length=1, max_length=128, description="Entered once, recorded on every row.")
    note: str | None = Field(default=None, max_length=2000)


class BulkDecisionOutcome(BaseModel):
    id: uuid.UUID
    entity_key: str
    status: str
    applied_to: str | None = None


class BulkDecisionResult(BaseModel):
    reviewed_by: str
    decided: list[BulkDecisionOutcome]

    @property
    def count(self) -> int:
        return len(self.decided)


class ProposalRevision(BaseModel):
    """
    Body of POST /proposals/{id}/revise — a reviewer correcting a pending value.

    Units-per-case is the only revisable field today, hence the integer.
    The proposal is not edited: a new pending proposal is created and the
    original is frozen as superseded. Approval is a separate, governed step.
    """

    proposed_value: int = Field(ge=1, le=9999)
    proposed_by: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=2000)


class ProposalRevisionResult(BaseModel):
    proposal: ProposalDetail = Field(description="The new PENDING proposal carrying the corrected value.")
    superseded: ProposalDetail = Field(description="The original, now REJECTED with a note naming its successor.")


class ProductHistory(BaseModel):
    """Everything that ever happened to one product's reusable data, in one store."""

    store: StoreRef
    item_code: str
    current_mapping: ResultingMapping | None = None
    proposals: list[ProposalDetail]


# ---------------------------------------------------------------------------
# Requires Mapping — the collaborative work queue
# ---------------------------------------------------------------------------


class MappingQueueOccurrence(BaseModel):
    """One invoice line waiting on this product's mapping."""

    invoice_id: uuid.UUID
    document_id: uuid.UUID
    invoice_number: str | None = None
    description: str | None = None
    quantity: float
    unit_price: float | None = None
    pack_size: str | None = None


class MappingQueueRow(BaseModel):
    """
    One master-data gap: a (store, UPC) with a product line on some
    invoice and no authoritative product_case_mappings row. Does not
    require a proposal to exist — that is the point of this queue.
    """

    store: StoreRef
    item_code: str
    description: str | None = None
    invoice_count: int = Field(description="Distinct invoices carrying this unresolved product.")
    occurrences: list[MappingQueueOccurrence]
    pending_proposal_id: str | None = Field(
        default=None,
        description="A submitted, not-yet-reviewed proposal already exists for this product, if any.",
    )
    pending_value: int | None = None
    pending_proposed_by: str | None = Field(
        default=None, description="Who submitted the pending proposal, when one exists.",
    )


class MappingQueueSummary(BaseModel):
    """Precise counts for the Requires Mapping badge and page header."""

    unique_products: int = Field(description="Distinct (store, UPC) pairs still needing a mapping.")
    invoice_occurrences: int = Field(
        description="Total invoice lines waiting on one of those products, across every invoice."
    )
    stores: int = Field(description="Distinct stores with at least one unresolved product.")


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


class StoreRef(BaseModel):
    """
    How a store is named wherever an invoice, mapping or proposal shows
    it. `label` is the confirmed name, or the source code marked as not
    yet confirmed — never a guessed name. `source_codes` are the Item
    Sales store codes, shown as provenance.
    """

    id: uuid.UUID
    label: str
    identity_status: str
    display_name: str | None = None
    address: str | None = None
    source_codes: list[str] = Field(default_factory=list)

    @classmethod
    def from_store(cls, store: Any) -> StoreRef:
        return cls(
            id=store.id, label=store.label, identity_status=store.identity_status,
            display_name=store.display_name, address=store.address_summary,
            source_codes=store.source_codes,
        )


class StoreIdentifierOut(BaseModel):
    source_system: str
    identifier_type: str
    identifier_value: str
    verified: bool = Field(description="Whether a person vouched for this identifier.")


class StoreDirectoryEntry(StoreRef):
    """One row of the Store Directory."""

    customer_name: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    status: str
    notes: str | None = None
    identifiers: list[StoreIdentifierOut] = Field(default_factory=list)
    invoices: int = 0
    pricing_rows: int = 0
    identities: int = 0
    catalogue_rows: int = 0
    case_mappings: int = 0
    pending_proposals: int = 0


class StoreIdentityUpdate(BaseModel):
    """
    A person confirming (or correcting) a store's human identity. Every
    field optional; `confirm=True` marks the identity confirmed.
    """

    display_name: str | None = Field(default=None, max_length=128)
    customer_name: str | None = Field(default=None, max_length=128)
    address_line_1: str | None = Field(default=None, max_length=128)
    address_line_2: str | None = Field(default=None, max_length=128)
    city: str | None = Field(default=None, max_length=64)
    state: str | None = Field(default=None, max_length=32)
    postal_code: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=2000)
    confirm: bool = False
    confirmed_by: str | None = Field(default=None, max_length=128)


class StoreCandidateOut(BaseModel):
    """A store the document's text names, with the evidence that matched."""

    store_id: uuid.UUID
    label: str
    identity_status: str
    address: str | None = None
    matched_on: list[dict[str, Any]] = Field(default_factory=list)


class StoreConfirmation(BaseModel):
    """Body of POST /documents/{id}/confirm-store."""

    store_id: uuid.UUID
    confirmed_by: str | None = Field(default=None, max_length=128)


class StoreDeferral(BaseModel):
    """
    Body of POST /documents/{id}/defer-store — read the document now,
    assign the store later. The invoice is persisted STORE_PENDING.
    """

    deferred_by: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class StoreAssignment(BaseModel):
    """Body of POST /invoices/{id}/assign-store — a person names the store of a pending invoice."""

    store_id: uuid.UUID
    assigned_by: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)


class StoreCreate(BaseModel):
    """
    Body of POST /stores — a person adds a store to the directory.

    A store is a location a person can name. No source code is attached
    here: linking an Item Sales / POS code to it is a separate, explicit
    step, never inferred from an invoice.
    """

    display_name: str = Field(min_length=1, max_length=128)
    customer_name: str | None = Field(default=None, max_length=128)
    address_line_1: str | None = Field(default=None, max_length=128)
    address_line_2: str | None = Field(default=None, max_length=128)
    city: str | None = Field(default=None, max_length=64)
    state: str | None = Field(default=None, max_length=32)
    postal_code: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=2000)
    created_by: str = Field(min_length=1, max_length=128)
    confirm: bool = Field(default=False, description="Mark the identity confirmed by created_by.")


class StageEntry(BaseModel):
    """One processing-log entry in the document timeline."""

    stage: str
    status: str
    message: str | None = None
    duration_ms: int | None = None
    created_at: datetime
    payload: dict[str, Any] | None = None


class ReprocessResultData(BaseModel):
    """
    Outcome of re-extracting a stored document under the active prompt.
    The document and invoice ids are unchanged — that is the point.
    """

    document_id: uuid.UUID
    invoice_id: uuid.UUID
    attempt: int = Field(description="1 is the original run; each reprocess increments.")
    run_id: str = Field(description="Stamped on every processing-log entry this attempt wrote.")
    prompt_version: str
    model: str | None = None
    decision: str
    document_status: str
    line_item_count: int
    review_reasons: list[str] = Field(default_factory=list)


class DocumentStatusData(BaseModel):
    """Live document status — drives the processing timeline."""

    document_id: uuid.UUID
    filename: str
    status: str
    is_terminal: bool
    source_type: str | None = None
    store: StoreRef | None = Field(
        default=None, description="The store this upload is for, once chosen or confirmed."
    )
    awaiting_store_confirmation: bool = Field(
        default=False,
        description="True while the run is paused for a person to confirm the store.",
    )
    store_candidates: list[StoreCandidateOut] = Field(
        default_factory=list,
        description="What identification found in the text; empty when nothing matched.",
    )
    invoice_id: uuid.UUID | None = None
    photos: list[DocumentPhoto] = Field(default_factory=list)
    error: dict[str, Any] | None = Field(
        default=None, description="Failure log payload when status is FAILED."
    )
    stages: list[StageEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Invoice details
# ---------------------------------------------------------------------------


class LineItemData(BaseModel):
    description: str
    quantity: float
    # Null when extraction could not read the figure. Distinct from 0.00,
    # and never coerced — a line with an unknown cost blocks PDI export
    # rather than being exported as free goods (see migration 0005).
    unit_price: float | None = None
    line_total: float | None = None
    tax_rate: float | None = None
    unit_deposit: float | None = Field(
        default=None,
        description=(
            "Per-unit container deposit as extracted. Null when it could not "
            "be read — which prevents reconciliation from explaining a line "
            "whose total includes it."
        ),
    )
    sort_order: int = 0
    line_type: str = Field(
        default="product",
        description="'product' or 'charge' — a charge (delivery/fuel/service) is never a PDI product record.",
    )
    product_code: str | None = None
    unit_discount: float | None = None
    entry_source: str = Field(default="extracted", description="'extracted' or 'manual'.")
    correction_history: list[CorrectionEntry] = Field(default_factory=list)
    source_pages: list[int] = Field(
        default_factory=list,
        description="Photo numbers (1-based) this row was read from; empty for single-file intakes.",
    )
    duplicate_candidate: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Set when the model could not tell whether this row is the same physical row as "
            "an earlier one (overlapping photos). {of_sort_order, reason, resolution, "
            "decided_by, decided_at, note}; resolution null until a reviewer decides."
        ),
    )
    corrected_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Transaction fields on this line replaced by a person. Empty means "
            "every value came from extraction."
        ),
    )


class VendorData(BaseModel):
    id: uuid.UUID
    name: str
    tax_id: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None


class DatabaseConfirmation(BaseModel):
    """Persistence proof for the demo's database panel."""

    vendor_saved: bool
    invoice_saved: bool
    items_saved: int
    logs_saved: int
    duplicate_check_passed: bool
    processing_duration_ms: int


class InvoiceDetailData(BaseModel):
    """Full invoice detail. `store` is None while the invoice is STORE_PENDING."""
    """Everything the invoice detail view needs in one request."""

    invoice_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    document_status: str
    source_type: str | None = None
    store: StoreRef | None = Field(default=None, description="None while STORE_PENDING.")
    store_pending: bool = Field(
        default=False,
        description=(
            "The invoice was read before its store was known. No reference data, case "
            "mappings, proposals or EDI until a person assigns the store."
        ),
    )

    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str
    subtotal: float | None = None
    tax_amount: float | None = None
    discount_amount: float | None = None
    deposit_total: float | None = None
    fuel_surcharge: float | None = None
    corrected_fields: list[str] = Field(
        default_factory=list, description="Header fields a person replaced; the extracted value is in the history."
    )
    correction_history: list[CorrectionEntry] = Field(default_factory=list)
    grand_total: float | None = None

    status: str = Field(description="Invoice decision status: VALIDATED or REVIEW_REQUIRED.")
    composite_confidence: float | None = None
    extraction_model: str | None = None
    created_at: datetime

    pdi_export_allowed: bool = Field(
        description="Whether GET .../export?format=pdi will succeed for this invoice."
    )
    pdi_export_requires_confirmation: bool = Field(
        description=(
            "True for REVIEW_REQUIRED invoices: the export is allowed, but the "
            "client should confirm with the user before downloading, since the "
            "underlying data may contain extraction inaccuracies."
        )
    )
    pdi_export_blocked_reason: str | None = Field(
        default=None, description="Why the export is blocked, when pdi_export_allowed is false."
    )
    photos: list[DocumentPhoto] = Field(
        default_factory=list,
        description="The photos of a multi-photo intake, in operator order; empty for one file.",
    )
    duplicate_review_required: bool = Field(
        default=False,
        description="An unresolved cross-photo duplicate candidate blocks this invoice.",
    )
    review: InvoiceReviewSummary = Field(
        description=(
            "This invoice's master-data review, from its proposal rows. Separate from the "
            "store's identity status and from the validation status."
        ),
    )
    case_mappings: list[CaseMappingRow] = Field(
        default_factory=list,
        description=(
            "Per-line units-per-case state. Any row with mapped=false must be "
            "confirmed before the PDI export is allowed."
        ),
    )

    vendor: VendorData | None = None
    line_items: list[LineItemData] = Field(default_factory=list)

    validation_report: dict[str, Any] | None = None
    llm_metadata: dict[str, Any] | None = None
    database: DatabaseConfirmation | None = Field(
        default=None, description="ADMIN-only technical/persistence diagnostics; null for MANAGER/USER."
    )

    # Developer panel
    ocr_text: str | None = None
    raw_extraction: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# History & dashboard
# ---------------------------------------------------------------------------


class HistoryRow(BaseModel):
    """One row in processing history / recent activity."""

    document_id: uuid.UUID
    invoice_id: uuid.UUID | None = None
    filename: str
    store: StoreRef | None = None
    status: str = Field(description="Document lifecycle status.")
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    grand_total: float | None = None
    currency: str | None = None
    composite_confidence: float | None = None
    source_type: str | None = None
    photo_count: int = Field(default=1, description="Photos in the intake; 1 for a single file.")
    review: InvoiceReviewSummary | None = Field(
        default=None, description="Master-data review state for this invoice, when it has one.",
    )
    mapping_required: int | None = Field(
        default=None,
        description=(
            "Products on this invoice with no confirmed units-per-case mapping. Null when "
            "not applicable (no invoice yet, store not yet assigned, or MANAGER/ADMIN-only "
            "field hidden from this role)."
        ),
    )
    edi_status: str | None = Field(
        default=None,
        description=(
            "'ready' (exportable, no confirmation needed), 'needs_confirmation' (exportable "
            "pending an explicit confirm — see pdi_export_requires_confirmation on invoice "
            "detail), 'blocked' (not exportable yet), or null when not applicable."
        ),
    )
    created_at: datetime


class DashboardData(BaseModel):
    """Executive summary powering the dashboard."""

    total_documents: int
    completed: int
    review_required: int
    failed: int
    in_progress: int
    success_rate: float | None = Field(
        default=None, description="COMPLETED / terminal documents; null before any finish."
    )
    average_confidence: float | None = None
    average_processing_ms: float | None = None
    total_tokens: int = 0
    total_estimated_cost_usd: float = 0.0
    status_breakdown: dict[str, int] = Field(default_factory=dict)
    recent: list[HistoryRow] = Field(default_factory=list)
