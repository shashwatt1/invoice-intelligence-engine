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
  unit_price: number;
  line_total: number;
  tax_rate: number | null;
  sort_order: number;
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
