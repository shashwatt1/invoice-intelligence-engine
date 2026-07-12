import { ArrowLeft, ArrowRight } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { PageHeader } from "@/components/layout/page-header";
import { ProcessingTimeline } from "@/components/processing/processing-timeline";
import { StatusBadge } from "@/components/shared/status-badge";
import { ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDocumentStatus } from "@/hooks/use-api";
import { formatDateTime } from "@/lib/format";

/**
 * Status view for documents without an invoice — failed runs and
 * documents still mid-pipeline. Polls live until terminal.
 */
export function DocumentStatusPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const { data, isPending, isError, error, refetch } = useDocumentStatus(documentId);

  return (
    <>
      <div className="mb-1">
        <Button asChild variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
          <Link to="/invoices">
            <ArrowLeft className="size-3.5" /> Invoice history
          </Link>
        </Button>
      </div>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} title="Document not found" />
      ) : isPending || !data ? (
        <div className="space-y-4">
          <Skeleton className="h-14 w-2/3" />
          <Skeleton className="h-72" />
        </div>
      ) : (
        <>
          <PageHeader
            title={data.filename}
            description={`Document ${data.document_id.slice(0, 8)} · created ${formatDateTime(data.created_at)}`}
            actions={<StatusBadge status={data.status} />}
          />

          {data.status === "FAILED" && data.error ? (
            <Card className="border-danger/30 bg-danger-soft/40 mb-4">
              <CardContent className="text-[0.85rem]">
                <div className="text-danger font-semibold">
                  Failed during {data.error.stage?.replaceAll("_", " ").toLowerCase() ?? "processing"}
                </div>
                <p className="mt-1 text-muted-foreground">
                  {data.error.message ?? "No detail recorded."}
                </p>
                {data.error.error_code && (
                  <code className="mt-2 inline-block rounded bg-card px-1.5 py-0.5 text-[0.72rem] text-muted-foreground">
                    {data.error.error_code}
                  </code>
                )}
              </CardContent>
            </Card>
          ) : null}

          <div className="grid grid-cols-2 gap-4 max-lg:grid-cols-1">
            <Card>
              <CardHeader>
                <CardTitle className="text-[0.95rem]">Processing timeline</CardTitle>
              </CardHeader>
              <CardContent>
                <ProcessingTimeline status={data} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-[0.95rem]">Stage log</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2.5">
                {data.stages.map((stage, index) => (
                  <div key={`${stage.stage}-${index}`} className="flex items-start gap-3">
                    <span
                      className={
                        stage.status === "SUCCESS"
                          ? "bg-success mt-1.5 size-1.5 shrink-0 rounded-full"
                          : "bg-danger mt-1.5 size-1.5 shrink-0 rounded-full"
                      }
                    />
                    <div className="min-w-0 text-[0.8rem]">
                      <span className="font-semibold">
                        {stage.stage.replaceAll("_", " ").toLowerCase()}
                      </span>
                      <span className="text-muted-foreground">
                        {" — "}
                        {stage.message ?? stage.status.toLowerCase()}
                      </span>
                      <div className="text-[0.7rem] text-muted-foreground tabular-nums">
                        {formatDateTime(stage.created_at)}
                        {stage.duration_ms !== null ? ` · ${stage.duration_ms} ms` : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          {data.invoice_id && (
            <div className="mt-4">
              <Button asChild>
                <Link to={`/invoices/${data.invoice_id}`}>
                  View extracted invoice <ArrowRight className="size-4" />
                </Link>
              </Button>
            </div>
          )}
        </>
      )}
    </>
  );
}
