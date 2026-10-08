/**
 * API contracts — one-to-one mirrors of the FastAPI response schemas
 * (app/schemas/processing.py, app/schemas/base.py). The backend is the
 * single source of truth; nothing here adds or reinterprets fields.
 */

export type DocumentStatus =
  | "UPLOADED"
  | "OCR_IN_PROGRESS"
  | "OCR_COMPLETED"
  | "AI_PROCESSING"
  | "VALIDATED"
  | "REVIEW_REQUIRED"
  | "STORE_CONFIRMATION_REQUIRED"
  | "COMPLETED"
  | "FAILED"
  | "STOPPED"
  | "BINNED";

export type InvoiceDecision = "VALIDATED" | "REVIEW_REQUIRED";

export type PipelineStage =
  | "UPLOAD"
  | "TEXT_EXTRACTION"
  | "STORE_IDENTIFICATION"
  | "AI_STRUCTURING"
  | "VALIDATION"
  | "PERSISTENCE"
  | "MANUAL_CORRECTION";

export type CheckStatus = "PASSED" | "FAILED" | "WARNING" | "SKIPPED";

// ---------------------------------------------------------------------------
// Envelopes (app/schemas/base.py)
// ---------------------------------------------------------------------------

export interface ApiErrorDetail {
  error_code: string;
  message: string;
  detail?: unknown;
}

export interface ApiEnvelope<T> {
  success: boolean;
  data: T | null;
  error: ApiErrorDetail | null;
  request_id: string | null;
}

export interface Paginated<T> {
  success: boolean;
  items: T[];
  total: number;
  page: number;
  page_size: number;
  request_id: string | null;
}

// ---------------------------------------------------------------------------
// Stores (app/api/v1/stores.py)
// ---------------------------------------------------------------------------

/**
 * How a store is named wherever an invoice, mapping or proposal shows it.
 * `label` is the confirmed name, or the source code marked as not yet
 * confirmed — never a guessed name. `source_codes` are the Item Sales
 * store codes, shown as provenance.
 */
/** What a source identity is: a source system's own identifier (e.g. Item Sales store code), not a physical store. */
export interface SourceIdentityRef {
  source_system: string;
  /** The source system as a person names it, e.g. "Item Sales". */
  source_label: string;
  identifier_type: string;
  identifier_value: string;
  /** e.g. "Item Sales · 47708760". */
  label: string;
}

export interface StoreRef {
  id: string;
  label: string;
  identity_status: "confirmed" | "unresolved";
  display_name: string | null;
  address: string | null;
  source_codes: string[];
  /** The Store Master classification; "source_identity" is not a physical store. */
  kind?: "physical" | "source_identity";
  source_identity?: SourceIdentityRef | null;
}

export interface StoreIdentifier {
  source_system: string;
  identifier_type: string;
  identifier_value: string;
  verified: boolean;
}

export interface StoreDirectoryEntry extends StoreRef {
  /** "physical": a location. "source_identity": known only by a source-system code — not a physical store. */
  kind: "physical" | "source_identity";
  /** True when the operator's CStorePro store directory names this store. */
  in_store_directory: boolean;
  customer_name: string | null;
  address_line_1: string | null;
  address_line_2: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  status: string;
  notes: string | null;
  identifiers: StoreIdentifier[];
  invoices: number;
  pricing_rows: number;
  identities: number;
  catalogue_rows: number;
  case_mappings: number;
  pending_proposals: number;
}

/** Body of POST /stores — a person adds a store (a location) to the directory. */
export interface StoreCreate {
  display_name: string;
  customer_name?: string | null;
  address_line_1?: string | null;
  address_line_2?: string | null;
  city?: string | null;
  state?: string | null;
  postal_code?: string | null;
  notes?: string | null;
  created_by: string;
  confirm?: boolean;
}

/** assign-store returns the full detail; alias kept for readability at call sites. */
export type StoreAssignmentResult = InvoiceDetail;

