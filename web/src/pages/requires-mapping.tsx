import { ListChecks, PackageSearch } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { MappingQueueListParams, MappingQueueRow } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { MappingWorkbench } from "@/components/requires-mapping/mapping-workbench";
import { FilterBar } from "@/components/shared/filter-bar";
import { MetricStrip } from "@/components/shared/metric-strip";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { StoreChip } from "@/components/shared/store-chip";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useMappingQueue, useMappingQueueSummary, useStores } from "@/hooks/use-api";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;
const MAX_INVOICE_LINKS = 3;

/**
 * The collaborative work queue: every (store, UPC) with a product line
 * on some invoice and no confirmed units-per-case mapping — regardless
 * of who uploaded that invoice, whether they are still logged in, or
 * whether anyone has even proposed a value yet. That last part is the
 * point: an unresolved mapping is visible here the moment it exists,
 * not only once someone submits a proposal for it.
 *
 * "Map" opens a focused workbench right here — no need to open the
 * invoice merely to enter a value — but submission still goes through
 * the exact same governed path as confirming from the invoice itself
 * (POST /invoices/{id}/case-mappings, a PENDING proposal), and approval
 * still happens on Master Data Review. This page has no write path of
 * its own; nothing here ever touches product_case_mappings directly.
 */
export function RequiresMappingPage() {
  const [search, setSearch] = useSearchParams();
  const [store, setStore] = useState(search.get("store") ?? "ALL");
  const [page, setPage] = useState(1);
  const [workbenchRow, setWorkbenchRow] = useState<MappingQueueRow | null>(null);

  useEffect(() => setPage(1), [store]);
  useEffect(() => {
    const next = new URLSearchParams();
    if (store !== "ALL") next.set("store", store);
    setSearch(next, { replace: true });
  }, [store, setSearch]);

  const params = useMemo<MappingQueueListParams>(
    () => ({ store_id: store === "ALL" ? undefined : store, page, page_size: PAGE_SIZE }),
    [store, page],
  );

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useMappingQueue(params);
  const summary = useMappingQueueSummary();
  const stores = useStores();

  return (
    <>
      <PageHeader
        title="Requires Mapping"
        description="Every product with no confirmed units-per-case, across every processed invoice — not just the ones you uploaded. Click Map to enter a value right here; a reviewer's approval on Master Data Review clears it everywhere the product appears."
      />

      <MetricStrip
        className="mb-4"
        items={[
          {
            key: "products", label: "Products",
            value: summary.data?.unique_products ?? "—",
            tone: summary.data?.unique_products ? "warning" : "neutral",
            hint: "distinct UPCs still unmapped",
          },
          {
            key: "occurrences", label: "Invoice occurrences",
            value: summary.data?.invoice_occurrences ?? "—",
            hint: "invoice lines waiting on one of them",
          },
          {
            key: "stores", label: "Stores",
            value: summary.data?.stores ?? "—",
            hint: "with at least one unresolved product",
          },
        ]}
      />

      <FilterBar summary={data ? `${data.total} product${data.total === 1 ? "" : "s"}` : null}>
        <Select value={store} onValueChange={setStore}>
          <SelectTrigger
            className={cn("h-8 w-44 text-[0.8rem]", store !== "ALL" && "border-primary/40 bg-accent/40")}
            aria-label="Store filter"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All stores</SelectItem>
            {(stores.data ?? []).map((s) => (
              <SelectItem key={s.id} value={s.id}>{s.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </FilterBar>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isPending ? (
        <TableSkeleton rows={8} />
      ) : data.items.length === 0 ? (
        <EmptyState
          icon={ListChecks}
          title="Nothing needs mapping"
          description="Every product on every processed invoice has a confirmed units-per-case mapping."
        />
      ) : (
        <div className="space-y-3">
          <div className={cn("surface overflow-hidden transition-opacity", isPlaceholderData && "opacity-60")}>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Store</TableHead>
                    <TableHead>Product</TableHead>
                    <TableHead>UPC</TableHead>
                    <TableHead>Affected invoices</TableHead>
                    <TableHead>Proposed value</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((row) => {
                    const uniqueInvoices = Array.from(
                      new Map(row.occurrences.map((o) => [o.invoice_id, o])).values(),
                    );
                    const shown = uniqueInvoices.slice(0, MAX_INVOICE_LINKS);
                    const overflow = row.invoice_count - shown.length;
                    return (
                      <TableRow key={`${row.store.id}-${row.item_code}`} data-testid="mapping-queue-row">
                        <TableCell><StoreChip store={row.store} link={false} compact /></TableCell>
                        <TableCell className="max-w-56" title={row.description ?? undefined}>
                          <div className="truncate text-[0.84rem] font-semibold tracking-[-0.01em]">
                            {row.description ?? <span className="font-normal text-muted-foreground">unnamed product</span>}
                          </div>
                        </TableCell>
                        <TableCell className="t-mono font-medium">{row.item_code}</TableCell>
                        <TableCell>
                          <div className="flex flex-wrap items-center gap-1.5">
                            {shown.map((occurrence) => (
                              <Link
                                key={occurrence.invoice_id}
                                to={`/invoices/${occurrence.invoice_id}`}
                                className="rounded-md bg-accent px-1.5 py-0.5 text-[0.72rem] font-medium text-accent-foreground hover:underline"
                                title={
                                  `${occurrence.invoice_number || occurrence.invoice_id} — qty ${occurrence.quantity}` +
                                  (occurrence.unit_price !== null ? ` @ $${occurrence.unit_price.toFixed(2)}` : "")
                                }
                                data-testid="mapping-queue-invoice-link"
                              >
                                {occurrence.invoice_number || occurrence.invoice_id.slice(0, 8)}
                              </Link>
                            ))}
                            {overflow > 0 ? <span className="t-meta">+{overflow} more</span> : null}
                          </div>
                        </TableCell>
                        <TableCell>
                          {row.pending_value !== null ? (
                            <span
                              className="rounded-full bg-warning/15 px-2 py-0.5 text-[0.72rem] font-semibold text-warning"
                              title="Submitted, awaiting a reviewer's decision on Master Data Review — pending, not yet authoritative"
                              data-testid="mapping-queue-pending"
                            >
                              Pending approval — {row.pending_value}
                            </span>
                          ) : (
                            <span className="t-meta">none proposed yet</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => setWorkbenchRow(row)}
                            data-testid="mapping-queue-map-button"
                          >
                            <PackageSearch className="size-3.5" /> Map
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </div>
      )}

      <MappingWorkbench row={workbenchRow} open={workbenchRow !== null} onOpenChange={(open) => !open && setWorkbenchRow(null)} />
    </>
  );
}
