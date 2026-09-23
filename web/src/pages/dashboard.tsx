import { AlertTriangle, ArrowRight, ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { DashboardData, HistoryRow } from "@/api/types";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { StatusChart } from "@/components/dashboard/status-chart";
import { PipelineStrip } from "@/components/shared/pipeline-strip";
import { EmptyState, ErrorState, PanelSkeleton, TableSkeleton } from "@/components/shared/states";
import { StoreChip } from "@/components/shared/store-chip";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiHealth, useDashboard, usePendingProposalCount, useStores } from "@/hooks/use-api";
import { useClock } from "@/hooks/use-clock";
import { formatDateTime, formatPercent } from "@/lib/format";
import { reviewHeadline } from "@/lib/review";
import { rowDestination } from "@/lib/routes";
import { cn } from "@/lib/utils";

function greeting(now: Date): string {
  const hour = now.getHours();
  if (hour < 12) return "Good Morning";
  if (hour < 18) return "Good Afternoon";
  return "Good Evening";
}

function clock(now: Date): string {
  return now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * One sentence, built only from numbers the API reports: what is waiting
 * on a person right now. Reads "Nothing is waiting on you" when the
 * queues are empty — that is a real state, not a placeholder.
 */
function operationalStatement(data: DashboardData, pendingProposals: number | null, unresolvedStores: number | null): string {
  const storePending = data.status_breakdown.STORE_CONFIRMATION_REQUIRED ?? 0;
  const parts: string[] = [];
  if (data.review_required) parts.push(`${plural(data.review_required, "invoice")} need${data.review_required === 1 ? "s" : ""} review`);
  if (storePending) parts.push(`${storePending} waiting on a store`);
  if (pendingProposals) parts.push(`${plural(pendingProposals, "master-data decision")} pending`);
  if (unresolvedStores) parts.push(`${unresolvedStores} store ${unresolvedStores === 1 ? "identity" : "identities"} to confirm`);
  if (data.failed) parts.push(`${plural(data.failed, "run")} failed`);
  if (parts.length === 0) {
    return data.total_documents === 0
      ? "No invoices have been processed yet."
      : `Nothing is waiting on you — ${plural(data.completed, "invoice")} validated, ${data.in_progress ? `${data.in_progress} processing now.` : "the pipeline is idle."}`;
  }
  return parts.join(" · ") + ".";
}

type SignalTone = "ok" | "attention" | "down" | "checking";

/** A single observed fact about the system, on the command surface. */
function Signal({ tone, label, meaning }: { tone: SignalTone; label: string; meaning: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[0.74rem] text-brand-cream/70" title={meaning}>
      <span
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          tone === "ok" && "bg-brand-cream/80",
          tone === "attention" && "bg-amber-300",
          tone === "down" && "bg-red-400 animate-pulse",
          tone === "checking" && "bg-brand-cream/40 animate-pulse",
        )}
        aria-hidden
      />
      {label}
    </span>
  );
}

/** One line of the queue: the figure, what it is, and where it opens. */
function QueueRow({ value, label, hint, to, attention }: { value: number | string; label: string; hint: string; to: string; attention: boolean }) {
  return (
    <li>
      <Link to={to} className="group -mx-3 flex items-center gap-4 rounded-md px-3 py-3 transition-colors hover:bg-white/6 focus-visible:bg-white/6 focus-visible:outline-none" data-testid="queue-row">
        <span className={cn("t-figure w-12 shrink-0 text-[2rem]", attention ? "text-white" : "text-brand-cream/40")}>{value}</span>
        <span className="min-w-0 flex-1">
          <span className={cn("block text-[0.88rem] font-medium transition-colors group-hover:text-white", attention ? "text-brand-cream" : "text-brand-cream/70")}>{label}</span>
          <span className="block truncate text-[0.72rem] text-brand-cream/50">{hint}</span>
        </span>
        <ArrowUpRight className={cn("size-4 shrink-0 transition-all group-hover:translate-x-0.5 group-hover:text-white", attention ? "text-brand-cream/70" : "text-brand-cream/30")} aria-hidden />
      </Link>
    </li>
  );
}

/** Rows that need a person now: paused for a store, failed, or flagged for review. */
function needsAttention(row: HistoryRow): { reason: string; tone: "warning" | "danger" } | null {
  if (row.status === "FAILED") return { reason: "Processing failed", tone: "danger" };
  if (row.status === "STORE_CONFIRMATION_REQUIRED") return { reason: "Waiting for a store", tone: "warning" };
  if (row.status === "REVIEW_REQUIRED") return { reason: "Validation needs review", tone: "warning" };
  if (row.review?.status === "PENDING") return { reason: reviewHeadline(row.review), tone: "warning" };
  return null;
}

