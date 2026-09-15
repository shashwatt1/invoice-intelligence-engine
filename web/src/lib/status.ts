/**
 * Status vocabulary — the single mapping from backend status strings to
 * labels and visual tone, shared by badges, charts, and the timeline.
 */

import type {
  CheckStatus,
  DocumentStatus,
  PipelineStage,
  ProposalSource,
  ProposalStatus,
} from "@/api/types";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

export const STATUS_META: Record<DocumentStatus, { label: string; tone: Tone }> = {
  UPLOADED: { label: "Uploaded", tone: "info" },
  OCR_IN_PROGRESS: { label: "Extracting text", tone: "info" },
  OCR_COMPLETED: { label: "Text extracted", tone: "info" },
  AI_PROCESSING: { label: "AI structuring", tone: "info" },
  VALIDATED: { label: "Validated", tone: "success" },
  REVIEW_REQUIRED: { label: "Review required", tone: "warning" },
  STORE_CONFIRMATION_REQUIRED: { label: "Confirm store", tone: "warning" },
  COMPLETED: { label: "Completed", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
};

/** Proposal lifecycle. The labels say what each state MEANS for the EDI,
 * because "pending" and "approved" look alike in a table and are not. */
export const PROPOSAL_STATUS_META: Record<
  ProposalStatus,
  { label: string; tone: Tone; meaning: string }
> = {
  PENDING: {
    label: "Pending review",
    tone: "warning",
    meaning: "In the queue. Not master data — no EDI uses this value.",
  },
  APPROVED: {
    label: "Approved",
    tone: "success",
    meaning: "A reviewer promoted it. It wrote the authoritative mapping the EDI uses.",
  },
  REJECTED: {
    label: "Rejected",
    tone: "danger",
    meaning: "A reviewer declined it. Master data was not touched.",
  },
};

/** Where a proposed value came from — assigned by the backend, never the
 * client. Ordered strongest evidence first. */
export const PROPOSAL_SOURCE_META: Record<ProposalSource, { label: string; blurb: string }> = {
  beer_inventory_explicit: {
    label: "Typed items/case",
    blurb: "A typed items-per-case cell in the store's Beer Inventory workbook.",
  },
  beer_inventory_package: {
    label: "Package string",
    blurb: "Decoded from a two-fraction package string such as 24/12OZ 2/12 CANS.",
  },
  reference_derived: {
    label: "Reference-derived",
    blurb: "Derived from the store's own cost or retail data against the invoice case cost.",
  },
  document_derived: {
    label: "Document",
    blurb: "The invoice's own pack descriptor or N/M notation, confirmed as printed.",
  },
  document_ambiguous: {
    label: "Document (ambiguous)",
    blurb: "The invoice notation supported several readings; an operator chose one.",
  },
  operator_entered: {
    label: "Operator-entered",
    blurb: "Typed by an operator with no supporting suggestion, or against the suggestion.",
  },
  legacy_migrated: {
    label: "Legacy",
    blurb: "Existed before the approval workflow; grandfathered into the history.",
  },
};

export const CHECK_META: Record<CheckStatus, { tone: Tone; symbol: string }> = {
  PASSED: { tone: "success", symbol: "✓" },
  FAILED: { tone: "danger", symbol: "✕" },
  WARNING: { tone: "warning", symbol: "!" },
  SKIPPED: { tone: "neutral", symbol: "—" },
};

export const PIPELINE_STEPS: { stage: PipelineStage; title: string; description: string }[] = [
  { stage: "UPLOAD", title: "Upload", description: "File validated, hashed & stored" },
  { stage: "TEXT_EXTRACTION", title: "Text extraction", description: "Digital PDF parsing or OCR" },
  { stage: "STORE_IDENTIFICATION", title: "Store identification", description: "Which store the document names; a person confirms" },
  { stage: "AI_STRUCTURING", title: "AI structuring", description: "Structured extraction via OpenAI" },
  { stage: "VALIDATION", title: "Validation", description: "Math checks & confidence scoring" },
  { stage: "PERSISTENCE", title: "Persistence", description: "Vendor, invoice & items saved" },
];

/** Which timeline step is currently active for a non-terminal status. */
export const ACTIVE_STEP_BY_STATUS: Partial<Record<DocumentStatus, PipelineStage>> = {
  UPLOADED: "TEXT_EXTRACTION",
  OCR_IN_PROGRESS: "TEXT_EXTRACTION",
  OCR_COMPLETED: "STORE_IDENTIFICATION",
  STORE_CONFIRMATION_REQUIRED: "STORE_IDENTIFICATION",
  AI_PROCESSING: "AI_STRUCTURING",
  VALIDATED: "PERSISTENCE",
  REVIEW_REQUIRED: "PERSISTENCE",
};

export const TONE_CLASSES: Record<Tone, string> = {
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  info: "bg-info-soft text-info",
  neutral: "bg-muted text-muted-foreground",
};
