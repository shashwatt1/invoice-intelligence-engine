import { FileText, Keyboard, Sheet, Sparkles, History } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { ProposalSource, ProposalStatus } from "@/api/types";
import { StatusPill } from "@/components/shared/status-badge";
import { PROPOSAL_SOURCE_META, PROPOSAL_STATUS_META } from "@/lib/status";
import { titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Status pill. Reads its label and meaning from PROPOSAL_STATUS_META so a
 * PENDING proposal is never styled like approved master data.
 */
export function ProposalStatusBadge({ status, className }: { status: ProposalStatus | string; className?: string }) {
  const meta = PROPOSAL_STATUS_META[status as ProposalStatus] ?? { label: titleCase(status), tone: "neutral" as const, meaning: "" };
  return <StatusPill tone={meta.tone} label={meta.label} meaning={meta.meaning || undefined} size="xs" className={className} />;
}

const SOURCE_ICON: Record<string, LucideIcon> = {
  beer_inventory_explicit: Sheet,
  beer_inventory_package: Sheet,
  reference_derived: Sparkles,
  document_derived: FileText,
  document_ambiguous: FileText,
  operator_entered: Keyboard,
  legacy_migrated: History,
};

/** Evidence-type pill: where the proposed value came from. */
export function ProposalSourceBadge({ source, className }: { source: ProposalSource | string; className?: string }) {
  const meta = PROPOSAL_SOURCE_META[source as ProposalSource];
  const Icon = SOURCE_ICON[source] ?? FileText;
  return (
    <span title={meta?.blurb}
          className={cn("inline-flex h-5 items-center gap-1 rounded-md border bg-card px-1.5 text-[0.68rem] font-medium whitespace-nowrap text-foreground", className)}>
      <Icon className="size-3 text-muted-foreground" aria-hidden />
      {meta?.label ?? titleCase(source)}
    </span>
  );
}
