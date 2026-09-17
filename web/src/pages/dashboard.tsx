import {
  AlertTriangle,
  ArrowRight,
  BadgeCheck,
  ClipboardCheck,
  FileStack,
  Gauge,
  MapPinOff,
  Timer,
  UserSearch,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import type { HistoryRow } from "@/api/types";
import { PageHeader, SectionHeader } from "@/components/layout/page-header";
import { ConfidenceChart } from "@/components/dashboard/confidence-chart";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { StatusChart } from "@/components/dashboard/status-chart";
import { MetricCard, MetricCardSkeleton } from "@/components/shared/metric-card";
import { PipelineStrip } from "@/components/shared/pipeline-strip";
import { EmptyState, ErrorState, PanelSkeleton, TableSkeleton } from "@/components/shared/states";
import { StatusPill } from "@/components/shared/status-badge";
import { StoreChip } from "@/components/shared/store-chip";
import { Button } from "@/components/ui/button";
import { useApiHealth, useDashboard, usePendingProposalCount, useStores } from "@/hooks/use-api";
import { formatDateTime, formatDuration, formatPercent } from "@/lib/format";
import { reviewHeadline } from "@/lib/review";
import { rowDestination } from "@/lib/routes";

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** Rows that need a person now: paused for a store, failed, or flagged for review. */
function needsAttention(row: HistoryRow): { reason: string; tone: "warning" | "danger" } | null {
  if (row.status === "FAILED") return { reason: "Processing failed", tone: "danger" };
  if (row.status === "STORE_CONFIRMATION_REQUIRED") return { reason: "Waiting for a store", tone: "warning" };
  if (row.status === "REVIEW_REQUIRED") return { reason: "Validation needs review", tone: "warning" };
  if (row.review?.status === "PENDING") return { reason: reviewHeadline(row.review), tone: "warning" };
  return null;
}

export function DashboardPage() {
  const navigate = useNavigate();
  const { data, isPending, isError, error, refetch } = useDashboard();
  const pending = usePendingProposalCount();
  const stores = useStores();
  const health = useApiHealth();

  const unresolvedStores = stores.data?.filter((s) => s.identity_status !== "confirmed") ?? [];
  const attention = (data?.recent ?? []).map((row) => ({ row, why: needsAttention(row) })).filter((x) => x.why !== null).slice(0, 6);
  const extracted = data ? data.completed + data.review_required : 0;

  return (
    <>
      <PageHeader
        eyebrow={new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}
        title={`${greeting()} — invoice operations`}
        description="What the pipeline has done, what it is waiting on, and what needs a person."
        actions={
          <Button asChild>
            <Link to="/process">Process an invoice</Link>
          </Button>
        }
      />

      {/* System health — only what is actually observed. */}
      <div className="mb-5 flex flex-wrap items-center gap-2" data-testid="system-health">
        <span className="t-eyebrow mr-1">System</span>
        <StatusPill tone={health.isSuccess ? "success" : health.isPending ? "neutral" : "danger"}
                    label={health.isSuccess ? "API operational" : health.isPending ? "Checking API" : "API unreachable"}
                    meaning="Backend health endpoint, checked every 30 seconds" active={health.isPending} size="xs" />
        <StatusPill tone={data && data.in_progress > 0 ? "info" : "neutral"}
                    label={data && data.in_progress > 0 ? `${data.in_progress} processing` : "Pipeline idle"}
                    meaning="Documents currently in OCR, extraction or validation" active={Boolean(data && data.in_progress > 0)} size="xs" />
        <StatusPill tone={pending.data ? "warning" : "success"}
                    label={pending.data ? `${pending.data} awaiting master-data review` : "Master data up to date"}
                    meaning="Case-mapping proposals waiting for a reviewer" size="xs" />
        <StatusPill tone={unresolvedStores.length ? "warning" : "success"}
                    label={unresolvedStores.length ? `${unresolvedStores.length} store${unresolvedStores.length === 1 ? "" : "s"} need identity confirmation` : "All stores confirmed"}
                    meaning="Stores known only by a source code or by document evidence" size="xs" />
      </div>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div className="space-y-5">
          {/* Operational metrics */}
          <div className="grid grid-cols-6 gap-3 max-xl:grid-cols-3 max-md:grid-cols-2">
            {isPending ? (
              Array.from({ length: 6 }).map((_, index) => <MetricCardSkeleton key={index} />)
            ) : (
              <>
                <MetricCard index={0} label="Invoices processed" value={data.total_documents} icon={FileStack}
                            hint={data.in_progress > 0 ? `${data.in_progress} in progress now` : "all runs settled"}
                            onClick={() => navigate("/invoices")} />
                <MetricCard index={1} label="Needs review" value={data.review_required} icon={UserSearch}
                            tone={data.review_required > 0 ? "warning" : "neutral"}
                            hint="flagged for a person"
                            onClick={() => navigate("/invoices?status=REVIEW_REQUIRED")} />
                <MetricCard index={2} label="Validated" value={data.completed} icon={BadgeCheck} tone="success"
                            hint={data.success_rate !== null ? `${formatPercent(data.success_rate, 0)} of processed` : "every check passed"}
                            onClick={() => navigate("/invoices?status=COMPLETED")} />
                <MetricCard index={3} label="Master-data decisions" value={pending.isSuccess ? pending.data : "—"} icon={ClipboardCheck}
                            tone={pending.data ? "warning" : "neutral"} hint="mappings pending approval"
                            onClick={() => navigate("/data-review")} />
                <MetricCard index={4} label="Stores unconfirmed" value={stores.isSuccess ? unresolvedStores.length : "—"} icon={MapPinOff}
                            tone={unresolvedStores.length ? "warning" : "neutral"} hint="identity to confirm"
                            onClick={() => navigate("/stores")} />
                <MetricCard index={5} label="Avg confidence" value={data.average_confidence !== null ? formatPercent(data.average_confidence) : "—"} icon={Gauge}
                            hint={<span className="inline-flex items-center gap-1"><Timer className="size-3" /> {formatDuration(data.average_processing_ms)} per invoice</span>} />
              </>
            )}
          </div>

          {/* Pipeline */}
          {isPending ? (
            <PanelSkeleton className="h-24" />
          ) : (
            <PipelineStrip
              stages={[
                { key: "upload", label: "Upload", value: data.total_documents, hint: "documents received", tone: "neutral", state: "done" },
                { key: "ocr", label: "OCR", value: data.total_documents - data.failed, hint: data.failed ? `${data.failed} failed` : "text extracted", tone: data.failed ? "danger" : "success", state: data.in_progress ? "active" : "done" },
                { key: "extract", label: "Extraction", value: extracted, hint: "structured invoices", tone: "success", state: "done" },
                { key: "validate", label: "Validation", value: data.completed, hint: data.review_required ? `${data.review_required} need review` : "all passed", tone: data.review_required ? "warning" : "success", state: "done" },
                { key: "master", label: "Master data", value: pending.isSuccess ? pending.data : null, hint: pending.data ? "awaiting review" : "nothing pending", tone: pending.data ? "warning" : "success", state: pending.data ? "blocked" : "done" },
                { key: "edi", label: "EDI", value: null, hint: "decided per invoice", tone: "neutral", state: "idle" },
              ]}
            />
          )}

          {isPending ? (
            <div className="grid grid-cols-3 gap-4 max-lg:grid-cols-1">
              <PanelSkeleton className="h-64" />
              <PanelSkeleton className="h-64" />
              <PanelSkeleton className="h-64" />
            </div>
          ) : data.total_documents === 0 ? (
            <EmptyState
              title="No invoices yet"
              description="Process the first invoice to see pipeline metrics, confidence and review activity here."
              action={<Button asChild><Link to="/process">Process an invoice</Link></Button>}
            />
          ) : (
            <>
              <div className="grid grid-cols-3 gap-4 max-lg:grid-cols-1">
                {/* Attention */}
                <div className="surface flex flex-col overflow-hidden">
                  <SectionHeader title="Needs attention" count={attention.length || undefined}
                                 description="From the most recent runs" />
                  {attention.length === 0 ? (
                    <div className="flex flex-1 items-center justify-center px-5 pb-6 text-center text-[0.78rem] text-muted-foreground">
                      Nothing is waiting on a person.
                    </div>
                  ) : (
                    <ul className="divide-y border-t">
                      {attention.map(({ row, why }) => (
                        <li key={row.document_id}>
                          <button type="button" onClick={() => navigate(rowDestination(row))}
                                  className="row-hover flex w-full items-center gap-3 px-5 py-2.5 text-left">
                            <AlertTriangle className={why!.tone === "danger" ? "size-3.5 shrink-0 text-danger" : "size-3.5 shrink-0 text-warning"} aria-hidden />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[0.8rem] font-medium">
                                {row.invoice_number ? `#${row.invoice_number}` : row.filename}
                                {row.vendor_name ? <span className="font-normal text-muted-foreground"> · {row.vendor_name}</span> : null}
                              </span>
                              <span className="t-meta block truncate">{why!.reason} · {formatDateTime(row.created_at)}</span>
                            </span>
                            <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <StatusChart data={data} />
                <ConfidenceChart data={data} />
              </div>

              {unresolvedStores.length ? (
                <div className="surface overflow-hidden">
                  <SectionHeader title="Store identity" description="Stores known only by a source code or by what documents say — a person confirms each one."
                                 actions={<Button asChild variant="ghost" size="sm"><Link to="/stores">Store Directory <ArrowRight className="size-3.5" /></Link></Button>} />
                  <div className="flex flex-wrap gap-2 border-t px-5 py-3">
                    {unresolvedStores.map((s) => <StoreChip key={s.id} store={s} />)}
                  </div>
                </div>
              ) : null}

              {isPending ? <TableSkeleton /> : <RecentActivity rows={data.recent} />}
            </>
          )}
        </div>
      )}
    </>
  );
}