export interface StoreIdentityUpdate {
  display_name?: string | null;
  customer_name?: string | null;
  address_line_1?: string | null;
  address_line_2?: string | null;
  city?: string | null;
  state?: string | null;
  postal_code?: string | null;
  notes?: string | null;
  confirm?: boolean;
  confirmed_by?: string | null;
}

/** A store the document's text names, with the evidence that matched. */
export interface StoreCandidate {
  store_id: string;
  label: string;
  identity_status: "confirmed" | "unresolved";
  address: string | null;
  matched_on: { kind: string; value: string; source_system: string; verified: boolean }[];
}

// ---------------------------------------------------------------------------
// Processing (app/schemas/processing.py)
// ---------------------------------------------------------------------------

export interface ProcessAccepted {
  document_id: string;
  filename: string;
  status: DocumentStatus;
  status_url: string;
}

export interface StageEntry {
  stage: PipelineStage;
  status: "SUCCESS" | "FAILURE";
  message: string | null;
  duration_ms: number | null;
  created_at: string;
  payload: Record<string, unknown> | null;
}

export interface DocumentStatusData {
  document_id: string;
  filename: string;
  status: DocumentStatus;
  is_terminal: boolean;
  source_type: string | null;
  /** The store this upload is for, once chosen or confirmed. */
  store: StoreRef | null;
  /** True while the run is paused for a person to confirm the store. */
  awaiting_store_confirmation: boolean;
  /** What identification found in the text; empty when nothing matched. */
  store_candidates: StoreCandidate[];
  invoice_id: string | null;
  /** The photos of a multi-photo intake, in order; empty for one file. */
  photos: DocumentPhoto[];
  error: { stage?: PipelineStage; message?: string; error_code?: string } | null;
  stages: StageEntry[];
  created_at: string;
  updated_at: string;
}

export interface DocumentPhoto {
  page_number: number;
  filename: string;
  mime_type: string;
  file_size_bytes: number;
  source_type: string | null;
  mean_confidence: number | null;
  text_chars: number | null;
}

export interface DuplicateCandidate {
  of_sort_order: number;
  reason: string | null;
  resolution: "same_row" | "separate_rows" | null;
  decided_by?: string | null;
  decided_at?: string | null;
  note?: string | null;
}

export type InvoiceReviewStatus = "NONE" | "PENDING" | "APPROVED" | "REJECTED";

export interface InvoiceReviewProposal {
  id: string;
  entity_key: string;
  field: string;
  current_value: unknown | null;
  proposed_value: unknown;
  status: ProposalStatus;
  source: ProposalSource;
  proposed_by: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  revised_from: string | null;
}

/** This invoice's master-data review, from its proposal rows. Separate from
 * the store's identity status and from the validation status. */
export interface InvoiceReviewSummary {
  status: InvoiceReviewStatus;
  pending: number;
  approved: number;
  rejected: number;
  proposals: InvoiceReviewProposal[];
}

export interface DuplicateDecision {
  decision: "same_row" | "separate_rows";
  decided_by: string;
  note?: string | null;
}

export interface DuplicateDecisionResult {
  sort_order: number;
  of_sort_order: number;
  decision: string;
  line_type: string;
  status: string;
  failed_checks: number;
  review_reasons: string[];
  pdi_export_allowed: boolean;
  pdi_export_blocked_reason: string | null;
}

// ---------------------------------------------------------------------------
// Invoice detail
// ---------------------------------------------------------------------------

export interface LineItem {
  description: string;
  quantity: number;
  /** Null when extraction could not read the figure — distinct from 0.00.
   * A line with an unknown cost blocks the PDI export rather than being
   * sent as free goods. */
  unit_price: number | null;
  line_total: number | null;
  tax_rate: number | null;
  /** Per-unit container deposit. Not product cost and never in an EDI —
   * it is what lets reconciliation explain a line whose extended total
   * includes it. */
  unit_deposit: number | null;
  sort_order: number;
  /** "product", "charge", "duplicate" (resolved as seen twice) or "voided" (a person removed it). */
  line_type: string;
  product_code: string | null;
  /** The product's normalized name: the Product Master canonical description when one exists,
   * otherwise this line's own `description`. Never a PDI description — none is held. */
  normalized_description?: string | null;
  normalized_description_source?: NormalizedDescriptionSource | null;
  unit_discount: number | null;
  /** "extracted" or "manual" (a person added the row). */
  entry_source: string;
  correction_history: CorrectionEntry[];
  /** Photo numbers (1-based) this row was read from; empty for a single file. */
  source_pages: number[];
  /** Set when the model could not tell whether this row is the same physical
   * row as an earlier one (overlapping photos). Unresolved until a reviewer
   * decides; the invoice is REVIEW_REQUIRED meanwhile. */
  duplicate_candidate: DuplicateCandidate | null;
  /** Transaction fields on this line replaced by a person. Empty means
   * every value came from extraction. */
  corrected_fields: string[];
}

