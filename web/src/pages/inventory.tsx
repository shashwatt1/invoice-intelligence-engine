import { Boxes, ClipboardCheck, ClipboardList, PackageCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { InventoryItemRow, InventoryItemStatus, StoreInvoiceRow } from "@/api/types";
import { MovementHistory } from "@/components/inventory/movement-history";
import { OpeningCountDialog } from "@/components/inventory/opening-count-dialog";
import { ReceiptDialog } from "@/components/inventory/receipt-dialog";
import { PageHeader } from "@/components/layout/page-header";
import { FilterBar } from "@/components/shared/filter-bar";
import { MetricStrip, MetricStripSkeleton } from "@/components/shared/metric-strip";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { StatusPill } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useInventoryItems,
  useInventoryOverview,
  useInventoryStatus,
  usePendingApprovals,
  useStoreInvoices,
  useInventoryStores,
} from "@/hooks/use-api";
import { formatDate, formatDateTime } from "@/lib/format";
import { formatUnits, ITEM_STATUS, RECEIPT_STATUS, receivableReason, staleReason } from "@/lib/inventory";

const ITEM_PAGE_SIZE = 50;

const INVOICE_PAGE_SIZE = 25;
const TABS = ["products", "approvals", "invoices"] as const;
type Tab = (typeof TABS)[number];

function receiptAction(row: StoreInvoiceRow): { label: string; emphasis: boolean } {
  if (row.receipt?.status === "SUBMITTED") return { label: "Review", emphasis: true };
  if (row.receipt?.status === "POSTED") return { label: "View", emphasis: false };
  return { label: "Details", emphasis: false };
}

/**
 * Store inventory: per physical store, what was counted and what has been
 * received since — in sellable units, every figure traced to its movements.
 *
 * On hand exists only after an audited opening count; sales are not imported
 * yet, so every on-hand figure is provisional (count + receipts), and says so.
 * A receipt is never inferred and never entered directly: marking an invoice
 * Retailed submits it, and it moves stock only once an approver posts it from
 * the Approvals queue (or the invoice's row).
 * MANAGER/ADMIN, and only for stores the account has been granted: the store
 * list comes from the server (GET /inventory/stores) and every route checks the
 * grant again, so hiding stores here is convenience, not the control.
 */
export function InventoryPage() {
  const status = useInventoryStatus();
  const stores = useInventoryStores({ enabled: status.data?.enabled === true });
  const physical = useMemo(() => stores.data ?? [], [stores.data]);
  const [params, setParams] = useSearchParams();
  const requested = params.get("store");
  const storeId = physical.some((s) => s.id === requested) ? requested : null;
  const store = physical.find((s) => s.id === storeId) ?? null;
  const chooseStore = (id: string) => setParams((prev) => { const next = new URLSearchParams(prev); next.set("store", id); return next; });

  if (status.isLoading || (status.data?.enabled && stores.isLoading)) return <><Header /><TableSkeleton rows={6} /></>;
  if (status.isError) return <><Header /><ErrorState error={status.error} onRetry={() => void status.refetch()} /></>;
  if (!status.data?.enabled) {
    return (
      <>
        <Header />
        <EmptyState icon={Boxes} title="Inventory is not switched on"
                    description="Store inventory is available once an administrator enables it for this environment." />
      </>
    );
  }
  if (stores.isError) return <><Header /><ErrorState error={stores.error} onRetry={() => void stores.refetch()} /></>;

  return (
    <>
      <Header />
      <FilterBar>
        <Select value={storeId ?? ""} onValueChange={chooseStore}>
          <SelectTrigger className="w-[280px]" aria-label="Store"><SelectValue placeholder="Choose a physical store" /></SelectTrigger>
          <SelectContent>
            {physical.map((s) => <SelectItem key={s.id} value={s.id}>{s.label}</SelectItem>)}
          </SelectContent>
        </Select>
      </FilterBar>
      {!physical.length ? (
        <EmptyState icon={Boxes} title="No stores assigned to you"
                    description="Inventory access is granted per store. Ask an administrator to grant you access to a store." />
      ) : !store ? (
        <EmptyState icon={Boxes} title="Choose a store" description="Inventory is kept per physical store." />
      ) : (
        <StoreInventory key={store.id} storeId={store.id} storeLabel={store.label}
                        initialTab={TABS.find((t) => t === params.get("tab")) ?? "products"} />
      )}
    </>
  );
}

