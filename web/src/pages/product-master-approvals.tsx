import { ClipboardCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { CommercialCandidateRow } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { CommercialEvidencePanel } from "@/components/product-master/commercial-evidence-panel";
import { ReviewStatusBadge } from "@/components/product-master/review-status-badge";
import { FilterBar } from "@/components/shared/filter-bar";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useBulkApproveCommercialCandidates, useCommercialCandidates, useStores } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { roleLabel } from "@/lib/commercial";
import { formatDateTime } from "@/lib/format";
import { storeOptionLabel } from "@/lib/stores";
import { basisLabel, costLabel, ProductName, StoreContext } from "@/pages/product-master-review";

const PAGE_SIZE_OPTIONS = [25, 50, 100, 250, 500] as const;

function evidenceLabel(row: CommercialCandidateRow): string {
  const legacy = row.legacy_agreement === "AGREES" ? "legacy agrees"
    : row.legacy_agreement === "DISSENTS" ? "legacy dissents" : "no legacy mapping";
  return `${costLabel(row.cost_basis)} · ${legacy}`;
}

/**
 * The final-governance workbench for MANAGER/ADMIN: what awaits a decision,
 * with multi-select approval for the rows the evidence settled. It is a
 * convenience over the same governed approval — every selected mapping is
 * approved and recorded individually — and it never decides a conflict, a
 * pending proposal or a reopened mapping: those open the full evidence panel.
 */
