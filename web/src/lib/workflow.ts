import type { InvoiceDetail } from "@/api/types";

export type StepState = "done" | "warn" | "blocked" | "active" | "idle";

export function workflowSteps(d: InvoiceDetail): { key: string; label: string; state: StepState; detail: string }[] {
  const failed = d.validation_report?.summary.failed ?? 0;
  const mapped = d.case_mappings.filter((r) => r.mapped).length;
  const pendingMappings = d.review.pending;
  const storePending = d.store_pending || !d.store;
  const masterState: StepState = storePending ? "idle" : pendingMappings ? "warn" : d.case_mappings.length && mapped === d.case_mappings.length ? "done" : d.case_mappings.length ? "warn" : "done";
  const ediState: StepState = d.pdi_export_allowed ? (d.pdi_export_requires_confirmation ? "warn" : "done") : "blocked";
  return [
    { key: "ocr", label: "OCR", state: d.ocr_text ? "done" : "idle", detail: d.ocr_text ? (d.photos.length > 1 ? `${d.photos.length} photos read` : d.source_type === "digital_pdf" ? "PDF text read" : "photo read") : "no text" },
    { key: "extract", label: "Extraction", state: d.llm_metadata ? "done" : "idle", detail: d.llm_metadata ? `${d.line_items.length} lines · ${d.llm_metadata.model}` : "—" },
    { key: "validate", label: "Validation", state: d.status === "VALIDATED" ? "done" : failed ? "warn" : "warn", detail: d.status === "VALIDATED" ? "all checks passed" : `${failed} check${failed === 1 ? "" : "s"} failed` },
    { key: "master", label: "Master data", state: masterState, detail: storePending ? "assign the store first" : pendingMappings ? `${pendingMappings} pending approval` : `${mapped}/${d.case_mappings.length} mappings approved` },
    { key: "edi", label: "EDI", state: ediState, detail: d.pdi_export_allowed ? (d.pdi_export_requires_confirmation ? "ready — review flagged" : "ready") : (d.pdi_export_blocked_reason ?? "blocked") },
  ];
}