function Header() {
  return (
    <PageHeader
      title="Inventory"
      description="Per physical store, in sellable units: audited opening counts and approved receipts. Sales are not imported yet."
    />
  );
}

function StoreInventory({ storeId, storeLabel, initialTab }: { storeId: string; storeLabel: string; initialTab: Tab }) {
  const overview = useInventoryOverview(storeId);
  const [tab, setTab] = useState<string>(initialTab);
  const [counting, setCounting] = useState(false);
  const o = overview.data;

  return (
    <div className="space-y-5">
      {overview.isLoading ? (
        <MetricStripSkeleton count={6} />
      ) : overview.isError ? (
        <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
      ) : o ? (
        <>
          <MetricStrip items={[
            { key: "tracked", label: "Products tracked", value: o.tracked_products, hint: "with any movement here" },
            { key: "opening", label: "Opening stock required", value: o.opening_required, tone: o.opening_required ? "warning" : "neutral",
              hint: "no audited count yet" },
            { key: "out", label: "Out of stock", value: o.out_of_stock, tone: o.out_of_stock ? "danger" : "neutral" },
            { key: "in", label: "In stock", value: o.in_stock, tone: "success" },
            { key: "pending", label: "Awaiting approval", value: o.pending_approvals, tone: o.pending_approvals ? "warning" : "neutral",
              hint: "receipts submitted by Retailed" },
            { key: "fresh", label: "Last recorded", value: o.last_recorded_at ? formatDateTime(o.last_recorded_at) : "Never",
              hint: o.recent_receipts[0] ? `last receipt ${formatDate(o.recent_receipts[0].received_on)}` : "no receipts yet" },
          ]} />
          <p className="text-xs text-muted-foreground" data-testid="provisional-note">
            Provisional: on hand is the opening count plus receipts and adjustments since. Sales are not imported yet, so
            it overstates stock until they are. Nothing here is reconciled against the POS.
          </p>
        </>
      ) : null}

      <Tabs value={tab} onValueChange={setTab}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <TabsList>
            <TabsTrigger value="products">Products</TabsTrigger>
            <TabsTrigger value="approvals">
              Approvals{o?.pending_approvals ? ` (${o.pending_approvals})` : ""}
            </TabsTrigger>
            <TabsTrigger value="invoices">Invoices &amp; receipts</TabsTrigger>
          </TabsList>
          <Button variant="outline" size="sm" onClick={() => setCounting(true)}>
            <ClipboardList className="size-4" aria-hidden /> Record opening counts
          </Button>
        </div>
        <TabsContent value="products" className="mt-3"><ProductsTab storeId={storeId} /></TabsContent>
        <TabsContent value="approvals" className="mt-3"><ApprovalsTab storeId={storeId} /></TabsContent>
        <TabsContent value="invoices" className="mt-3"><InvoicesTab storeId={storeId} /></TabsContent>
      </Tabs>

      {counting && <OpeningCountDialog storeId={storeId} storeLabel={storeLabel} onClose={() => setCounting(false)} />}
    </div>
  );
}

