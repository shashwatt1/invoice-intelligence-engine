import { Check, ClipboardCheck, Pencil, X } from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import type { ProposalListParams, ProposalSource, ProposalStatus } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { ActiveFilters, FilterBar, SearchInput } from "@/components/shared/filter-bar";
import { MetricStrip } from "@/components/shared/metric-strip";
import { type BulkAction, BulkDecisionDialog } from "@/components/review/bulk-decision-dialog";
import { BulkEditDialog } from "@/components/review/bulk-edit-dialog";
import { ProposalSourceBadge, ProposalStatusBadge } from "@/components/review/proposal-badges";
import { ProposedValueCell } from "@/components/review/proposed-value-cell";
import { StoreChip } from "@/components/shared/store-chip";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { useProposalCounts, useProposals, useStores } from "@/hooks/use-api";
import { formatDate, formatDateTime } from "@/lib/format";
import { isEditable } from "@/lib/proposals";
import { PROPOSAL_SOURCE_META, PROPOSAL_STATUS_META } from "@/lib/status";
import { storeOptionLabel } from "@/lib/stores";
import { cn } from "@/lib/utils";

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
  const counts = useProposalCounts();
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

  const chosenStore = stores.data?.find((x) => x.id === store);
  const chips = [
    ...(store !== "ALL" ? [{ key: "store", label: chosenStore ? storeOptionLabel(chosenStore) : "store", onRemove: () => setStore("ALL") }] : []),
    ...(source !== "ALL" ? [{ key: "source", label: PROPOSAL_SOURCE_META[source].label, onRemove: () => setSource("ALL") }] : []),
    ...(upc ? [{ key: "upc", label: `UPC ${upc}`, onRemove: () => setUpcInput("") }] : []),
    ...(invoice ? [{ key: "invoice", label: `invoice ${invoice.slice(0, 8)}…`, onRemove: () => setInvoiceInput("") }] : []),
  ];

  return (
    <>
      <PageHeader
        title="Master Data Review"
        description="Values proposed to become permanent master data — case mappings today. A person approves each one here; only that approval writes the mapping every future invoice for the store uses. Correcting an invoice never changes master data."
      />

      {/* Decision summary — real counts from the queue */}
      <MetricStrip
        className="mb-4"
        items={[
          { key: "pending", label: "Pending decisions", value: counts.data?.PENDING ?? "—", tone: counts.data?.PENDING ? "warning" : "neutral", hint: "awaiting a reviewer", onClick: () => setStatus("PENDING") },
          { key: "approved", label: "Approved", value: counts.data?.APPROVED ?? "—", tone: "success", hint: "wrote authoritative mappings", onClick: () => setStatus("APPROVED") },
          { key: "rejected", label: "Rejected", value: counts.data?.REJECTED ?? "—", hint: "master data untouched", onClick: () => setStatus("REJECTED") },
          { key: "ambiguous", label: "Needs evidence", value: counts.data ? counts.data.ambiguous : "—", tone: counts.data?.ambiguous ? "warning" : "neutral", hint: "pending, document notation ambiguous",
            onClick: () => { setStatus("PENDING"); setSource("document_ambiguous"); } },
        ]}
      />

      <FilterBar summary={data ? `${data.total} proposal${data.total === 1 ? "" : "s"}` : null}>
        <Select value={status} onValueChange={(value) => setStatus(value as ProposalStatus | "ALL")}>
          <SelectTrigger className="h-8 w-40 text-[0.8rem]" aria-label="Status filter"><SelectValue /></SelectTrigger>
          <SelectContent>
            {STATUS_FILTERS.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={store} onValueChange={setStore}>
          <SelectTrigger className={cn("h-8 w-44 text-[0.8rem]", store !== "ALL" && "border-primary/40 bg-accent/40")} aria-label="Store filter"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All stores</SelectItem>
            {(stores.data ?? []).map((s) => <SelectItem key={s.id} value={s.id}>{storeOptionLabel(s)}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={source} onValueChange={(value) => setSource(value as ProposalSource | "ALL")}>
          <SelectTrigger className={cn("h-8 w-48 text-[0.8rem]", source !== "ALL" && "border-primary/40 bg-accent/40")} aria-label="Evidence filter"><SelectValue /></SelectTrigger>
          <SelectContent>
            {SOURCE_FILTERS.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <SearchInput value={upcInput} onChange={setUpcInput} placeholder="UPC / item code" className="w-44" inputMode="numeric" mono />
        <SearchInput value={invoiceInput} onChange={setInvoiceInput} placeholder="Invoice ID" className="w-56" mono ariaLabel="Invoice ID" />
        <ActiveFilters chips={chips} onClear={() => { setStore("ALL"); setSource("ALL"); setUpcInput(""); setInvoiceInput(""); }} />
      </FilterBar>

      <p className="t-meta mb-3">
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
              className="flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-accent/50 px-3 py-2 shadow-sm"
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
          <div className={cn("surface overflow-hidden transition-opacity", isPlaceholderData && "opacity-60")}>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="w-8 pl-3">
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
                      <TableHead>Product</TableHead>
                      <TableHead>UPC</TableHead>
                      <TableHead>Field</TableHead>
                      <TableHead className="text-right">Current</TableHead>
                      <TableHead className="text-right">Proposed</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Evidence</TableHead>
                      <TableHead>Origin</TableHead>
                      <TableHead>Proposed</TableHead>
                      <TableHead>Reviewed</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((row, index) => (
                      <Fragment key={row.id}>
                      {status === "PENDING" && source === "ALL" && (index === 0 || data.items[index - 1].source !== row.source) ? (
                        <TableRow className="bg-surface-2/60 hover:bg-surface-2/60" data-testid="source-group">
                          <TableCell colSpan={12} className="t-eyebrow py-1.5 pl-3">
                            {PROPOSAL_SOURCE_META[row.source].label} · {PROPOSAL_SOURCE_META[row.source].blurb}
                          </TableCell>
                        </TableRow>
                      ) : null}
                      <TableRow
                        className="cursor-pointer row-hover"
                        onClick={() => navigate(`/data-review/proposals/${row.id}`)}
                        data-testid="proposal-row"
                        data-state={selected.has(row.id) ? "selected" : undefined}
                      >
                        <TableCell className="pl-3" onClick={(event) => event.stopPropagation()}>
                          <Checkbox
                            aria-label={`Select ${row.entity_key}`}
                            checked={selected.has(row.id)}
                            disabled={row.status !== "PENDING"}
                            onCheckedChange={(on) => toggleOne(row.id, on === true)}
                            data-testid="row-checkbox"
                          />
                        </TableCell>
                        <TableCell><StoreChip store={row.store} link={false} compact /></TableCell>
                        <TableCell className="max-w-56" title={row.description ?? undefined}>
                          <div className="truncate text-[0.84rem] font-semibold tracking-[-0.01em]">{row.description ?? <span className="font-normal text-muted-foreground">unnamed product</span>}</div>
                        </TableCell>
                        <TableCell className="t-mono font-medium">{row.entity_key}</TableCell>
                        <TableCell className="text-[0.78rem] whitespace-nowrap text-muted-foreground">{row.field.replace(/_/g, " ")}</TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {row.current_value === null || row.current_value === undefined
                            ? <span title="No authoritative value yet">none</span>
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
                        <TableCell className="max-w-40 truncate text-[0.74rem] text-muted-foreground">
                          {row.source_file ? (
                            <span title={`${row.source_file}${row.source_sheet ? ` · ${row.source_sheet}` : ""}${row.source_row !== null ? ` · row ${row.source_row}` : ""}`}>
                              {row.source_sheet ?? row.source_file}
                              {row.source_row !== null ? ` · r${row.source_row}` : ""}
                            </span>
                          ) : row.invoice_id ? (
                            <span title={row.invoice_id}>
                              {row.invoice_deleted ? (
                                <>invoice {row.invoice_id.slice(0, 8)}…</>
                              ) : (
                                <Link
                                  to={`/invoices/${row.invoice_id}`}
                                  className="hover:underline"
                                  onClick={(event) => event.stopPropagation()}
                                  data-testid="related-invoice"
                                >
                                  invoice {row.invoice_id.slice(0, 8)}…
                                </Link>
                              )}
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
                        <TableCell className="whitespace-nowrap" title={`${formatDateTime(row.created_at)} · ${row.proposed_by}`}>
                          <span className="block text-[0.76rem] text-foreground">{formatDate(row.created_at)}</span>
                          <span className="block max-w-32 truncate font-mono text-[0.64rem] text-muted-foreground">{row.proposed_by}</span>
                        </TableCell>
                        <TableCell className="whitespace-nowrap">
                          {row.reviewed_at ? (
                            <span title={`${formatDateTime(row.reviewed_at)}${row.review_note ? ` — ${row.review_note}` : ""}`}>
                              <span className="block text-[0.76rem] text-foreground">{formatDate(row.reviewed_at)}</span>
                              <span className="block max-w-32 truncate font-mono text-[0.64rem] text-muted-foreground">{row.reviewed_by}</span>
                            </span>
                          ) : (
                            <span className="t-meta">—</span>
                          )}
                        </TableCell>
                      </TableRow>
                      </Fragment>
                    ))}
                  </TableBody>
                </Table>
              </div>
          </div>
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
