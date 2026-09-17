import { ApiError } from "@/api/client";
import type { ProposalRow } from "@/api/types";

/** Fields a reviewer may correct from the table — only what the backend can approve. */
export const EDITABLE_FIELDS = new Set(["units_per_case"]);

export function isEditable(row: ProposalRow): boolean {
  return row.status === "PENDING" && EDITABLE_FIELDS.has(row.field);
}

/** A whole number in 1–9999 (the backend's bounds for units per case), or null. */
export function parseUnits(text: string): number | null {
  const n = Number(text.trim());
  return Number.isInteger(n) && n >= 1 && n <= 9999 ? n : null;
}

/** What the backend says about each id that stopped a bulk decision. */
export function batchFailures(error: unknown): Record<string, string> {
  if (error instanceof ApiError && error.detail && typeof error.detail === "object") {
    const failures = (error.detail as { failures?: unknown }).failures;
    if (failures && typeof failures === "object") return failures as Record<string, string>;
  }
  return {};
}
