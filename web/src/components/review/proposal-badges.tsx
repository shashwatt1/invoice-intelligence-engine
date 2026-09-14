import type { ProposalSource, ProposalStatus } from "@/api/types";
import { PROPOSAL_SOURCE_META, PROPOSAL_STATUS_META, TONE_CLASSES } from "@/lib/status";
import { titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Status pill. Reads its label and meaning from PROPOSAL_STATUS_META so a
 * PENDING proposal is never styled like approved master data.
 */
export function ProposalStatusBadge({
  status,
  className,
}: {
  status: ProposalStatus | string;
  className?: string;
}) {
  const meta = PROPOSAL_STATUS_META[status as ProposalStatus] ?? {
    label: titleCase(status),
    tone: "neutral" as const,
    meaning: "",
  };
  return (
    <span
      title={meta.meaning || undefined}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[0.72rem] font-semibold whitespace-nowrap",
        TONE_CLASSES[meta.tone],
        className,
      )}
    >
      <span className="size-1.5 rounded-full bg-current opacity-80" />
      {meta.label}
    </span>
  );
}

/** Evidence-type pill: where the proposed value came from. */
export function ProposalSourceBadge({
  source,
  className,
}: {
  source: ProposalSource | string;
  className?: string;
}) {
  const meta = PROPOSAL_SOURCE_META[source as ProposalSource];
  return (
    <span
      title={meta?.blurb}
      className={cn(
        "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[0.7rem] font-medium whitespace-nowrap text-foreground",
        className,
      )}
    >
      {meta?.label ?? titleCase(source)}
    </span>
  );
}
