import { Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { DocumentStatus } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { rowDestination } from "@/lib/routes";
import { ConfidenceInline } from "@/components/shared/confidence-meter";
import { Pagination } from "@/components/shared/pagination";
import { StatusBadge } from "@/components/shared/status-badge";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useInvoices } from "@/hooks/use-api";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

const PAGE_SIZE = 15;

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: "ALL", label: "All statuses" },
  { value: "COMPLETED", label: "Completed" },
  { value: "REVIEW_REQUIRED", label: "Review required" },
  { value: "FAILED", label: "Failed" },
];

const SORT_OPTIONS: { value: string; label: string; sortBy: string; descending: boolean }[] = [
  { value: "newest", label: "Newest first", sortBy: "created_at", descending: true },
  { value: "oldest", label: "Oldest first", sortBy: "created_at", descending: false },
  { value: "total_desc", label: "Highest total", sortBy: "grand_total", descending: true },
  { value: "conf_asc", label: "Lowest confidence", sortBy: "confidence", descending: false },
  { value: "vendor", label: "Vendor A–Z", sortBy: "vendor", descending: false },
];

/** Debounce a value so typing doesn't fire a request per keystroke. */
function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export function HistoryPage() {
  const navigate = useNavigate();
  const [searchInput, setSearchInput] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [sortKey, setSortKey] = useState("newest");
  const [page, setPage] = useState(1);

  const search = useDebounced(searchInput.trim());
  const sort = SORT_OPTIONS.find((option) => option.value === sortKey) ?? SORT_OPTIONS[0];

  // New filters restart pagination.
  useEffect(() => setPage(1), [search, statusFilter, sortKey]);

  const params = useMemo(
    () => ({
      search: search || undefined,
      status: statusFilter === "ALL" ? undefined : (statusFilter as DocumentStatus),
      sort_by: sort.sortBy,
      descending: sort.descending,
      page,
      page_size: PAGE_SIZE,
    }),
    [search, statusFilter, sort, page],
  );

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useInvoices(params);
  const hasFilters = Boolean(search) || statusFilter !== "ALL";

  return (
    <>
      <PageHeader
        title="Invoice History"
        description="Every processed document — search, filter, and open details."
      />

      {/* Filter bar */}
      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <div className="relative min-w-64 flex-1">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search vendor, invoice number, or filename…"
            className="pl-8"
          />
        </div>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_FILTERS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sortKey} onValueChange={setSortKey}>
          <SelectTrigger className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SORT_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isPending ? (
        <TableSkeleton rows={8} />
      ) : data.items.length === 0 ? (
        <EmptyState
          title={hasFilters ? "No matching documents" : "No documents yet"}
          description={
            hasFilters
              ? "Try clearing the search or switching the status filter."
              : "Process your first invoice and it will show up here."
          }
        />
      ) : (
        <div className="space-y-3">
          <Card className="gap-0 p-0">
            <CardContent
              className={isPlaceholderData ? "px-2 pb-2 opacity-60 transition-opacity" : "px-2 pb-2"}
            >
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Document</TableHead>
                    <TableHead>Vendor</TableHead>
                    <TableHead>Invoice #</TableHead>
                    <TableHead>Invoice date</TableHead>
                    <TableHead className="text-right">Total</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Processed</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((row) => (
                    <TableRow
                      key={row.document_id}
                      className="cursor-pointer"
                      onClick={() => navigate(rowDestination(row))}
                    >
                      <TableCell className="max-w-48 truncate font-medium">
                        {row.filename}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {row.vendor_name ?? "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {row.invoice_number ?? "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatDate(row.invoice_date)}
                      </TableCell>
                      <TableCell className="text-right font-medium tabular-nums">
                        {formatMoney(row.grand_total, row.currency)}
                      </TableCell>
                      <TableCell>
                        <ConfidenceInline score={row.composite_confidence} />
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={row.status} />
                      </TableCell>
                      <TableCell className="text-right text-[0.78rem] whitespace-nowrap text-muted-foreground">
                        {formatDateTime(row.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={data.total}
            onPageChange={setPage}
          />
        </div>
      )}
    </>
  );
}
