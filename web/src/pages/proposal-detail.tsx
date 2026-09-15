import { ArrowLeft, ArrowRight, Database, History, Lock } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import type { ProposalDetail } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { DecisionPanel } from "@/components/review/decision-panel";
import { EvidencePanel } from "@/components/review/evidence-panel";
import { ProposalSourceBadge, ProposalStatusBadge } from "@/components/review/proposal-badges";
import { ProposalTimeline } from "@/components/review/proposal-timeline";
import { StoreChip } from "@/components/shared/store-chip";
import { ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProductHistory, useProposal } from "@/hooks/use-api";
import { formatDateTime } from "@/lib/format";
import { PROPOSAL_SOURCE_META, PROPOSAL_STATUS_META } from "@/lib/status";
import { cn } from "@/lib/utils";

function Value({ value, muted }: { value: unknown; muted?: boolean }) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground">none</span>;
  }
  return <span className={cn("font-semibold tabular-nums", muted && "font-normal")}>{String(value)}</span>;
}

/**
 * The three values a reviewer must not confuse, side by side:
 *   current master  — what the EDI uses today (may be nothing);
 *   proposed        — what this proposal wants it to be;
 *   outcome         — what the decision did (only after review).
 */
function ValueComparison({ proposal: p }: { proposal: ProposalDetail }) {
  const master = p.current_master_value;
  const wroteIt = p.resulting_mapping !== null;
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <div className="rounded-md border px-3 py-2.5">
        <div className="mb-1 flex items-center gap-1.5 text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">
          <Database className="size-3.5" /> Master data now
        </div>
        <div className="text-lg">
          <Value value={master} />
          {master !== null && master !== undefined ? (
            <span className="ml-1 text-[0.75rem] text-muted-foreground">{p.field.replace(/_/g, " ")}</span>
          ) : null}
        </div>
        <p className="mt-1 text-[0.7rem] text-muted-foreground">
          {master === null || master === undefined
            ? "Unmapped — no EDI can use this product yet."
            : wroteIt
              ? "Written by this proposal's approval."
              : "The value the EDI uses today."}
        </p>
      </div>
      <div className={cn("rounded-md border px-3 py-2.5", p.status === "PENDING" && "border-warning/50 bg-warning-soft/40")}>
        <div className="mb-1 flex items-center gap-1.5 text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">
          <ArrowRight className="size-3.5" /> Proposed
        </div>
        <div className="text-lg">
          <Value value={p.proposed_value} />
          <span className="ml-1 text-[0.75rem] text-muted-foreground">{p.field.replace(/_/g, " ")}</span>
        </div>
        <p className="mt-1 text-[0.7rem] text-muted-foreground">
          {p.current_value !== null && p.current_value !== undefined
            ? `Was ${String(p.current_value)} when proposed.`
            : "Nothing was mapped when proposed."}
        </p>
      </div>
      <div className="rounded-md border px-3 py-2.5">
        <div className="mb-1 flex items-center gap-1.5 text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">
          <Lock className="size-3.5" /> Outcome
        </div>
        <div className="text-[0.85rem]">
          <ProposalStatusBadge status={p.status} />
        </div>
        <p className="mt-1 text-[0.7rem] text-muted-foreground">
          {p.status === "PENDING"
            ? PROPOSAL_STATUS_META.PENDING.meaning
            : p.status === "APPROVED"
              ? wroteIt
                ? `Wrote the mapping. Reviewed ${formatDateTime(p.reviewed_at)} by ${p.reviewed_by}.`
                : `Approved ${formatDateTime(p.reviewed_at)} by ${p.reviewed_by}; a later proposal has since replaced its value.`
              : `Rejected ${formatDateTime(p.reviewed_at)} by ${p.reviewed_by}. Master data untouched.`}
        </p>
      </div>
    </div>
  );
}