export function ProductMasterApprovalsPage() {
  const { user } = useAuth();
  const canDecide = user?.role === "MANAGER" || user?.role === "ADMIN";
  const [status, setStatus] = useState("UNDECIDED");
  const [storeId, setStoreId] = useState("ALL");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<number>(50);
  // Selected mapping id → the row as it was when selected (its review_version is
  // what the approval is checked against). Independent of what is on screen:
  // searching, filtering and paging change the view, never the selection.
  const [selected, setSelected] = useState<Map<string, CommercialCandidateRow>>(new Map());
  const [confirming, setConfirming] = useState(false);
  const [open, setOpen] = useState<CommercialCandidateRow | null>(null);

  const params = useMemo(() => ({
    review_status: status === "ALL" ? undefined : status,
    store_id: storeId === "ALL" ? undefined : storeId,
    search: search.trim() || undefined,
    page,
    page_size: pageSize,
  }), [status, storeId, search, page, pageSize]);
  const query = useCommercialCandidates(params);
  const stores = useStores();

  useEffect(() => setPage(1), [status, storeId, search]);

  const rows = query.data?.items ?? [];
  const eligibleOnPage = rows.filter((r) => r.bulk_eligible);
  const allEligibleSelected = eligibleOnPage.length > 0 && eligibleOnPage.every((r) => selected.has(r.id));
  const hiddenSelected = [...selected.keys()].filter((id) => !rows.some((r) => r.id === id)).length;

  const toggle = (row: CommercialCandidateRow) => setSelected((prev) => {
    const next = new Map(prev);
    if (next.has(row.id)) next.delete(row.id);
    else next.set(row.id, row);
    return next;
  });
  // Adds or removes this page's eligible rows; selections elsewhere are kept.
  const toggleAll = () => setSelected((prev) => {
    const next = new Map(prev);
    for (const r of eligibleOnPage) {
      if (allEligibleSelected) next.delete(r.id);
      else next.set(r.id, r);
    }
    return next;
  });

  if (!canDecide) {
    return (
      <EmptyState icon={ClipboardCheck} title="Managers only"
                  description="Approving Product Master mappings is a manager or administrator decision." />
    );
  }

  return (
    <>
      <PageHeader
        title="Product Master — Approvals"
        description={
          "Mappings awaiting a manager decision. Select rows the evidence settled to approve them together — each is " +
          "approved and recorded individually. Conflicts, proposals and reopened mappings are decided one at a time."
        }
      />

      <FilterBar>
        <Input placeholder="Search UPC, item code or name" value={search}
               onChange={(event) => setSearch(event.target.value)} className="max-w-[240px]" />
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="w-[190px]" aria-label="Review status"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="UNDECIDED">Awaiting approval</SelectItem>
            <SelectItem value="READY_FOR_REVIEW">Ready for review</SelectItem>
            <SelectItem value="PENDING">Proposal pending</SelectItem>
            <SelectItem value="CONFLICT">Conflict</SelectItem>
            <SelectItem value="NO_MULTIPLIER">No multiplier</SelectItem>
            <SelectItem value="APPROVED">Approved</SelectItem>
            <SelectItem value="REJECTED">Rejected</SelectItem>
            <SelectItem value="ALL">All states</SelectItem>
          </SelectContent>
        </Select>
        <Select value={storeId} onValueChange={setStoreId}>
          <SelectTrigger className="w-[230px]" aria-label="Store"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All stores</SelectItem>
            {(stores.data ?? []).map((s) => <SelectItem key={s.id} value={s.id}>{storeOptionLabel(s)}</SelectItem>)}
          </SelectContent>
        </Select>
        <div className="ml-auto flex items-center gap-2">
          {selected.size > 0 && (
            <>
              <span className="text-xs text-muted-foreground" data-testid="selection-summary">
                {selected.size} selected{hiddenSelected ? ` · ${hiddenSelected} not in this view` : ""}
              </span>
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Map())}>Clear selection</Button>
            </>
          )}
          <Button disabled={selected.size === 0} onClick={() => setConfirming(true)}>
            Approve Selected ({selected.size})
          </Button>
        </div>
      </FilterBar>

      {query.isLoading ? (
        <TableSkeleton rows={8} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState icon={ClipboardCheck} title="Nothing awaiting approval" description="No mappings match these filters." />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">
                  <Checkbox aria-label="Select every eligible row on this page" checked={allEligibleSelected}
                            disabled={eligibleOnPage.length === 0} onCheckedChange={toggleAll} />
                </TableHead>
                <TableHead>Product</TableHead>
                <TableHead>UPC / PDI item</TableHead>
                <TableHead>Store</TableHead>
                <TableHead className="text-right">Units/case</TableHead>
                <TableHead className="text-right">Case cost</TableHead>
                <TableHead>Interpretation</TableHead>
                <TableHead>Evidence</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Submitted</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id} data-testid="approval-row" data-conflict={row.is_conflict || undefined}>
                  <TableCell>
                    {row.bulk_eligible ? (
                      <Checkbox aria-label={`Select ${row.canonical_identifier ?? row.id}`}
                                checked={selected.has(row.id)} onCheckedChange={() => toggle(row)} />
                    ) : (
                      <span className="text-[0.68rem] text-muted-foreground" title="Needs an individual decision">—</span>
                    )}
                  </TableCell>
                  <TableCell className="min-w-[200px] max-w-[280px] text-sm"><ProductName row={row} /></TableCell>
                  <TableCell className="font-mono text-xs">
                    {row.canonical_identifier ?? "—"}
                    <div className="text-muted-foreground">{row.pdi_item_code ?? ""}</div>
                  </TableCell>
                  <TableCell className="text-sm"><StoreContext row={row} /></TableCell>
                  <TableCell className="text-right tabular-nums">{row.units_accounted_for ?? "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">{row.case_cost === null ? "—" : row.case_cost.toFixed(2)}</TableCell>
                  <TableCell className="text-sm">{basisLabel(row.commercial_unit_basis)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{evidenceLabel(row)}</TableCell>
                  <TableCell><ReviewStatusBadge status={row.review_status} lastDecision={row.last_decision} /></TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {row.proposed_by ? (
                      <>
                        <div className="text-foreground">{row.proposed_by}</div>
                        {row.proposed_at ? formatDateTime(row.proposed_at) : null}
                      </>
                    ) : "Evidence candidate"}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="outline" size="sm" onClick={() => setOpen(row)}>
                      {row.bulk_eligible ? "Review" : "Review individually"}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Pagination page={page} pageSize={pageSize} total={query.data!.total} onPageChange={setPage}
                      pageSizeOptions={PAGE_SIZE_OPTIONS}
                      onPageSizeChange={(size) => { setPageSize(size); setPage(1); }} />
        </>
      )}

      {confirming && (
        <BulkApprovalDialog
          rows={rows}
          selected={selected}
          onClose={() => setConfirming(false)}
          onFinished={() => { setSelected(new Map()); setConfirming(false); void query.refetch(); }}
        />
      )}
      {open && <CommercialEvidencePanel candidate={open} onClose={() => setOpen(null)} />}
    </>
  );
}

