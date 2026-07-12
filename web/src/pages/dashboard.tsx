import {
  BadgeCheck,
  CircleDollarSign,
  FileStack,
  Gauge,
  Timer,
  UserSearch,
} from "lucide-react";
import { Link } from "react-router-dom";

import { PageHeader } from "@/components/layout/page-header";
import { ConfidenceChart } from "@/components/dashboard/confidence-chart";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { StatusChart } from "@/components/dashboard/status-chart";
import { KpiCard, KpiCardSkeleton } from "@/components/shared/kpi-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard } from "@/hooks/use-api";
import { formatDuration, formatPercent } from "@/lib/format";

export function DashboardPage() {
  const { data, isPending, isError, error, refetch } = useDashboard();

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Processing overview across the invoice intelligence pipeline."
      />

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div className="space-y-5">
          {/* KPI row */}
          <div className="grid grid-cols-6 gap-3 max-xl:grid-cols-3 max-md:grid-cols-2">
            {isPending ? (
              Array.from({ length: 6 }).map((_, index) => <KpiCardSkeleton key={index} />)
            ) : (
              <>
                <KpiCard
                  index={0}
                  label="Processed"
                  value={String(data.total_documents)}
                  hint={
                    data.in_progress > 0 ? `${data.in_progress} in progress` : "all settled"
                  }
                  icon={FileStack}
                />
                <KpiCard
                  index={1}
                  label="Success rate"
                  value={data.success_rate !== null ? formatPercent(data.success_rate, 0) : "—"}
                  hint={`${data.completed} completed`}
                  icon={BadgeCheck}
                />
                <KpiCard
                  index={2}
                  label="Review queue"
                  value={String(data.review_required)}
                  hint="awaiting manual review"
                  icon={UserSearch}
                  valueClassName={data.review_required > 0 ? "text-warning" : undefined}
                />
                <KpiCard
                  index={3}
                  label="Avg confidence"
                  value={
                    data.average_confidence !== null
                      ? formatPercent(data.average_confidence)
                      : "—"
                  }
                  hint="composite score"
                  icon={Gauge}
                />
                <KpiCard
                  index={4}
                  label="Avg processing"
                  value={formatDuration(data.average_processing_ms)}
                  hint="upload → persisted"
                  icon={Timer}
                />
                <KpiCard
                  index={5}
                  label="Est. AI cost"
                  value={`$${data.total_estimated_cost_usd.toFixed(4)}`}
                  hint={`${data.total_tokens.toLocaleString()} tokens`}
                  icon={CircleDollarSign}
                />
              </>
            )}
          </div>

          {/* Charts */}
          {isPending ? (
            <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
              <Skeleton className="h-56" />
              <Skeleton className="h-56" />
            </div>
          ) : data.total_documents === 0 ? (
            <EmptyState
              title="No invoices processed yet"
              description="Upload your first invoice to see live pipeline metrics, confidence scores, and processing history here."
              action={
                <Button asChild>
                  <Link to="/process">Process an invoice</Link>
                </Button>
              }
            />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4 max-md:grid-cols-1">
                <StatusChart data={data} />
                <ConfidenceChart data={data} />
              </div>
              {isPending ? <TableSkeleton /> : <RecentActivity rows={data.recent} />}
            </>
          )}
        </div>
      )}
    </>
  );
}