export interface Vendor {
  id: string;
  name: string;
  tax_id: string | null;
  address: string | null;
  phone: string | null;
  email: string | null;
  /** Vendor Master: the canonical name a person confirmed; `name` stays the observed name. */
  display_name?: string | null;
  identity_status?: VendorIdentityStatus;
}

// ---------------------------------------------------------------------------
// Vendor Master — canonical vendor identity beside what invoices printed
// ---------------------------------------------------------------------------

export type VendorIdentityStatus = "unresolved" | "confirmed";

export interface VendorRow {
  id: string;
  /** The confirmed canonical name, else the name first observed on an invoice. */
  label: string;
  /** The name first observed on an invoice — what extraction matches on. */
  name: string;
  display_name: string | null;
  identity_status: VendorIdentityStatus;
  tax_id: string | null;
  invoices: number;
  /** Distinct vendor wordings printed on this vendor's invoices. */
  observed_names: number;
  last_seen_at: string | null;
}

export interface VendorObservedName {
  name: string;
  invoices: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export interface VendorInvoiceRef {
  invoice_id: string;
  document_id: string;
  invoice_number: string | null;
  invoice_date: string | null;
  observed_vendor_name: string | null;
  grand_total: string | null;
  store: StoreRef | null;
}

export interface VendorIdentityReviewEntry {
  decision: "CONFIRM" | "REOPEN";
  previous_status: VendorIdentityStatus;
  new_status: VendorIdentityStatus;
  previous_display_name: string | null;
  new_display_name: string | null;
  reviewer: string;
  reviewer_role: string | null;
  basis: string;
  decided_at: string;
}

export interface VendorDetail extends VendorRow {
  address: string | null;
  phone: string | null;
  email: string | null;
  observed_name_list: VendorObservedName[];
  observed_tax_ids: { tax_id: string; invoices: number }[];
  recent_invoices: VendorInvoiceRef[];
  history: VendorIdentityReviewEntry[];
  /** Reprocessed readings that named another vendor (or none); the confirmed vendor was kept. */
  discrepancies?: VendorDiscrepancy[];
}

export interface VendorDiscrepancy {
  invoice_id: string;
  invoice_number: string | null;
  observed_vendor_name: string | null;
  observed_vendor_tax_id: string | null;
  recorded_at: string;
}

export interface VendorListParams {
  identity_status?: VendorIdentityStatus;
  search?: string;
  page?: number;
  page_size?: number;
}

export interface VendorDecision {
  vendor_id: string;
  previous_status: VendorIdentityStatus;
  new_status: VendorIdentityStatus;
  display_name: string | null;
}

export interface DatabaseConfirmation {
  vendor_saved: boolean;
  invoice_saved: boolean;
  items_saved: number;
  logs_saved: number;
  duplicate_check_passed: boolean;
  processing_duration_ms: number;
}

export interface ValidationCheck {
  name: string;
  status: CheckStatus;
  field?: string;
  message?: string;
  expected?: string;
  actual?: string;
}

export interface ValidationReport {
  decision: InvoiceDecision;
  confidence: {
    composite: number;
    ocr_confidence: number | null;
    ai_confidence: number | null;
    validation_score: number;
    weights: Record<string, number>;
  };
  review_reasons: string[];
  summary: { passed: number; failed: number; warnings: number; skipped: number };
  checks: ValidationCheck[];
  validated_at: string;
  duration_ms: number;
}

export interface LlmMetadata {
  provider: string;
  model: string;
  request_id: string | null;
  finish_reason: string | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
  created_at: string;
  prompt_version: string;
  ocr_text_truncated: boolean;
}

/**
 * One product's units-per-case state on this invoice.
 *
 * `units_per_case` is the confirmed value from the mapping table — the
 * only value the EDI is allowed to use. `suggested_units_per_case` is
 * parsed from the document's pack descriptor and is a suggestion for a
 * person to confirm, never applied on its own.
 */
/**
 * Where a displayed units-per-case value came from. Only "database" is
 * ever used to build an EDI; the rest are suggestions a person must
 * confirm, and "description_ambiguous" marks a package notation whose
 * leading number does not settle the question (e.g. "4/6/16OZ").
 */
export type SuggestionSource =
  | "database"
  | "reference_explicit"    // a typed items/case cell in the store's workbook
  | "reference_package"     // decoded from a two-fraction package string
  | "reference_ratio"       // a source's own case cost ÷ unit cost
  | "reference_retail"      // invoice case cost vs the store's own unit retail
  | "pack_size"
  | "description"
  | "description_ambiguous";

export type NormalizedDescriptionSource =
  | "PRODUCT_MASTER_CANONICAL_DESCRIPTION"
  | "PRODUCT_MASTER_CANONICAL_NAME"
  | "INVOICE_DESCRIPTION";

/** Which path produced a line's units per case. Only the first two are Product Master authoritative. */
export type CommercialResolutionPath =
  | "PRODUCT_MASTER_PHYSICAL_STORE"
  | "PRODUCT_MASTER_GLOBAL"
  | "LEGACY_FALLBACK"
  | "REQUIRES_MAPPING"
  | "CONFLICT";

export interface CaseMappingRow {
  /** Normalized UPC — the mapping key. Null when the line has no usable code. */
  item_code: string | null;
  description: string | null;
  pack_size: string | null;
  units_per_case: number | null;
  suggested_units_per_case: number | null;
  suggestion_source: SuggestionSource | null;
  /** Readings a structurally ambiguous description could support, smallest
   * first ("4/6/16OZ" -> [4, 24]). Empty when packaging is unambiguous.
   * Offered as choices instead of prefilling, so an ambiguous product
   * cannot be confirmed with a single click. */
  suggestion_candidates: number[];
  /** Product name in the store's own catalogue, when matched by exact UPC.
   * A hint for the operator; never used to match automatically. */
  reference_description: string | null;
  /** The store's per-selling-unit cost — the evidence behind a
   * reference-derived suggestion. Never written to an EDI. */
  reference_avg_cost: number | null;
  /** A submitted-but-unreviewed value. Shown so the operator knows it is
   * in the queue; NEVER used for the EDI — only an approved mapping is. */
  pending_proposal_id: string | null;
  pending_value: number | null;
  mapped: boolean;
  resolution_path?: CommercialResolutionPath | null;
  /** Where the approved mapping applied from: the physical store (an override), or
   * "Global (distributor evidence, held under Item Sales · 47708760)". */
  resolution_scope?: string | null;
  resolution_mapping_id?: string | null;
  resolution_notes?: string[];
}

/** Body of PATCH /invoices/{id}/items/{sort_order}. Only the transaction
 * values a person may need to supply when OCR could not associate them. */
export interface LineItemCorrection {
  unit_price?: string;
  quantity?: string;
  line_total?: string;
  unit_deposit?: string;
  unit_discount?: string;
  description?: string;
  product_code?: string;
  /** Who and why — recorded on the row's history. */
  corrected_by?: string | null;
  note?: string | null;
}

/** One manual change in a row's or the invoice's history. */
export interface CorrectionEntry {
  field: string;
  old: unknown | null;
  new: unknown | null;
  by: string | null;
  at: string;
  note: string | null;
}

export interface LineItemCreate {
  description: string;
  product_code?: string | null;
  pack_size?: string | null;
  quantity: string;
  unit_price?: string | null;
  unit_deposit?: string | null;
  unit_discount?: string | null;
  line_total?: string | null;
  added_by: string;
  note?: string | null;
}

export interface InvoiceDateCorrection {
  /** ISO date read off the document, or null when it genuinely cannot be determined. */
  invoice_date: string | null;
  corrected_by: string;
  note?: string | null;
}

export interface InvoiceDateCorrectionResult {
  invoice_date: string | null;
  corrected_fields: string[];
  correction_history: CorrectionEntry[];
  status: string;
  composite_confidence: number;
  failed_checks: number;
  review_reasons: string[];
  pdi_export_allowed: boolean;
  pdi_export_blocked_reason: string | null;
}

export interface InvoiceTotalsCorrection {
  subtotal?: string;
  tax_amount?: string;
  discount_amount?: string;
  deposit_total?: string;
  fuel_surcharge?: string;
  grand_total?: string;
  corrected_by: string;
  note?: string | null;
}

export interface InvoiceTotalsCorrectionResult {
  subtotal: number | null;
  tax_amount: number | null;
  discount_amount: number | null;
  deposit_total: number | null;
  fuel_surcharge: number | null;
  grand_total: number | null;
  corrected_fields: string[];
  correction_history: CorrectionEntry[];
  status: string;
  composite_confidence: number;
  failed_checks: number;
  review_reasons: string[];
  pdi_export_allowed: boolean;
  pdi_export_blocked_reason: string | null;
}

export interface LineItemCorrectionResult {
  item: {
    sort_order: number;
    description: string;
    quantity: number;
    unit_price: number | null;
    line_total: number | null;
    unit_deposit: number | null;
    corrected_fields: string[];
  };
  status: string;
  composite_confidence: number;
  failed_checks: number;
  review_reasons: string[];
  pdi_export_allowed: boolean;
  pdi_export_blocked_reason: string | null;
}

export interface CaseMappingConfirmation {
  item_code: string;
  units_per_case: number;
  description?: string | null;
}

/** Response of POST /invoices/{id}/case-mappings — the refreshed state.
 * `saved` counts PROPOSALS submitted for review, not mappings written. */
export interface CaseMappingResult {
  saved: number;
  case_mappings: CaseMappingRow[];
  pdi_export_allowed: boolean;
  pdi_export_blocked_reason: string | null;
}

export interface InvoiceDetail {
  invoice_id: string;
  document_id: string;
  filename: string;
  document_status: DocumentStatus;
  source_type: string | null;
  /** The store this invoice was received for. Every reference lookup,
   * case mapping and proposal on this page is that store's own. */
  /** null while STORE_PENDING — read before the store was known. */
  store: StoreRef | null;
  store_pending: boolean;

  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  currency: string;
  subtotal: number | null;
  tax_amount: number | null;
  discount_amount: number | null;
  deposit_total: number | null;
  fuel_surcharge: number | null;
  /** Header fields a person replaced; the extracted value is in the history. */
  corrected_fields: string[];
  correction_history: CorrectionEntry[];
  grand_total: number | null;

