import { Check, ClipboardCheck, Pencil, Search, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import type { ProposalListParams, ProposalSource, ProposalStatus } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { type BulkAction, BulkDecisionDialog } from "@/components/review/bulk-decision-dialog";
import { BulkEditDialog } from "@/components/review/bulk-edit-dialog";
import { ProposalSourceBadge, ProposalStatusBadge } from "@/components/review/proposal-badges";
import { ProposedValueCell } from "@/components/review/proposed-value-cell";
import { StoreChip } from "@/components/shared/store-chip";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
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
import { usePendingProposalCount, useProposals, useStores } from "@/hooks/use-api";
import { formatDateTime } from "@/lib/format";
import { isEditable } from "@/lib/proposals";
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
 *
 * Two ways to review. The table is the fast path: tick the obvious rows,
 * correct a value inline if needed, approve or reject them together with
 * one reviewer name. The proposal page is the deep path — evidence,
 * history, a note — for the rows that need it. Both end in the same
 * backend approve(); the table is only quicker.
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
  const [store, setStore] = useState(search.get("store") ?? "ALL");
  const [upcInput, setUpcInput] = useState(search.get("upc") ?? "");
  const [invoiceInput, setInvoiceInput] = useState(search.get("invoice") ?? "");
  const [page, setPage] = useState(1);

  const upc = useDebounced(upcInput.trim());
  const invoice = useDebounced(invoiceInput.trim());

  useEffect(() => setPage(1), [status, source, store, upc, invoice]);
  useEffect(() => {
    const next = new URLSearchParams();
    if (status !== "PENDING") next.set("status", status);
    if (source !== "ALL") next.set("source", source);
    if (store !== "ALL") next.set("store", store);
    if (upc) next.set("upc", upc);
    if (invoice) next.set("invoice", invoice);
    setSearch(next, { replace: true });
  }, [status, source, store, upc, invoice, setSearch]);

  const params = useMemo<ProposalListParams>(
    () => ({
      status,
      source: source === "ALL" ? undefined : source,
      store_id: store === "ALL" ? undefined : store,
      item_code: upc || undefined,
      invoice_id: /^[0-9a-f-]{36}$/i.test(invoice) ? invoice : undefined,
      page,
      page_size: PAGE_SIZE,
    }),
    [status, source, store, upc, invoice, page],
  );

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useProposals(params);
  const pendingCount = usePendingProposalCount();
  const stores = useStores();
  const hasFilters = source !== "ALL" || store !== "ALL" || Boolean(upc) || Boolean(invoice);

  // Selection is always a subset of the rows on screen: when filters or the
  // page change, rows that scrolled out of view are dropped, so "12 selected"
  // never counts something the reviewer cannot see.
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [bulkAction, setBulkAction] = useState<BulkAction | null>(null);
  const [editing, setEditing] = useState(false);
  const rows = useMemo(() => data?.items ?? [], [data]);
  const selectable = useMemo(() => rows.filter((row) => row.status === "PENDING"), [rows]);
  useEffect(() => {
    setSelected((prev) => {
      const visible = new Set(selectable.map((row) => row.id));
      const next = new Set([...prev].filter((id) => visible.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [selectable]);

  const selectedRows = useMemo(() => selectable.filter((row) => selected.has(row.id)), [selectable, selected]);
  const allSelected = selectable.length > 0 && selectedRows.length === selectable.length;
  const someSelected = selectedRows.length > 0 && !allSelected;
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(selectable.map((row) => row.id)));
  const toggleOne = (id: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  // A revision replaces a row with a new proposal id; keep it selected so the
  // reviewer can go straight on to approving what they just corrected.
  const onRevised = useCallback((supersededId: string, revisionId: string) => {
    setSelected((prev) => {
      if (!prev.has(supersededId)) return prev;
      const next = new Set(prev);
      next.delete(supersededId);
      next.add(revisionId);
      return next;
    });
  }, []);

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
        <Select value={store} onValueChange={setStore}>
          <SelectTrigger className="w-40 font-mono">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All stores</SelectItem>
            {(stores.data ?? []).map((s) => (
              <SelectItem key={s.id} value={s.id}>
                {s.label}
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
          {selectedRows.length > 0 ? (
            <div
              className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2"
              data-testid="bulk-toolbar"
            >
              <span className="text-[0.82rem] font-semibold" data-testid="selected-count">
                {selectedRows.length} selected
              </span>
              <span className="text-[0.72rem] text-muted-foreground">
                of {selectable.length} pending on this page
              </span>
              <div className="ml-auto flex flex-wrap gap-1.5">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!selectedRows.some(isEditable)}
                  onClick={() => setEditing(true)}
                >
                  <Pencil /> Edit selected
                </Button>
                <Button size="sm" onClick={() => setBulkAction("approve")}>
                  <Check strokeWidth={3} /> Approve selected
                </Button>
                <Button size="sm" variant="destructive" onClick={() => setBulkAction("reject")}>
                  <X /> Reject selected
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
                  Clear
                </Button>
              </div>
            </div>
          ) : null}
          <Card className="gap-0 p-0">
            <CardContent
              className={isPlaceholderData ? "px-2 pb-2 opacity-60 transition-opacity" : "px-2 pb-2"}
            >
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="w-8">
                        <Checkbox
                          aria-label={
                            allSelected
                              ? "Deselect all pending rows on this page"
                              : `Select all ${selectable.length} pending rows on this page`
                          }
                          checked={allSelected ? true : someSelected ? "indeterminate" : false}
                          disabled={selectable.length === 0}
                          onCheckedChange={toggleAll}
                          data-testid="select-all"
                        />
                      </TableHead>
                      <TableHead>Store</TableHead>
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
                        data-state={selected.has(row.id) ? "selected" : undefined}
                      >
                        <TableCell onClick={(event) => event.stopPropagation()}>
                          <Checkbox
                            aria-label={`Select ${row.entity_key}`}
                            checked={selected.has(row.id)}
                            disabled={row.status !== "PENDING"}
                            onCheckedChange={(on) => toggleOne(row.id, on === true)}
                            data-testid="row-checkbox"
                          />
                        </TableCell>
                        <TableCell><StoreChip store={row.store} link={false} compact /></TableCell>
                        <TableCell className="font-mono text-[0.8rem] font-medium">{row.entity_key}</TableCell>
                        <TableCell className="text-[0.78rem] whitespace-nowrap text-muted-foreground">{row.field.replace(/_/g, " ")}</TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {row.current_value === null || row.current_value === undefined
                            ? "—"
                            : String(row.current_value)}
                        </TableCell>
                        <TableCell className="text-right">
                          <ProposedValueCell row={row} onRevised={onRevised} />
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
                            <span title={row.invoice_id}>
                              invoice {row.invoice_id.slice(0, 8)}…
                              {row.invoice_deleted ? (
                                <span
                                  className="ml-1 rounded bg-muted px-1 text-[0.65rem] font-medium text-muted-foreground"
                                  title="The source invoice was deleted. This proposal is immutable history and stays; any mapping its approval wrote stays too."
                                  data-testid="invoice-deleted"
                                >
                                  invoice deleted
                                </span>
                              ) : null}
                            </span>
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

      <BulkDecisionDialog
        action={bulkAction}
        rows={selectedRows}
        onOpenChange={(open) => !open && setBulkAction(null)}
        onDone={(decidedIds) =>
          setSelected((prev) => new Set([...prev].filter((id) => !decidedIds.includes(id))))
        }
      />
      <BulkEditDialog
        open={editing}
        rows={selectedRows}
        onOpenChange={setEditing}
        onDone={(replaced) => {
          for (const [supersededId, revisionId] of Object.entries(replaced)) onRevised(supersededId, revisionId);
        }}
      />
    </>
  );
}
