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
  | "FAILED";

export type InvoiceDecision = "VALIDATED" | "REVIEW_REQUIRED";

export type PipelineStage =
  | "UPLOAD"
  | "TEXT_EXTRACTION"
  | "STORE_IDENTIFICATION"
  | "AI_STRUCTURING"
  | "VALIDATION"
  | "PERSISTENCE";

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
export interface StoreRef {
  id: string;
  label: string;
  identity_status: "confirmed" | "unresolved";
  display_name: string | null;
  address: string | null;
  source_codes: string[];
}

export interface StoreIdentifier {
  source_system: string;
  identifier_type: string;
  identifier_value: string;
  verified: boolean;
}

export interface StoreDirectoryEntry extends StoreRef {
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
  error: { stage?: PipelineStage; message?: string; error_code?: string } | null;
  stages: StageEntry[];
  created_at: string;
  updated_at: string;
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
}

/** Body of PATCH /invoices/{id}/items/{sort_order}. Only the transaction
 * values a person may need to supply when OCR could not associate them. */
export interface LineItemCorrection {
  unit_price?: string;
  quantity?: string;
  line_total?: string;
  unit_deposit?: string;
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
  store: StoreRef;

  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  currency: string;
  subtotal: number | null;
  tax_amount: number | null;
  discount_amount: number | null;
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
  case_mappings: CaseMappingRow[];

  vendor: Vendor | null;
  line_items: LineItem[];

  validation_report: ValidationReport | null;
  llm_metadata: LlmMetadata | null;
  database: DatabaseConfirmation;

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

export interface ProposalDecision {
  reviewed_by: string;
  note?: string | null;
}

export interface ProposalDecisionResult {
  proposal: ProposalDetail;
  applied_to: string | null;
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
