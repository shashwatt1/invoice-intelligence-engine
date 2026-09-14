import { ClipboardCheck, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import type { ProposalListParams, ProposalSource, ProposalStatus } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { ProposalSourceBadge, ProposalStatusBadge } from "@/components/review/proposal-badges";
import { Pagination } from "@/components/shared/pagination";
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
import { usePendingProposalCount, useProposals } from "@/hooks/use-api";
import { formatDateTime } from "@/lib/format";
import { PROPOSAL_SOURCE_META, PROPOSAL_STATUS_META } from "@/lib/status";

const PAGE_SIZE = 25;

const STATUS_FILTERS: { value: ProposalStatus | "ALL"; label: string }[] = [
  { value: "PENDING", label: "Pending review" },
  { value: "APPROVED", label: "Approved" },
  { value: "REJECTED", label: "Rejected" },
  { value: "ALL", label: "All statuses" },
];

const SOURCE_FILTERS: { value: ProposalSource | "ALL"; label: string }[] = [
  { value: "ALL", label: "All evidence types" },
  ...(Object.keys(PROPOSAL_SOURCE_META) as ProposalSource[]).map((source) => ({
    value: source,
    label: PROPOSAL_SOURCE_META[source].label,
  })),
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
 * The master-data review queue.
 *
 * A proposal is what the frontend, the reference import, or an operator
 * PUT FORWARD. It is not master data until a reviewer approves it here
 * (or through the CLI). The default filter is PENDING because that is the
 * work; the other statuses are the immutable audit history.
 */
export function DataReviewPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const [status, setStatus] = useState<ProposalStatus | "ALL">(
    (search.get("status") as ProposalStatus | "ALL" | null) ?? "PENDING",
  );
  const [source, setSource] = useState<ProposalSource | "ALL">(
    (search.get("source") as ProposalSource | null) ?? "ALL",
  );
  const [upcInput, setUpcInput] = useState(search.get("upc") ?? "");
  const [invoiceInput, setInvoiceInput] = useState(search.get("invoice") ?? "");
  const [page, setPage] = useState(1);

  const upc = useDebounced(upcInput.trim());
  const invoice = useDebounced(invoiceInput.trim());

  useEffect(() => setPage(1), [status, source, upc, invoice]);
  useEffect(() => {
    const next = new URLSearchParams();
    if (status !== "PENDING") next.set("status", status);
    if (source !== "ALL") next.set("source", source);
    if (upc) next.set("upc", upc);
    if (invoice) next.set("invoice", invoice);
    setSearch(next, { replace: true });
  }, [status, source, upc, invoice, setSearch]);

  const params = useMemo<ProposalListParams>(
    () => ({
      status,
      source: source === "ALL" ? undefined : source,
      item_code: upc || undefined,
      invoice_id: /^[0-9a-f-]{36}$/i.test(invoice) ? invoice : undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [status, source, upc, invoice, page],
  );

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useProposals(params);
  const pendingCount = usePendingProposalCount();
  const hasFilters = source !== "ALL" || Boolean(upc) || Boolean(invoice);

  return (
    <>
      <PageHeader
        title="Data Review"
        description="Proposed master-data values awaiting a reviewer, and the immutable record of every decision. Only an approval here (or via the review CLI) writes the mapping an EDI uses."
        actions={
          pendingCount.isSuccess ? (
            <span className="text-warning bg-warning-soft inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[0.78rem] font-semibold">
              <ClipboardCheck className="size-3.5" />
              {pendingCount.data} pending
            </span>
          ) : null
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2.5">
        <Select value={status} onValueChange={(value) => setStatus(value as ProposalStatus | "ALL")}>
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
        <Select value={source} onValueChange={(value) => setSource(value as ProposalSource | "ALL")}>
          <SelectTrigger className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SOURCE_FILTERS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative w-48">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={upcInput}
            onChange={(event) => setUpcInput(event.target.value)}
            placeholder="UPC / item code"
            className="pl-8 font-mono"
            inputMode="numeric"
          />
        </div>
        <Input
          value={invoiceInput}
          onChange={(event) => setInvoiceInput(event.target.value)}
          placeholder="Invoice ID"
          className="w-72 font-mono"
          title="The invoice UUID the proposal was raised on (paste from the invoice page URL)"
        />
      </div>

      <p className="mb-3 text-[0.75rem] text-muted-foreground">
        {status === "ALL"
          ? "Every proposal ever made — pending, approved and rejected. Decisions are permanent; a changed value is a new proposal."
          : PROPOSAL_STATUS_META[status].meaning}
      </p>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isPending ? (
        <TableSkeleton rows={8} />
      ) : data.items.length === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title={
            status === "PENDING" && !hasFilters
              ? "Nothing waiting for review"
              : "No matching proposals"
          }
          description={
            status === "PENDING" && !hasFilters
              ? "Every submitted value has been decided. New proposals appear here when an operator confirms a mapping on an invoice or the reference import runs."
              : "Try another status, evidence type, UPC, or invoice."
          }
        />
      ) : (
        <div className="space-y-3">
          <Card className="gap-0 p-0">
            <CardContent
              className={isPlaceholderData ? "px-2 pb-2 opacity-60 transition-opacity" : "px-2 pb-2"}
            >
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>UPC</TableHead>
                      <TableHead>Field</TableHead>
                      <TableHead className="text-right">Current</TableHead>
                      <TableHead className="text-right">Proposed</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Evidence</TableHead>
                      <TableHead>Origin</TableHead>
                      <TableHead>Proposed by</TableHead>
                      <TableHead>Created</TableHead>
                      <TableHead>Reviewed</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((row) => (
                      <TableRow
                        key={row.id}
                        className="cursor-pointer"
                        onClick={() => navigate(`/data-review/proposals/${row.id}`)}
                        data-testid="proposal-row"
                      >
                        <TableCell className="font-mono text-[0.8rem] font-medium">{row.entity_key}</TableCell>
                        <TableCell className="text-[0.78rem] whitespace-nowrap text-muted-foreground">{row.field.replace(/_/g, " ")}</TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {row.current_value === null || row.current_value === undefined
                            ? "—"
                            : String(row.current_value)}
                        </TableCell>
                        <TableCell className="text-right font-semibold tabular-nums">
                          {String(row.proposed_value)}
                        </TableCell>
                        <TableCell>
                          <ProposalStatusBadge status={row.status} />
                        </TableCell>
                        <TableCell>
                          <ProposalSourceBadge source={row.source} />
                        </TableCell>
                        <TableCell className="max-w-56 truncate text-[0.75rem] text-muted-foreground">
                          {row.source_file ? (
                            <span title={`${row.source_file}${row.source_sheet ? ` · ${row.source_sheet}` : ""}${row.source_row !== null ? ` · row ${row.source_row}` : ""}`}>
                              {row.source_sheet ?? row.source_file}
                              {row.source_row !== null ? ` · r${row.source_row}` : ""}
                            </span>
                          ) : row.invoice_id ? (
                            <span title={row.invoice_id}>invoice {row.invoice_id.slice(0, 8)}…</span>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                        <TableCell className="max-w-32 truncate font-mono text-[0.72rem] text-muted-foreground" title={row.proposed_by}>
                          {row.proposed_by}
                        </TableCell>
                        <TableCell className="text-[0.75rem] whitespace-nowrap text-muted-foreground">
                          {formatDateTime(row.created_at)}
                        </TableCell>
                        <TableCell className="text-[0.75rem] whitespace-nowrap text-muted-foreground">
                          {row.reviewed_at ? (
                            <span title={row.review_note ?? undefined}>
                              {formatDateTime(row.reviewed_at)}
                              <span className="block font-mono text-[0.68rem]">{row.reviewed_by}</span>
                            </span>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPageChange={setPage} />
        </div>
      )}
    </>
  );
}