  status: InvoiceDecision;
  composite_confidence: number | null;
  extraction_model: string | null;
  created_at: string;

  /** Whether GET .../export?format=pdi will succeed for this invoice —
   * computed once on the backend so this and the export endpoint's own
   * gate can never drift apart. */
  pdi_export_allowed: boolean;
  /** True for REVIEW_REQUIRED invoices: export is allowed, but the UI
   * should confirm with the user first (possible extraction inaccuracies). */
  pdi_export_requires_confirmation: boolean;
  pdi_export_blocked_reason: string | null;
  /** Units-per-case state per product. A row with mapped=false is why
   * pdi_export_allowed is false; confirming it unblocks the download. */
  /** The photos of a multi-photo intake, in order; empty for one file. */
  photos: DocumentPhoto[];
  /** An unresolved cross-photo duplicate candidate blocks this invoice. */
  duplicate_review_required: boolean;
  review: InvoiceReviewSummary;
  case_mappings: CaseMappingRow[];

  vendor: Vendor | null;
  line_items: LineItem[];

  validation_report: ValidationReport | null;
  llm_metadata: LlmMetadata | null;
  /** ADMIN-only technical/persistence diagnostics; null for MANAGER/USER. */
  database: DatabaseConfirmation | null;

  ocr_text: string | null;
  raw_extraction: Record<string, unknown> | null;
}

// ---------------------------------------------------------------------------
// Master-data proposals / review (app/api/v1/proposals.py)
// ---------------------------------------------------------------------------

/**
 * Three states, three meanings, never conflated in the UI:
 *   PENDING  — submitted, in the queue; NOT used for any EDI.
 *   APPROVED — a reviewer promoted it; it wrote the authoritative mapping.
 *   REJECTED — a reviewer declined it; master data untouched.
 * A reviewed proposal is immutable. A changed value is a new proposal.
 */
export type ProposalStatus = "PENDING" | "APPROVED" | "REJECTED";

/** How the proposed value was arrived at — decided by the backend when the
 * proposal is created, never by the client. */
export type ProposalSource =
  | "reference_derived"
  | "document_derived"
  | "document_ambiguous"
  | "beer_inventory_explicit"
  | "beer_inventory_package"
  | "operator_entered"
  | "legacy_migrated";

export interface ProposalRow {
  id: string;
  store: StoreRef;
  entity_type: string;
  entity_key: string;
  field: string;
  proposed_value: unknown;
  current_value: unknown | null;
  source: ProposalSource;
  source_file: string | null;
  source_sheet: string | null;
  source_row: number | null;
  invoice_id: string | null;
  /** The source invoice's printed number and date, while it exists. */
  invoice_number?: string | null;
  invoice_date?: string | null;
  /** Why this store value cannot be approved: the Product Master governs or
   * disputes the same item. Such a row needs an individual decision and is
   * never part of a bulk approval. */
  product_master_block?: string | null;
  /** The product as the evidence names it. */
  description: string | null;
  /** The invoice this was raised on has since been deleted; the proposal
   * is immutable history and stays. */
  invoice_deleted: boolean;
  proposed_by: string;
  status: ProposalStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
}

/** The authoritative row a decision produced (product_case_mappings). */
export interface ResultingMapping {
  store: StoreRef;
  item_code: string;
  units_per_case: number;
  source: string;
  approved_proposal_id: string | null;
  updated_at: string;
}

export interface ProposalDetail extends ProposalRow {
  /** Shape varies by source; see EvidencePanel. Rendered as-is — the UI
   * never invents a field the backend did not record. */
  evidence: Record<string, unknown> | null;
  reason: string | null;
  /** Set only when the current mapping points back at THIS proposal. */
  resulting_mapping: ResultingMapping | null;
  /** The mapping's value right now — may differ from proposed_value when
   * a later proposal superseded this one. */
  current_master_value: unknown | null;
}

/** The reviewer is always the signed-in account; the server ignores any name sent. */
export interface ProposalDecision {
  reviewed_by?: string;
  note?: string | null;
}

export interface ProposalDecisionResult {
  proposal: ProposalDetail;
  applied_to: string | null;
}

/** Body of POST /proposals/bulk-approve and /bulk-reject — the table's
 * fast path. Decided all-or-nothing: one stale row refuses the batch. */
export interface BulkProposalDecision {
  proposal_ids: string[];
  reviewed_by?: string;
  note?: string | null;
}

export interface BulkDecisionOutcome {
  id: string;
  entity_key: string;
  status: ProposalStatus;
  applied_to: string | null;
}

export interface BulkDecisionResult {
  reviewed_by: string;
  decided: BulkDecisionOutcome[];
}

/** A reviewer's correction of a pending value. Not an edit: the backend
 * creates a new pending proposal and freezes the original as superseded. */
export interface ProposalRevision {
  proposed_value: number;
  proposed_by?: string;
  note?: string | null;
}

export interface ProposalRevisionResult {
  /** The new PENDING proposal carrying the corrected value. */
  proposal: ProposalDetail;
  /** The original, now REJECTED with a note naming its successor. */
  superseded: ProposalDetail;
}

export interface ProductHistory {
  /** A UPC's history is one store's history. */
  store: StoreRef;
  item_code: string;
  current_mapping: ResultingMapping | null;
  /** Oldest first — the audit trail. */
  proposals: ProposalDetail[];
}

export interface ProposalListParams {
  status?: ProposalStatus | "ALL";
  source?: ProposalSource;
  store_id?: string;
  item_code?: string;
  invoice_id?: string;
  /** The source invoice's printed number — every invoice carrying it. */
  invoice_number?: string;
  page?: number;
  page_size?: number;
}

/** An invoice that raised proposals: the review queue's Source invoice picker. */
export interface ProposalSourceInvoice {
  invoice_id: string;
  invoice_number: string | null;
  invoice_date: string | null;
  /** The invoice's own store — where the proposals came from. */
  store: StoreRef | null;
  proposals: number;
  pending: number;
}

// ---------------------------------------------------------------------------
// Requires Mapping — the collaborative work queue (app/api/v1/mapping_queue.py)
// ---------------------------------------------------------------------------

/** One invoice line waiting on this product's mapping. */
export interface MappingQueueOccurrence {
  invoice_id: string;
  document_id: string;
  invoice_number: string | null;
  description: string | null;
  quantity: number;
  unit_price: number | null;
  pack_size: string | null;
}

/** One master-data gap: a (store, UPC) with no authoritative mapping.
 * Does not require a proposal to exist — that is the point of this queue. */
export interface MappingQueueRow {
  store: StoreRef;
  item_code: string;
  description: string | null;
  invoice_count: number;
  occurrences: MappingQueueOccurrence[];
  pending_proposal_id: string | null;
  pending_value: number | null;
  pending_proposed_by: string | null;
}

export interface MappingQueueSummary {
  unique_products: number;
  invoice_occurrences: number;
  stores: number;
}

export interface MappingQueueListParams {
  store_id?: string;
  page?: number;
  page_size?: number;
}

// ---------------------------------------------------------------------------
// History & dashboard
// ---------------------------------------------------------------------------

export interface HistoryRow {
  document_id: string;
  invoice_id: string | null;
  filename: string;
  store: StoreRef | null;
  status: DocumentStatus;
  vendor_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  grand_total: number | null;
  currency: string | null;
  composite_confidence: number | null;
  source_type: string | null;
  photo_count: number;
  review: InvoiceReviewSummary | null;
  /** Products with no confirmed units-per-case mapping. Null when not applicable
   * (no invoice yet, store not yet assigned, or hidden for this role). MANAGER+ only. */
  mapping_required: number | null;
  /** 'ready' | 'needs_confirmation' | 'blocked', or null when not applicable. MANAGER+ only. */
  edi_status: "ready" | "needs_confirmation" | "blocked" | null;
  created_at: string;
}

export interface DashboardData {
  total_documents: number;
  completed: number;
  review_required: number;
  failed: number;
  in_progress: number;
  success_rate: number | null;
  average_confidence: number | null;
  average_processing_ms: number | null;
  total_tokens: number;
  total_estimated_cost_usd: number;
  status_breakdown: Partial<Record<DocumentStatus, number>>;
  recent: HistoryRow[];
}

export interface InvoiceListParams {
  search?: string;
  status?: DocumentStatus;
  sort_by?: string;
  descending?: boolean;
  page?: number;
  page_size?: number;
}

export type UserRole = "USER" | "MANAGER" | "ADMIN";

export interface UserAccount {
  id: string;
  username: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Product Master — commercial review (Phase 2D)
// ---------------------------------------------------------------------------

/** A current product_case_mappings row: the live EDI authority, shown as context. */
export interface LegacyMappingRef {
  item_code: string;
  units_per_case: number;
  description: string | null;
  source: string;
}

/** Where a product's display name came from. A label, never the product's identity. */
export type ProductNameBasis = "CANONICAL" | "SOURCE" | "AMBIGUOUS_SOURCE" | "UNAVAILABLE";

/** One materially distinct source wording, and every row that carried it. */
export interface ProductNameVariant {
  description: string;
  source_class: string;
  references: string[];
}

export interface ProductDescriptionView {
  role: string;
  description: string;
  source_system: string;
  source_file: string | null;
  source_sheet: string | null;
  source_row: number | null;
}

export interface CommercialCandidateRow {
  id: string;
  product_id: string;
  product_name: string | null;
  product_name_basis: ProductNameBasis;
  product_name_source: string | null;
  product_name_reference: string | null;
  product_name_variant_count: number;
  product_name_variants: ProductNameVariant[];
  canonical_identifier: string | null;
  pdi_item_code: string | null;
  store_id: string;
  store_label: string;
  store_identity_status: string;
  store_kind?: "physical" | "source_identity";
  store_source_identity?: SourceIdentityRef | null;
  commercial_unit_basis: string;
  units_accounted_for: number | null;
  case_cost: number | null;
  cost_basis: string | null;
  approval_state: string;
  evidence_state: string;
  is_conflict: boolean;
  requires_resolution: boolean;
  review_status: string;
  legacy_agreement: string;
  conflict_explanation: string | null;
  proposed_units_accounted_for: number | null;
  proposed_by: string | null;
  proposed_note: string | null;
  proposed_at?: string | null;
  /** The version a decision is checked against: how many review events the candidate has. */
  review_version?: number;
  /** The latest review event: PROPOSE, APPROVE, REJECT or REOPEN. */
  last_decision?: string | null;
  /** Whether it may be part of a multi-select approval — decided by the server. */
  bulk_eligible?: boolean;
  reviewed_by: string | null;
  reviewed_at: string | null;
  legacy_mappings: LegacyMappingRef[];
}

export interface EvidenceView {
  notes: string | null;
  source_statements: Record<string, unknown>[];
  governed_units_observed: number[];
  supporting_rows: Record<string, unknown>[];
  source_file: string | null;
  source_sheet: string | null;
  source_row: number | null;
  source_snapshot_rows: Record<string, unknown>[];
  admissible_evidence: string[];
  descriptions?: ProductDescriptionView[];
  inadmissible_evidence: string[];
}

export interface ReviewHistoryEntry {
  decision: string;
  previous_approval_state: string;
  new_approval_state: string;
  previous_commercial_unit_basis: string;
  new_commercial_unit_basis: string;
  previous_units_accounted_for: number | null;
  new_units_accounted_for: number | null;
  reviewer: string;
  /** The reviewer's role when deciding; null for decisions recorded before it was kept. */
  reviewer_role?: string | null;
  note: string | null;
  decided_at: string;
}

export interface CommercialCandidateDetail {
  candidate: CommercialCandidateRow;
  evidence: EvidenceView;
  history: ReviewHistoryEntry[];
}

export interface CommercialReviewSummary {
  review_required: number;
  pending: number;
  approved: number;
  rejected: number;
  conflicts: number;
  total: number;
}

export interface CommercialProposalRequest {
  units_accounted_for: number;
  /** What the proposal rests on — required. */
  note: string;
}

export interface CommercialReviewRequest {
  note?: string | null;
  commercial_unit_basis?: string | null;
  units_accounted_for?: number | null;
  /** The review_version the reviewer saw; a stale decision is refused (409). */
  expected_review_version: number;
}

export interface CommercialReopenRequest {
  reason: string;
  expected_review_version: number;
}

export interface CommercialBulkApprovalRequest {
  items: { mapping_id: string; expected_review_version: number }[];
  note: string;
}

export interface CommercialBulkApprovalResult {
  approved: number;
  decisions: { mapping_id: string; previous_state: string; new_state: string }[];
}

export interface CommercialCandidateParams {
  approval_state?: string;
  review_status?: string;
  commercial_unit_basis?: string;
  conflicts_only?: boolean;
  store_id?: string;
  cost_basis?: string;
  search?: string;
  page?: number;
  page_size?: number;
}

export interface IdentityUnresolvedRow {
  raw_identifier: string | null;
  source_system: string;
  source_store_identifier: string | null;
  source_file: string;
  source_sheet: string;
  source_row: number;
  description: string | null;
  reason: string;
  units_statement: string | null;
}
