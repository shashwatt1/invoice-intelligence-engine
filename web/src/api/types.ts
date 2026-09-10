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
  | "COMPLETED"
  | "FAILED";

export type InvoiceDecision = "VALIDATED" | "REVIEW_REQUIRED";

export type PipelineStage =
  | "UPLOAD"
  | "TEXT_EXTRACTION"
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
  | "reference"
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
  mapped: boolean;
}

/** Body of PATCH /invoices/{id}/items/{sort_order}. Only the transaction
 * values a person may need to supply when OCR could not associate them. */
export interface LineItemCorrection {
  unit_price?: string;
  quantity?: string;
  line_total?: string;
}

export interface LineItemCorrectionResult {
  item: {
    sort_order: number;
    description: string;
    quantity: number;
    unit_price: number | null;
    line_total: number | null;
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

/** Response of POST /invoices/{id}/case-mappings — the refreshed state. */
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
// History & dashboard
// ---------------------------------------------------------------------------

export interface HistoryRow {
  document_id: string;
  invoice_id: string | null;
  filename: string;
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