function BandHeader({ title, count, description, action }: { title: string; count?: number; description?: string; action?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h2 className="t-section flex items-baseline gap-2">
          {title}
          {count !== undefined ? <span className="t-meta font-normal tabular-nums">{count}</span> : null}
        </h2>
        {description ? <p className="t-meta mt-0.5">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function DashboardPage() {
  const navigate = useNavigate();
  const { data, isPending, isError, error, refetch } = useDashboard();
  const pending = usePendingProposalCount();
  const stores = useStores();
  const health = useApiHealth();
  const now = useClock();

  const unresolvedStores = stores.data?.filter((s) => s.identity_status !== "confirmed") ?? [];
  const attention = (data?.recent ?? []).map((row) => ({ row, why: needsAttention(row) })).filter((x) => x.why !== null).slice(0, 6);
  const extracted = data ? data.completed + data.review_required : 0;
  const storePending = data?.status_breakdown.STORE_CONFIRMATION_REQUIRED ?? 0;

  return (
    <>
      {/* Command surface: where the operation stands, and the queue that says what to do about it. */}
      <section className="dark-surface mb-8 grid grid-cols-[minmax(0,1fr)_minmax(300px,380px)] max-xl:grid-cols-1" data-testid="dashboard-hero">
        <div className="flex flex-col px-8 pt-7 pb-6 max-md:px-5">
          <div className="flex flex-wrap items-baseline gap-x-3 text-[0.78rem] font-semibold text-brand-cream/70 tabular-nums">
            <span>{now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}</span>
            <time dateTime={now.toISOString()} data-testid="clock" aria-label="Current time">{clock(now)}</time>
          </div>
          <h1 className="t-display mt-2 text-white">{greeting(now)}</h1>
          <p className="mt-3 max-w-xl text-[1.02rem] leading-relaxed text-brand-cream/85" data-testid="operational-statement">
            {isPending ? "Reading the pipeline…" : isError ? "The dashboard could not be loaded." : operationalStatement(data, pending.data ?? null, stores.isSuccess ? unresolvedStores.length : null)}
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2">
            <Button asChild className="bg-brand-cream text-brand-deep hover:bg-white">
              <Link to="/process">Process an invoice</Link>
            </Button>
            {data ? (
              <span className="text-[0.8rem] text-brand-cream/70 tabular-nums" data-testid="throughput">
                <Link to="/invoices" className="text-brand-cream hover:text-white">{plural(data.total_documents, "invoice")} processed</Link>
                {" · "}
                <Link to="/invoices?status=COMPLETED" className="hover:text-white">{data.completed} validated{data.success_rate !== null ? ` (${formatPercent(data.success_rate, 0)})` : ""}</Link>
              </span>
            ) : null}
          </div>
          {/* The pipeline as one line across the command surface. */}
          <div className="mt-9 border-t border-white/10 pt-5" data-testid="pipeline">
            {isPending || isError ? (
              <Skeleton className="h-16 bg-white/10" />
            ) : (
              <PipelineStrip
                variant="dark"
                dense
                stages={[
                  { key: "upload", label: "Upload", value: data.total_documents, hint: "received", tone: "neutral", state: "done" },
                  { key: "ocr", label: "OCR", value: data.total_documents - data.failed, hint: data.failed ? `${data.failed} failed` : "text extracted", tone: data.failed ? "danger" : "neutral", state: data.in_progress ? "active" : "done" },
                  { key: "extract", label: "Extraction", value: extracted, hint: "structured", tone: "neutral", state: "done" },
                  { key: "validate", label: "Validation", value: data.completed, hint: data.review_required ? `${data.review_required} need review` : "all passed", tone: data.review_required ? "warning" : "neutral", state: "done" },
                  { key: "master", label: "Master data", value: pending.isSuccess ? pending.data : null, hint: pending.data ? "awaiting review" : "nothing pending", tone: pending.data ? "warning" : "neutral", state: pending.data ? "blocked" : "done" },
                  { key: "edi", label: "EDI", value: null, hint: "per invoice", tone: "neutral", state: "idle" },
                ]}
              />
            )}
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-1.5" data-testid="system-health">
            <Signal tone={health.isSuccess ? "ok" : health.isPending ? "checking" : "down"}
                    label={health.isSuccess ? "API operational" : health.isPending ? "Checking API" : "API unreachable"}
                    meaning="Backend health endpoint, checked every 30 seconds" />
            <Signal tone={data && data.in_progress > 0 ? "attention" : "ok"}
                    label={data && data.in_progress > 0 ? `${data.in_progress} processing` : "Pipeline idle"}
                    meaning="Documents currently in OCR, extraction or validation" />
            <Signal tone={unresolvedStores.length ? "attention" : "ok"}
                    label={unresolvedStores.length ? `${plural(unresolvedStores.length, "store")} unconfirmed` : "All stores confirmed"}
                    meaning="Stores known only by a source code or by document evidence" />
          </div>
        </div>

        {/* The queue: every figure is a list a person can open. */}
        <div className="border-l border-white/10 px-7 py-6 max-xl:border-t max-xl:border-l-0 max-md:px-5" data-testid="queue">
          <div className="flex items-baseline justify-between">
            <div className="text-[0.74rem] font-medium text-brand-cream/60">Your queue</div>
            <div className="text-[0.7rem] text-brand-cream/40">open a list</div>
          </div>
          {isPending || isError ? (
            <div className="mt-4 space-y-4">
              <Skeleton className="h-9 bg-white/10" /><Skeleton className="h-9 bg-white/10" /><Skeleton className="h-9 bg-white/10" /><Skeleton className="h-9 bg-white/10" />
            </div>
          ) : (
            <ul className="mt-2 divide-y divide-white/10">
              <QueueRow value={data.review_required} label="Need review" hint="validation flagged a person" to="/invoices?status=REVIEW_REQUIRED" attention={data.review_required > 0} />
              <QueueRow value={storePending} label="Waiting on a store" hint="identity to confirm before processing continues" to={storePending ? "/invoices?status=STORE_CONFIRMATION_REQUIRED" : "/stores"} attention={storePending > 0} />
              <QueueRow value={pending.isSuccess ? pending.data : "—"} label="Master-data decisions" hint="mappings proposed, awaiting approval" to="/data-review" attention={Boolean(pending.data)} />
              <QueueRow value={data.failed} label="Failed runs" hint="reprocess or delete" to="/invoices?status=FAILED" attention={data.failed > 0} />
            </ul>
          )}
        </div>
      </section>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div className="space-y-8">
          {isPending ? (
            <div className="grid grid-cols-[minmax(0,7fr)_minmax(0,5fr)] gap-10 max-lg:grid-cols-1">
              <PanelSkeleton className="h-64" />
              <PanelSkeleton className="h-64" />
            </div>
          ) : data.total_documents === 0 ? (
            <EmptyState
              title="No invoices yet"
              description="Process the first invoice to see the queue, pipeline counts and review activity here."
              action={<Button asChild><Link to="/process">Process an invoice</Link></Button>}
            />
          ) : (
            <>
              <div className="grid grid-cols-[minmax(0,7fr)_minmax(0,5fr)] gap-x-12 gap-y-8 max-lg:grid-cols-1">
                {/* Attention */}
                <section className="band">
                  <BandHeader title="Needs attention" count={attention.length || undefined} description="From the most recent runs" />
                  {attention.length === 0 ? (
                    <p className="panel px-4 py-6 text-center text-[0.8rem] text-muted-foreground">Nothing is waiting on a person — every recent run either validated or is still processing.</p>
                  ) : (
                    <ul className="surface divide-y divide-border overflow-hidden">
                      {attention.map(({ row, why }) => (
                        <li key={row.document_id}>
                          <button type="button" onClick={() => navigate(rowDestination(row))}
                                  className="group flex w-full items-center gap-4 px-5 py-3.5 text-left transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none">
                            <AlertTriangle className={why!.tone === "danger" ? "size-4 shrink-0 text-danger" : "size-4 shrink-0 text-warning"} aria-hidden />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[0.9rem] font-semibold tracking-[-0.01em]">
                                {row.invoice_number ? `#${row.invoice_number}` : row.filename}
                                {row.vendor_name ? <span className="font-normal text-muted-foreground"> · {row.vendor_name}</span> : null}
                              </span>
                              <span className="t-meta mt-0.5 block truncate">{why!.reason} · {formatDateTime(row.created_at)}</span>
                            </span>
                            <ArrowRight className="size-4 shrink-0 text-muted-foreground/50 transition-all group-hover:translate-x-0.5 group-hover:text-foreground" aria-hidden />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <div className="space-y-8">
                  <section className="band">
                    <BandHeader title="Where invoices stand" description="Every document by its current state" />
                    <StatusChart data={data} />
                  </section>

                  {unresolvedStores.length ? (
                    <section className="band">
                      <BandHeader title="Store identity" description="A person confirms each one."
                                  action={<Button asChild variant="ghost" size="sm" className="-mr-2"><Link to="/stores">Store Directory <ArrowRight className="size-3.5" /></Link></Button>} />
                      <div className="flex flex-wrap gap-2">
                        {unresolvedStores.map((s) => <StoreChip key={s.id} store={s} />)}
                      </div>
                    </section>
                  ) : null}
                </div>
              </div>

              {isPending ? <TableSkeleton /> : <RecentActivity rows={data.recent} />}
            </>
          )}
        </div>
      )}
    </>
  );
}