/**
 * Confirms a multi-select approval. Each selected mapping is checked against the
 * latest row on screen when it is visible, otherwise against the row as it was
 * selected. If a mapping was decided, proposed on or reopened since it was
 * selected, nothing is sent — the reviewer refreshes and selects again. The
 * server makes the authoritative check under a lock (version and eligibility)
 * and refuses the whole request if any mapping fails it.
 */
function BulkApprovalDialog({ rows, selected, onClose, onFinished }: {
  rows: CommercialCandidateRow[];
  selected: Map<string, CommercialCandidateRow>;
  onClose: () => void;
  onFinished: () => void;
}) {
  const { user } = useAuth();
  const bulk = useBulkApproveCommercialCandidates();
  const [basis, setBasis] = useState("");

  const byId = new Map(rows.map((r) => [r.id, r]));
  let eligible = 0;
  let conflicts = 0;
  let decided = 0;
  let changed = 0;
  for (const [id, seen] of selected) {
    const row = byId.get(id) ?? seen;
    const version = seen.review_version ?? 0;
    if (row.approval_state === "APPROVED" || row.approval_state === "REJECTED") decided += 1;
    else if (row.review_status === "CONFLICT") conflicts += 1;
    else if (!row.bulk_eligible || (row.review_version ?? 0) !== version) changed += 1;
    else eligible += 1;
  }
  const queueChanged = eligible !== selected.size;

  const submit = () => bulk.mutate(
    {
      items: [...selected.values()].map((row) => ({ mapping_id: row.id, expected_review_version: row.review_version ?? 0 })),
      note: basis.trim(),
    },
    {
      onSuccess: (result) => {
        toast.success(`Approved ${result.approved} mappings — each recorded as its own decision.`);
        onFinished();
      },
      onError: (error) => {
        toast.error(error instanceof ApiError && error.statusCode === 409
          ? "The queue changed since you selected these rows. Nothing was approved — refresh and select again."
          : error instanceof ApiError ? error.userMessage : "Nothing was approved.");
        onFinished();
      },
    },
  );

  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">Approve {selected.size} selected mappings?</AlertDialogTitle>
          <AlertDialogDescription>
            Each selected mapping becomes the authoritative commercial mapping for its store and is recorded as its own
            decision. Master data only — EDI output does not change.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm" data-testid="bulk-counts">
          <dt className="text-muted-foreground">Selected</dt><dd className="tabular-nums">{selected.size}</dd>
          <dt className="text-muted-foreground">Eligible</dt><dd className="tabular-nums">{eligible}</dd>
          <dt className="text-muted-foreground">Conflicts</dt><dd className="tabular-nums">{conflicts}</dd>
          <dt className="text-muted-foreground">Already decided</dt><dd className="tabular-nums">{decided}</dd>
          {changed ? (<><dt className="text-muted-foreground">Changed since selected</dt><dd className="tabular-nums">{changed}</dd></>) : null}
          <dt className="text-muted-foreground">Reviewer</dt>
          <dd>{user?.username ?? "—"} · {roleLabel(user?.role)}</dd>
        </dl>
        {queueChanged ? (
          <p className="text-sm text-danger" role="alert">
            The queue changed since you selected these rows. Refresh and select again — nothing will be approved.
          </p>
        ) : (
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor="bulk-basis">Decision basis</label>
            <Input id="bulk-basis" value={basis} onChange={(event) => setBasis(event.target.value)}
                   placeholder="What these approvals rest on" aria-required />
            <p className="text-xs text-muted-foreground">Required. Recorded with every one of these approvals.</p>
          </div>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose} disabled={bulk.isPending}>Cancel</Button>
          <Button onClick={submit} disabled={queueChanged || !basis.trim() || bulk.isPending}>
            Approve {eligible}
          </Button>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
