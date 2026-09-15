import { ArrowLeft } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { PageHeader } from "@/components/layout/page-header";
import { ProposalTimeline } from "@/components/review/proposal-timeline";
import { StoreChip } from "@/components/shared/store-chip";
import { ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProductHistory } from "@/hooks/use-api";

/** The audit view for one UPC: what master data says today and how it got there. */
export function ProductHistoryPage() {
  const { itemCode } = useParams<{ itemCode: string }>();
  const [search] = useSearchParams();
  const store = search.get("store") ?? undefined;
  const { data, isPending, isError, error, refetch } = useProductHistory(store, itemCode);

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
        title={
          <span className="flex flex-wrap items-center gap-2">
            Product {data?.item_code ?? itemCode ?? ""}
            {data ? <StoreChip store={data.store} withAddress /> : null}
          </span>
        }
        description="What happened to this product's reusable data in this store — every proposal, every decision, and the one authoritative value in force today. Another store's decisions about the same barcode are not part of it."
        actions={
          <Button asChild variant="outline" size="sm">
            <Link to={`/data-review?status=ALL&upc=${encodeURIComponent(data?.item_code ?? itemCode ?? "")}${store ? `&store=${store}` : ""}`}>
              Open in queue
            </Link>
          </Button>
        }
      />
      {!store ? (
        <ErrorState error={new Error("A product's history is one store's history — open it from an invoice or a proposal so the store is known.")} title="Store not given" />
      ) : isError ? (
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