export function ProposalDetailPage() {
  const { proposalId } = useParams<{ proposalId: string }>();
  const { data, isPending, isError, error, refetch } = useProposal(proposalId);
  const history = useProductHistory(data?.store.id, data?.entity_key);

  return (
    <>
      <div className="mb-1">
        <Button asChild variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
          <Link to="/data-review">
            <ArrowLeft className="size-3.5" /> Data Review
          </Link>
        </Button>
      </div>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} title="Proposal not found" />
      ) : isPending || !data ? (
        <div className="space-y-4">
          <Skeleton className="h-14 w-2/3" />
          <Skeleton className="h-28" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <>
          <PageHeader
            title={
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{data.entity_key}</span>
                <StoreChip store={data.store} withAddress />
                <ProposalStatusBadge status={data.status} />
                <ProposalSourceBadge source={data.source} />
              </span>
            }
            description={`${data.entity_type.replace(/_/g, " ")} · ${data.field.replace(/_/g, " ")} · proposed ${formatDateTime(data.created_at)} by ${data.proposed_by}`}
            actions={
              <Button asChild variant="outline" size="sm">
                <Link to={`/data-review/products/${data.entity_key}?store=${data.store.id}`}>
                  <History className="size-3.5" /> Product history
                </Link>
              </Button>
            }
          />

          <div className="space-y-4">
            <ValueComparison proposal={data} />

            {data.status === "PENDING" ? (
              <Card className="gap-0 border-warning/50 p-0">
                <CardHeader className="px-5 py-3">
                  <CardTitle className="text-[0.9rem]">Decision</CardTitle>
                  <p className="text-[0.75rem] text-muted-foreground">
                    Approving writes master data for every invoice carrying this UPC. Rejecting freezes the
                    proposal and changes nothing. Either way the record is permanent.
                  </p>
                </CardHeader>
                <CardContent className="px-5 pb-4">
                  <DecisionPanel proposal={data} />
                </CardContent>
              </Card>
            ) : (
              <Card className="gap-0 p-0">
                <CardContent className="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3 text-[0.8rem]">
                  <Lock className="size-4 text-muted-foreground" />
                  <span>
                    Reviewed {formatDateTime(data.reviewed_at)} by{" "}
                    <span className="font-mono font-medium">{data.reviewed_by}</span>
                  </span>
                  {data.review_note ? <span className="text-muted-foreground">“{data.review_note}”</span> : null}
                  {data.resulting_mapping ? (
                    <span className="text-muted-foreground">
                      → <span className="font-mono">product_case_mappings[{data.resulting_mapping.store.label}].{data.resulting_mapping.item_code}</span>{" "}
                      = {data.resulting_mapping.units_per_case} (source {data.resulting_mapping.source})
                    </span>
                  ) : null}
                  <span className="ml-auto text-[0.72rem] text-muted-foreground">
                    Immutable. A different value is a new proposal.
                  </span>
                </CardContent>
              </Card>
            )}

            <div className="grid gap-4 lg:grid-cols-2">
              <Card className="gap-0 p-0">
                <CardHeader className="px-5 py-3">
                  <CardTitle className="text-[0.9rem]">Evidence</CardTitle>
                  <p className="text-[0.75rem] text-muted-foreground">
                    {PROPOSAL_SOURCE_META[data.source]?.blurb ?? "How this value was arrived at."}
                  </p>
                </CardHeader>
                <CardContent className="px-5 pb-4">
                  <EvidencePanel proposal={data} />
                </CardContent>
              </Card>

              <Card className="gap-0 p-0">
                <CardHeader className="px-5 py-3">
                  <CardTitle className="text-[0.9rem]">This product's history in {data.store.label}</CardTitle>
                  <p className="text-[0.75rem] text-muted-foreground">
                    Every proposal ever made for UPC {data.entity_key} in this store, oldest first.
                  </p>
                </CardHeader>
                <CardContent className="px-5 pb-4">
                  {history.isError ? (
                    <ErrorState error={history.error} onRetry={() => void history.refetch()} />
                  ) : history.isPending || !history.data ? (
                    <Skeleton className="h-32" />
                  ) : (
                    <ProposalTimeline history={history.data} highlightId={data.id} />
                  )}
                </CardContent>
              </Card>
            </div>

            {data.invoice_id ? (
              <p className="text-[0.75rem] text-muted-foreground">
                Raised on{" "}
                <Link to={`/invoices/${data.invoice_id}`} className="text-primary hover:underline">
                  invoice {data.invoice_id}
                </Link>
                . Line-item corrections on that invoice are separate from this master-data decision.
              </p>
            ) : null}
          </div>
        </>
      )}
    </>
  );
}
