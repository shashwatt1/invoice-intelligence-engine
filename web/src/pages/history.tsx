import { ArrowDownUp, FileClock } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import type { DocumentStatus } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { ActiveFilters, FilterBar, SearchInput } from "@/components/shared/filter-bar";
import { InvoiceRow, InvoiceTableHead } from "@/components/shared/invoice-table";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody } from "@/components/ui/table";
import { useInvoices } from "@/hooks/use-api";
import { rowDestination } from "@/lib/routes";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 15;

/** Only the states the API filters on. "Needs attention" is the two waiting states. */
const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "ALL", label: "All statuses" },
  { value: "COMPLETED", label: "Validated" },
  { value: "REVIEW_REQUIRED", label: "Needs review" },
  { value: "STORE_CONFIRMATION_REQUIRED", label: "Waiting for a store" },
  { value: "FAILED", label: "Failed" },
];

const SORT_OPTIONS: { value: string; label: string; sortBy: string; descending: boolean }[] = [
  { value: "newest", label: "Newest first", sortBy: "created_at", descending: true },
  { value: "oldest", label: "Oldest first", sortBy: "created_at", descending: false },
  { value: "total_desc", label: "Highest amount", sortBy: "grand_total", descending: true },
  { value: "conf_asc", label: "Lowest confidence", sortBy: "confidence", descending: false },
  { value: "vendor", label: "Vendor A–Z", sortBy: "vendor", descending: false },
];

function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

/**
 * Every processed document. Filters live in the URL so a link to
 * "?status=REVIEW_REQUIRED" from the dashboard lands on the right view;
 * only filters the API actually supports are offered.
 */
export function HistoryPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const [searchInput, setSearchInput] = useState(search.get("q") ?? "");
  const [statusFilter, setStatusFilter] = useState(search.get("status") ?? "ALL");
  const [sortKey, setSortKey] = useState(search.get("sort") ?? "newest");
  const [page, setPage] = useState(1);

  const query = useDebounced(searchInput.trim());
  const sort = SORT_OPTIONS.find((option) => option.value === sortKey) ?? SORT_OPTIONS[0];

  useEffect(() => setPage(1), [query, statusFilter, sortKey]);
  useEffect(() => {
    const next = new URLSearchParams();
    if (query) next.set("q", query);
    if (statusFilter !== "ALL") next.set("status", statusFilter);
    if (sortKey !== "newest") next.set("sort", sortKey);
    setSearch(next, { replace: true });
  }, [query, statusFilter, sortKey, setSearch]);

  const params = useMemo(
    () => ({
      search: query || undefined,
      status: statusFilter === "ALL" ? undefined : (statusFilter as DocumentStatus),
      sort_by: sort.sortBy,
      descending: sort.descending,
      page,
      page_size: PAGE_SIZE,
    }),
    [query, statusFilter, sort, page],
  );

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useInvoices(params);
  const chips = [
    ...(query ? [{ key: "q", label: `“${query}”`, onRemove: () => setSearchInput("") }] : []),
    ...(statusFilter !== "ALL" ? [{ key: "status", label: STATUS_FILTERS.find((s) => s.value === statusFilter)?.label ?? statusFilter, onRemove: () => setStatusFilter("ALL") }] : []),
  ];
  const clearAll = () => {
    setSearchInput("");
    setStatusFilter("ALL");
  };

  return (
    <>
      <PageHeader
        title="Invoices"
        description="Every document the pipeline has read — its state, its store, and what still needs a person."
        actions={<Button asChild><Link to="/process">Process an invoice</Link></Button>}
      />

      <FilterBar summary={data ? `${data.total} invoice${data.total === 1 ? "" : "s"}` : null}>
        <SearchInput value={searchInput} onChange={setSearchInput} placeholder="Search vendor, invoice number or filename" className="w-80 max-md:w-full" />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className={cn("h-8 w-44 text-[0.8rem]", statusFilter !== "ALL" && "border-primary/40 bg-accent/40")} aria-label="Status filter">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_FILTERS.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={sortKey} onValueChange={setSortKey}>
          <SelectTrigger className="h-8 w-44 text-[0.8rem]" aria-label="Sort">
            <ArrowDownUp className="size-3.5 text-muted-foreground" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SORT_OPTIONS.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <ActiveFilters chips={chips} onClear={clearAll} />
      </FilterBar>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isPending ? (
        <TableSkeleton rows={8} />
      ) : data.items.length === 0 ? (
        <EmptyState
          icon={FileClock}
          title={chips.length ? "No invoices match" : "No invoices yet"}
          description={chips.length ? "Clear the search or the status filter." : "Process the first invoice and it will appear here."}
          action={chips.length ? <Button variant="outline" size="sm" onClick={clearAll}>Clear filters</Button> : <Button asChild><Link to="/process">Process an invoice</Link></Button>}
        />
      ) : (
        <div className="space-y-3">
          <div className={cn("surface overflow-hidden transition-opacity", isPlaceholderData && "opacity-60")} aria-busy={isPlaceholderData}>
            <Table>
              <InvoiceTableHead />
              <TableBody>
                {data.items.map((row) => <InvoiceRow key={row.document_id} row={row} onOpen={() => navigate(rowDestination(row))} />)}
              </TableBody>
            </Table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </div>
      )}
    </>
  );
}
