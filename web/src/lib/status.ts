/**
 * Status vocabulary — the single mapping from backend status strings to
 * labels and visual tone, shared by badges, charts, and the timeline.
 */

import type { CheckStatus, DocumentStatus, PipelineStage } from "@/api/types";

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

export const STATUS_META: Record<DocumentStatus, { label: string; tone: Tone }> = {
  UPLOADED: { label: "Uploaded", tone: "info" },
  OCR_IN_PROGRESS: { label: "Extracting text", tone: "info" },
  OCR_COMPLETED: { label: "Text extracted", tone: "info" },
  AI_PROCESSING: { label: "AI structuring", tone: "info" },
  VALIDATED: { label: "Validated", tone: "success" },
  REVIEW_REQUIRED: { label: "Review required", tone: "warning" },
  COMPLETED: { label: "Completed", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
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
  { stage: "AI_STRUCTURING", title: "AI structuring", description: "Structured extraction via OpenAI" },
  { stage: "VALIDATION", title: "Validation", description: "Math checks & confidence scoring" },
  { stage: "PERSISTENCE", title: "Persistence", description: "Vendor, invoice & items saved" },
];

/** Which timeline step is currently active for a non-terminal status. */
export const ACTIVE_STEP_BY_STATUS: Partial<Record<DocumentStatus, PipelineStage>> = {
  UPLOADED: "TEXT_EXTRACTION",
  OCR_IN_PROGRESS: "TEXT_EXTRACTION",
  OCR_COMPLETED: "AI_STRUCTURING",
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
