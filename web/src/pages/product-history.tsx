import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { PageHeader } from "@/components/layout/page-header";
import { ProposalTimeline } from "@/components/review/proposal-timeline";
import { ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProductHistory } from "@/hooks/use-api";

/** The audit view for one UPC: what master data says today and how it got there. */
export function ProductHistoryPage() {
  const { itemCode } = useParams<{ itemCode: string }>();
  const { data, isPending, isError, error, refetch } = useProductHistory(itemCode);

  return (
    <>
      <div className="mb-1">
        <Button asChild variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
          <Link to="/data-review">
            <ArrowLeft className="size-3.5" /> Data Review
          </Link>
        </Button>
      </div>
      <PageHeader
        title={`Product ${data?.item_code ?? itemCode ?? ""}`}
        description="What happened to this product's reusable data — every proposal, every decision, and the one authoritative value in force today."
        actions={
          <Button asChild variant="outline" size="sm">
            <Link to={`/data-review?status=ALL&upc=${encodeURIComponent(data?.item_code ?? itemCode ?? "")}`}>
              Open in queue
            </Link>
          </Button>
        }
      />
      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isPending || !data ? (
        <Skeleton className="h-48" />
      ) : (
        <Card className="gap-0 p-0">
          <CardContent className="px-5 py-4">
            <ProposalTimeline history={data} />
          </CardContent>
        </Card>
      )}
    </>
  );
}
