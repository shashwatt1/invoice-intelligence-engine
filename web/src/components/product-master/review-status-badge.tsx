import { StatusPill } from "@/components/shared/status-badge";
import type { Tone } from "@/lib/status";

/**
 * A commercial candidate's review state, as words — never colour alone.
 *
 * READY FOR REVIEW, CONFLICT and NO MULTIPLIER are all still REVIEW_REQUIRED
 * (undecided); the second line says so, and says when a person must decide
 * the row on its own rather than in a multi-select approval.
 */
const META: Record<string, { label: string; tone: Tone; detail: string }> = {
  READY_FOR_REVIEW: { label: "Ready for review", tone: "info", detail: "Review required" },
  PENDING: { label: "Proposal pending", tone: "warning", detail: "Awaiting a manager decision" },
  CONFLICT: { label: "Conflict", tone: "danger", detail: "Individual review required" },
  NO_MULTIPLIER: { label: "No multiplier", tone: "warning", detail: "Individual review required" },
  APPROVED: { label: "Approved", tone: "success", detail: "Decided — authoritative" },
  REJECTED: { label: "Rejected", tone: "neutral", detail: "Decided — kept for audit" },
};

export function ReviewStatusBadge({ status, lastDecision }: { status: string; lastDecision?: string | null }) {
  const meta = META[status] ?? { label: status.replaceAll("_", " ").toLowerCase(), tone: "neutral" as Tone, detail: "" };
  const reopened = lastDecision === "REOPEN" && status !== "APPROVED" && status !== "REJECTED";
  const detail = reopened ? "Reopened — individual review required" : meta.detail;
  return (
    <span className="inline-flex flex-col items-start gap-0.5" data-testid="review-status">
      <StatusPill size="xs" tone={meta.tone} label={meta.label} meaning={detail} />
      {detail ? <span className="text-[0.68rem] text-muted-foreground">{detail}</span> : null}
    </span>
  );
}