function ProductsTab({ storeId }: { storeId: string }) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"ALL" | InventoryItemStatus>("ALL");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<InventoryItemRow | null>(null);
  useEffect(() => setPage(1), [search, status]);
  const params = useMemo(() => ({
    search: search.trim() || undefined, status: status === "ALL" ? undefined : status, page, page_size: ITEM_PAGE_SIZE,
  }), [search, status, page]);
  const query = useInventoryItems(storeId, params);

  return (
    <div className="space-y-3">
      <FilterBar>
        <Input placeholder="Search product or item code" value={search} onChange={(e) => setSearch(e.target.value)} className="max-w-[300px]" />
        <Select value={status} onValueChange={(v) => setStatus(v as typeof status)}>
          <SelectTrigger className="w-[220px]" aria-label="Stock status"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All products</SelectItem>
            <SelectItem value="OPENING_STOCK_REQUIRED">Opening stock required</SelectItem>
            <SelectItem value="OUT_OF_STOCK">Out of stock</SelectItem>
            <SelectItem value="IN_STOCK">In stock</SelectItem>
          </SelectContent>
        </Select>
      </FilterBar>
      {query.isLoading ? (
        <TableSkeleton rows={8} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : !query.data?.items.length ? (
        <EmptyState icon={Boxes} title="No products"
                    description={search || status !== "ALL" ? "No product matches these filters."
                      : "Nothing recorded at this store yet. Record opening counts, or approve a submitted receipt."} />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Opening count</TableHead>
                <TableHead className="text-right">Received since</TableHead>
                <TableHead className="text-right">On hand</TableHead>
                <TableHead>Last movement</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.items.map((row) => {
                const meta = ITEM_STATUS[row.status];
                return (
                  <TableRow key={row.item_code} data-testid="inventory-row">
                    <TableCell className="min-w-[220px]">
                      <div className="font-medium">{row.description ?? "—"}</div>
                      <div className="font-mono text-xs text-muted-foreground">{row.item_code}</div>
                    </TableCell>
                    <TableCell><StatusPill tone={meta.tone} label={meta.label} meaning={meta.meaning} /></TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.opening_quantity !== null ? (
                        <>{formatUnits(row.opening_quantity)}<div className="text-xs text-muted-foreground">{formatDate(row.opening_date)}</div></>
                      ) : "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{formatUnits(row.received)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.on_hand !== null ? (
                        <span title="Provisional: sales are not imported yet">{formatUnits(row.on_hand)}<span className="ml-1 text-xs text-muted-foreground">prov.</span></span>
                      ) : (
                        <span className="text-xs text-warning">Opening stock required</span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{formatDate(row.last_movement)}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => setSelected(row)}>History</Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          <Pagination page={page} pageSize={ITEM_PAGE_SIZE} total={query.data.total} onPageChange={setPage} />
        </>
      )}
      {selected && <MovementHistory storeId={storeId} item={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function InvoicesTab({ storeId }: { storeId: string }) {
  const [page, setPage] = useState(1);
  const [receiving, setReceiving] = useState<string | null>(null);
  const query = useStoreInvoices(storeId, { page, page_size: INVOICE_PAGE_SIZE });

  if (query.isLoading) return <TableSkeleton rows={6} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  if (!query.data?.items.length) {
    return <EmptyState icon={PackageCheck} title="No invoices" description="No invoice has been processed for this store." />;
  }
  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Invoice</TableHead>
            <TableHead>Vendor</TableHead>
            <TableHead>Date</TableHead>
            <TableHead className="text-right">Product lines</TableHead>
            <TableHead>Receipt</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {query.data.items.map((row) => (
            <TableRow key={row.invoice_id} data-testid="store-invoice-row">
              <TableCell>
                <Link className="font-medium underline-offset-2 hover:underline" to={`/invoices/${row.invoice_id}`}>
                  #{row.invoice_number ?? "—"}
                </Link>
                <div className="text-xs text-muted-foreground">{row.status.toLowerCase()}</div>
              </TableCell>
              <TableCell className="text-sm">{row.vendor ?? "—"}</TableCell>
              <TableCell className="text-sm text-muted-foreground">{formatDate(row.invoice_date)}</TableCell>
              <TableCell className="text-right tabular-nums">{row.product_lines}</TableCell>
              <TableCell className="text-sm">
                {row.receipt?.status === "REVERSED" ? (
                  <StatusPill tone="warning" label="Receipt reversed"
                              meaning={`by ${row.receipt.reversed_by ?? "—"}: ${row.receipt.reversal_reason ?? ""}`} />
                ) : row.receipt?.status === "POSTED" ? (
                  <StatusPill tone="success" label={`Received ${formatDate(row.receipt.received_on)}`}
                              meaning={`approved by ${row.receipt.confirmed_by ?? "—"}${row.receipt.discrepancy_count ? `, ${row.receipt.discrepancy_count} line(s) differed` : ""}`} />
                ) : row.receipt ? (
                  <StatusPill tone={RECEIPT_STATUS[row.receipt.status].tone} label={RECEIPT_STATUS[row.receipt.status].label}
                              meaning={RECEIPT_STATUS[row.receipt.status].meaning} />
                ) : row.receivable_reasons.length ? (
                  <span className="text-xs text-muted-foreground">{row.receivable_reasons.map(receivableReason).join("; ")}</span>
                ) : (
                  <StatusPill tone="neutral" label="Not submitted" meaning="Marking the invoice Retailed submits its receipt for approval." />
                )}
              </TableCell>
              <TableCell className="text-right">
                <Button variant={receiptAction(row).emphasis ? "outline" : "ghost"} size="sm"
                        onClick={() => setReceiving(row.invoice_id)}>
                  {receiptAction(row).label}
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Pagination page={page} pageSize={INVOICE_PAGE_SIZE} total={query.data.total} onPageChange={setPage} />
      {receiving && <ReceiptDialog invoiceId={receiving} onClose={() => setReceiving(null)} />}
    </>
  );
}

/**
 * The store's pending receipt submissions, oldest first. The server lists only a store
 * this account holds a grant for; each row opens the review dialog, which re-reads the
 * invoice and shows whether the submission still matches it.
 */
function ApprovalsTab({ storeId }: { storeId: string }) {
  const query = usePendingApprovals(storeId);
  const [reviewing, setReviewing] = useState<string | null>(null);

  if (query.isLoading) return <TableSkeleton rows={4} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  if (!query.data?.length) {
    return <EmptyState icon={ClipboardCheck} title="Nothing awaits approval"
                       description="Marking an invoice Retailed submits its receipt here. No stock moves until it is approved." />;
  }
  return (
    <>
      <Table data-testid="approval-queue">
        <TableHeader>
          <TableRow>
            <TableHead>Invoice</TableHead>
            <TableHead>Vendor</TableHead>
            <TableHead>Submitted</TableHead>
            <TableHead className="text-right">Lines</TableHead>
            <TableHead>State</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {query.data.map((row) => (
            <TableRow key={row.receipt.id} data-testid="approval-row">
              <TableCell>
                {row.invoice_id ? (
                  <Link className="font-medium underline-offset-2 hover:underline" to={`/invoices/${row.invoice_id}`}>
                    #{row.invoice_number ?? "—"}
                  </Link>
                ) : <span className="font-medium">#{row.invoice_number ?? "—"}</span>}
                <div className="text-xs text-muted-foreground">{formatDate(row.invoice_date)}</div>
              </TableCell>
              <TableCell className="text-sm">{row.vendor ?? "—"}</TableCell>
              <TableCell className="text-sm">
                {row.receipt.submitted_by ?? "—"}
                <div className="text-xs text-muted-foreground">{row.receipt.submitted_at ? formatDateTime(row.receipt.submitted_at) : ""}</div>
              </TableCell>
              <TableCell className="text-right tabular-nums">{row.receipt.line_count}</TableCell>
              <TableCell className="text-sm">
                {row.stale_reasons.length ? (
                  <StatusPill tone="warning" label="Out of date" meaning={row.stale_reasons.map(staleReason).join("; ")} />
                ) : row.blockers ? (
                  <StatusPill tone="danger" label={`${row.blockers} line(s) blocked`} meaning="No approved units-per-case conversion" />
                ) : (
                  <StatusPill tone="info" label="Ready to review" />
                )}
              </TableCell>
              <TableCell className="text-right">
                {row.invoice_id ? (
                  <Button variant="outline" size="sm" onClick={() => setReviewing(row.invoice_id)}>Review</Button>
                ) : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {reviewing && <ReceiptDialog invoiceId={reviewing} onClose={() => setReviewing(null)} />}
    </>
  );
}
