import { ArrowDownUp, ArrowLeft, Boxes, ChevronRight, ClipboardCheck, ClipboardList, PackageCheck, Store, Truck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type {
  InventoryItemRow,
  InventoryItemSort,
  InventoryItemStatus,
  InventoryOverview,
  OpeningState,
  ResolvedValue,
  StoreInventoryEntry,
  StoreInvoiceReceiptFilter,
  StoreInvoiceRow,
} from "@/api/types";
import { MovementHistory } from "@/components/inventory/movement-history";
import { OpeningCountDialog } from "@/components/inventory/opening-count-dialog";
import { ReceiptDialog } from "@/components/inventory/receipt-dialog";
import { ZeroStartDialog } from "@/components/inventory/zero-start-dialog";
import { PageHeader } from "@/components/layout/page-header";
import { ActiveFilters, FilterBar, SearchInput } from "@/components/shared/filter-bar";
import { MetricStrip, MetricStripSkeleton } from "@/components/shared/metric-strip";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { StatusPill } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useInventoryAllStores,
  useInventoryItems,
  useInventoryOverview,
  useInventoryStatus,
  usePendingApprovals,
  useStoreInvoices,
} from "@/hooks/use-api";
import { formatDate, formatDateTime } from "@/lib/format";
import { useAuth } from "@/hooks/use-auth";
import { formatUnits, ITEM_STATUS, RECEIPT_STATUS, receivableReason, staleReason, UNITS_SOURCE } from "@/lib/inventory";
import { cn } from "@/lib/utils";

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
  const stores = useInventoryAllStores({ enabled: status.data?.enabled === true });
  const entries = useMemo(() => stores.data?.stores ?? [], [stores.data]);
  const [params, setParams] = useSearchParams();
  const requested = params.get("store");
  // Only a store the account holds a grant for can be opened; a listed-but-locked store has no store view.
  const store = entries.find((e) => e.access_granted && e.store.id === requested)?.store ?? null;
  const chooseStore = (id: string | null) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (id) next.set("store", id); else next.delete("store");
    next.delete("tab");
    return next;
  });

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

  if (!entries.length) {
    return (
      <>
        <Header />
        <EmptyState icon={Boxes} title="No stores assigned to you"
                    description="Inventory access is granted per store. Ask an administrator to grant you access to a store." />
      </>
    );
  }
  if (!store) return <><Header /><StoreGrid entries={entries} notGranted={stores.data?.not_granted ?? 0} onOpen={chooseStore} /></>;
  return (
    <>
      <Header />
      <div className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="store-header">
        <Button variant="ghost" size="sm" onClick={() => chooseStore(null)} className="-ml-2">
          <ArrowLeft className="size-4" aria-hidden /> All stores
        </Button>
        <div className="min-w-0">
          <h2 className="text-[1.05rem] font-semibold tracking-tight">{store.label}</h2>
          {store.address ? <div className="t-meta">{store.address}</div> : null}
        </div>
      </div>
      <StoreInventory key={store.id} storeId={store.id} storeLabel={store.label}
                      initialTab={TABS.find((t) => t === params.get("tab")) ?? "products"} />
    </>
  );
}

function Header() {
  return (
    <PageHeader
      title="Inventory"
      description="Per physical store, in sellable units: audited opening counts and approved receipts. Sales are not imported yet."
      actions={<Button asChild variant="outline" size="sm"><Link to="/inventory/supply"><Truck className="size-3.5" aria-hidden /> Supply history</Link></Button>}
    />
  );
}

const STORE_SHOW = [
  { value: "all", label: "All stores" },
  { value: "awaiting", label: "Awaiting approval" },
  { value: "opening", label: "Opening count needed" },
  { value: "not_set", label: "Opening not entered" },
  { value: "no_access", label: "No access for me" },
  { value: "no_activity", label: "No activity yet" },
] as const;
type StoreShow = (typeof STORE_SHOW)[number]["value"];

const STORE_SORT = [
  { value: "name", label: "Name A–Z" },
  { value: "awaiting", label: "Most awaiting approval" },
  { value: "products", label: "Most products" },
  { value: "recent", label: "Recently received" },
] as const;
type StoreSort = (typeof STORE_SORT)[number]["value"];

function lastReceipt(o: InventoryOverview | undefined): string | null {
  return o?.recent_receipts[0]?.received_on ?? null;
}

const OPENING_PILL: Record<OpeningState, { label: string; tone: "warning" | "neutral" | "success"; meaning: string }> = {
  NOT_SET: { label: "Opening count not entered", tone: "warning",
             meaning: "No opening count and no zero start: this store's stock is unknown, not zero." },
  ZERO_START: { label: "Zero start (not counted)", tone: "neutral",
                meaning: "An administrator declared the store started at zero. It is not a physical count." },
  COUNTED: { label: "Counted", tone: "success", meaning: "Physical opening counts were entered for this store." },
};

function openingState(o: InventoryOverview): OpeningState {
  return o.opening_state ?? "NOT_SET";
}

function matchesShow(show: StoreShow, entry: StoreInventoryEntry): boolean {
  const o = entry.overview ?? undefined;
  if (show === "all") return true;
  if (show === "no_access") return !entry.access_granted;
  if (!o) return false;                      // no figures (locked): shown only under "All stores" / "No access"
  if (show === "not_set") return openingState(o) === "NOT_SET";
  if (show === "awaiting") return o.pending_approvals > 0;
  if (show === "opening") return o.opening_required > 0;
  return o.tracked_products === 0 && o.pending_approvals === 0;
}

/**
 * One card per store this account holds a grant for (the server's list), each with the
 * store's figures at a glance. Opening a card opens that store's inventory. Filters and
 * sort live in the URL, so going back to "All stores" keeps them.
 */
function StoreGrid({ entries, notGranted, onOpen }: { entries: StoreInventoryEntry[]; notGranted: number; onOpen: (id: string) => void }) {
  const [params, setParams] = useSearchParams();
  const search = params.get("q") ?? "";
  const show = (STORE_SHOW.find((o) => o.value === params.get("show"))?.value ?? "all") as StoreShow;
  const sort = (STORE_SORT.find((o) => o.value === params.get("sort"))?.value ?? "name") as StoreSort;
  const setParam = (key: string, value: string, fallback: string) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    if (value && value !== fallback) next.set(key, value); else next.delete(key);
    return next;
  }, { replace: true });

  const term = search.trim().toLowerCase();
  const visible = entries
    .filter(({ store: s }) => !term || s.label.toLowerCase().includes(term) || (s.address ?? "").toLowerCase().includes(term))
    .filter((e) => matchesShow(show, e))
    .sort((ea, eb) => {
      const oa = ea.overview ?? undefined;
      const ob = eb.overview ?? undefined;
      const byName = ea.store.label.localeCompare(eb.store.label);
      if (sort === "awaiting") return (ob?.pending_approvals ?? -1) - (oa?.pending_approvals ?? -1) || byName;
      if (sort === "products") return (ob?.tracked_products ?? -1) - (oa?.tracked_products ?? -1) || byName;
      if (sort === "recent") return (lastReceipt(ob) ?? "").localeCompare(lastReceipt(oa) ?? "") || byName;
      return byName;
    });
  const chips = [
    ...(term ? [{ key: "q", label: `“${search.trim()}”`, onRemove: () => setParam("q", "", "") }] : []),
    ...(show !== "all" ? [{ key: "show", label: STORE_SHOW.find((o) => o.value === show)!.label, onRemove: () => setParam("show", "all", "all") }] : []),
  ];

  return (
    <>
      <FilterBar summary={`${visible.length} of ${entries.length} store${entries.length === 1 ? "" : "s"}`}>
        <SearchInput value={search} onChange={(v) => setParam("q", v, "")} placeholder="Search store name or address"
                     className="w-72 max-md:w-full" />
        <Select value={show} onValueChange={(v) => setParam("show", v, "all")}>
          <SelectTrigger className={cn("h-8 w-52 text-[0.8rem]", show !== "all" && "border-primary/40 bg-accent/40")} aria-label="Show stores">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STORE_SHOW.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(v) => setParam("sort", v, "name")}>
          <SelectTrigger className="h-8 w-52 text-[0.8rem]" aria-label="Sort stores">
            <ArrowDownUp className="size-3.5 text-muted-foreground" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STORE_SORT.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <ActiveFilters chips={chips} onClear={() => setParams((prev) => {
          const next = new URLSearchParams(prev); next.delete("q"); next.delete("show"); return next;
        }, { replace: true })} />
      </FilterBar>
      {notGranted > 0 ? (
        <p className="mb-3 text-xs text-muted-foreground" data-testid="not-granted-note">
          {notGranted} store{notGranted === 1 ? " is" : "s are"} listed without figures because your account cannot
          open {notGranted === 1 ? "it" : "them"}.
        </p>
      ) : null}
      {!visible.length ? (
        <EmptyState icon={Store} title="No store matches" description="Clear the search or the filter." />
      ) : (
        <div className="grid grid-cols-3 gap-4 max-xl:grid-cols-2 max-md:grid-cols-1" data-testid="store-grid">
          {visible.map((e) => <StoreCard key={e.store.id} entry={e} onOpen={() => onOpen(e.store.id)} />)}
        </div>
      )}
    </>
  );
}

function StoreCard({ entry, onOpen }: { entry: StoreInventoryEntry; onOpen: () => void }) {
  const { store, overview: o } = entry;
  const received = lastReceipt(o ?? undefined);
  const header = (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="truncate text-[0.98rem] font-semibold tracking-tight">{store.label}</div>
        <div className="t-meta mt-0.5 truncate">{store.address ?? "No address recorded"}</div>
      </div>
      {entry.access_granted
        ? <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden />
        : null}
    </div>
  );
  if (!entry.access_granted || !o) {
    return (
      <div data-testid="store-card" data-access="none" className="surface flex flex-col gap-4 p-5">
        {header}
        <p className="t-meta">
          No figures: your account cannot open this store.
        </p>
        <StatusPill size="xs" tone="neutral" label="No access for your account" />
      </div>
    );
  }
  const state = openingState(o);
  const pill = OPENING_PILL[state];
  return (
    <button type="button" onClick={onOpen} data-testid="store-card" aria-label={`Open ${store.label} inventory`}
            className="surface surface-hover group flex flex-col gap-4 p-5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50">
      {header}
      <div className="grid grid-cols-3 gap-2 border-y border-border py-3">
        <CardStat label="Products" value={o.tracked_products} />
        <CardStat label="Available units" value={o.on_hand_units == null ? "—" : formatUnits(o.on_hand_units)} />
        <CardStat label="Need opening count" value={o.opening_required} warn={o.opening_required > 0} />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatusPill size="xs" tone={pill.tone} label={state === "COUNTED" ? `Counted · ${o.opening_counted_products ?? 0} products` : pill.label}
                    meaning={pill.meaning} />
        {o.pending_approvals ? (
          <StatusPill size="xs" tone="warning" label={`${o.pending_approvals} awaiting approval`}
                      meaning="Receipts submitted by marking invoices Retailed; no stock moves until approved." />
        ) : null}
      </div>
      <div className="t-meta">{received ? `Last receipt ${formatDate(received)}` : "No receipts yet"}</div>
    </button>
  );
}

function CardStat({ label, value, warn = false }: { label: string; value: number | string; warn?: boolean }) {
  return (
    <div>
      <div className={cn("text-[1.15rem] font-semibold tabular-nums", warn && "text-warning")}>{value}</div>
      <div className="t-meta">{label}</div>
    </div>
  );
}

function StoreInventory({ storeId, storeLabel, initialTab }: { storeId: string; storeLabel: string; initialTab: Tab }) {
  const overview = useInventoryOverview(storeId);
  const [tab, setTab] = useState<string>(initialTab);
  const [counting, setCounting] = useState(false);
  const [zeroStart, setZeroStart] = useState(false);
  const isAdmin = useAuth().user?.role === "ADMIN";
  const o = overview.data;

  return (
    <div className="space-y-5">
      {overview.isLoading ? (
        <MetricStripSkeleton count={6} />
      ) : overview.isError ? (
        <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
      ) : o ? (
        <>
          <div className="flex flex-wrap items-center gap-2" data-testid="opening-state">
            <StatusPill tone={OPENING_PILL[openingState(o)].tone} meaning={OPENING_PILL[openingState(o)].meaning}
                        label={openingState(o) === "COUNTED"
                          ? `Counted · ${o.opening_counted_products ?? 0} product${o.opening_counted_products === 1 ? "" : "s"}`
                          : OPENING_PILL[openingState(o)].label} />
            <span className="t-meta">
              {o.on_hand_units == null ? "Available units unknown" : `${formatUnits(o.on_hand_units)} available units`}
            </span>
          </div>
          <MetricStrip items={[
            { key: "tracked", label: "Products tracked", value: o.tracked_products, hint: "in the catalogue or moved" },
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
          {openingState(o) === "NOT_SET" ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-warning/40 bg-warning/5 px-3 py-2 text-xs"
                 data-testid="opening-not-set">
              <span>
                <strong className="font-semibold">Opening count not entered.</strong>{" "}
                {o.opening_required > 0
                  ? `${o.opening_required} of ${o.tracked_products} products need an opening count. `
                  : ""}
                This store&apos;s stock is unknown, not zero. Record physical opening counts
                {isAdmin ? ", or declare that the store started at zero (it is shown as a zero start, never as counted)" : ""}.
                Receipts are still recorded.
              </span>
              {isAdmin ? (
                <Button variant="outline" size="sm" className="ml-auto h-7" onClick={() => setZeroStart(true)}>
                  Start at zero
                </Button>
              ) : null}
            </div>
          ) : null}
          {openingState(o) === "COUNTED" ? (
            <p className="text-xs text-muted-foreground" data-testid="opening-counted-note">
              Opening counted for {o.opening_counted_products ?? 0} product{o.opening_counted_products === 1 ? "" : "s"}
              {o.opening_last_counted_on ? `, latest ${formatDate(o.opening_last_counted_on)}` : ""}
              {o.opening_last_counted_by ? ` by ${o.opening_last_counted_by}` : ""}.
            </p>
          ) : null}
          {o.baseline ? (
            <p className="text-xs text-muted-foreground" data-testid="baseline-note">
              Zero start (declared, not counted) from {formatDate(o.baseline.baseline_date)}
              {o.baseline.basis === "EARLIEST_POSTED_RECEIPT" ? " (the store's first received invoice)" : ""}: all{" "}
              {o.tracked_products} product{o.tracked_products === 1 ? "" : "s"} without their own opening count start at an
              explicit zero there, and every approved receipt counts once.
            </p>
          ) : null}
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

      {zeroStart && <ZeroStartDialog storeId={storeId} storeLabel={storeLabel} onClose={() => setZeroStart(false)} />}
      {counting && <OpeningCountDialog storeId={storeId} storeLabel={storeLabel} onClose={() => setCounting(false)} />}
    </div>
  );
}

/**
 * A product's reviewed units per case with where it came from. Reference only: no inventory figure uses it.
 * A held value (conflicting or ambiguous) is not shown as a number; it needs a decision in Product Master.
 */
function UnitsPerCase({ value }: { value: ResolvedValue | undefined }) {
  if (!value || value.status === "MISSING") return <span className="text-muted-foreground" title="No approved source">—</span>;
  if (value.status === "HELD") {
    return <StatusPill size="xs" tone="warning" label="Needs review" meaning={value.notes.join(" · ") || "Held for review."} />;
  }
  const source = UNITS_SOURCE[value.source ?? ""] ?? value.source ?? "";
  return (
    <span title={[source, value.scope, value.notes.join(" · ")].filter(Boolean).join(" — ")}>
      {value.value}
      <div className="text-xs text-muted-foreground">{value.approval === "APPROVED" ? (value.scope?.startsWith("All") ? "shared" : "store") : "store mapping"}</div>
    </span>
  );
}

const ITEM_SORTS: { value: InventoryItemSort; label: string }[] = [
  { value: "code", label: "Item code" },
  { value: "name", label: "Name A–Z" },
  { value: "on_hand_asc", label: "Lowest on hand" },
  { value: "on_hand_desc", label: "Highest on hand" },
  { value: "received_desc", label: "Most received" },
  { value: "recent", label: "Latest movement" },
];

function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function ProductsTab({ storeId }: { storeId: string }) {
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const [status, setStatus] = useState<"ALL" | InventoryItemStatus>("ALL");
  const [sort, setSort] = useState<InventoryItemSort>("code");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<InventoryItemRow | null>(null);
  useEffect(() => setPage(1), [search, status, sort]);
  const params = useMemo(() => ({
    search: search || undefined, status: status === "ALL" ? undefined : status,
    sort: sort === "code" ? undefined : sort, page, page_size: ITEM_PAGE_SIZE,
  }), [search, status, sort, page]);
  const query = useInventoryItems(storeId, params);

  return (
    <div className="space-y-3">
      <FilterBar summary={query.data ? `${query.data.total} product${query.data.total === 1 ? "" : "s"}` : null}>
        <SearchInput value={searchInput} onChange={setSearchInput} placeholder="Search product or item code" className="w-72 max-md:w-full" />
        <Select value={status} onValueChange={(v) => setStatus(v as typeof status)}>
          <SelectTrigger className={cn("h-8 w-52 text-[0.8rem]", status !== "ALL" && "border-primary/40 bg-accent/40")} aria-label="Stock status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All products</SelectItem>
            <SelectItem value="OPENING_STOCK_REQUIRED">Opening stock required</SelectItem>
            <SelectItem value="OUT_OF_STOCK">Out of stock</SelectItem>
            <SelectItem value="IN_STOCK">In stock</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(v) => setSort(v as InventoryItemSort)}>
          <SelectTrigger className="h-8 w-48 text-[0.8rem]" aria-label="Sort products">
            <ArrowDownUp className="size-3.5 text-muted-foreground" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ITEM_SORTS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
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
                <TableHead className="text-right">Units / case</TableHead>
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
                    <TableCell className="text-right tabular-nums" data-testid="units-per-case">
                      <UnitsPerCase value={row.reference?.units_per_case} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {row.opening_quantity !== null ? (
                        <>{formatUnits(row.opening_quantity)}<div className="text-xs text-muted-foreground">
                          {row.opening_source === "STORE_BASELINE" ? `store start ${formatDate(row.opening_date)}` : formatDate(row.opening_date)}
                        </div></>
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

const RECEIPT_FILTERS: { value: "ALL" | StoreInvoiceReceiptFilter; label: string }[] = [
  { value: "ALL", label: "All invoices" },
  { value: "awaiting", label: "Awaiting approval" },
  { value: "received", label: "Received" },
  { value: "not_received", label: "Not received" },
];

function InvoicesTab({ storeId }: { storeId: string }) {
  const [page, setPage] = useState(1);
  const [receiving, setReceiving] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const [receipt, setReceipt] = useState<"ALL" | StoreInvoiceReceiptFilter>("ALL");
  useEffect(() => setPage(1), [search, receipt]);
  const query = useStoreInvoices(storeId, {
    search: search || undefined, receipt: receipt === "ALL" ? undefined : receipt, page, page_size: INVOICE_PAGE_SIZE,
  });
  const filtered = Boolean(search) || receipt !== "ALL";

  return (
    <div className="space-y-3">
      <FilterBar summary={query.data ? `${query.data.total} invoice${query.data.total === 1 ? "" : "s"}` : null}>
        <SearchInput value={searchInput} onChange={setSearchInput} placeholder="Search invoice number or vendor" className="w-72 max-md:w-full" />
        <Select value={receipt} onValueChange={(v) => setReceipt(v as typeof receipt)}>
          <SelectTrigger className={cn("h-8 w-52 text-[0.8rem]", receipt !== "ALL" && "border-primary/40 bg-accent/40")} aria-label="Receipt state">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {RECEIPT_FILTERS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
          </SelectContent>
        </Select>
      </FilterBar>
      {query.isLoading ? (
        <TableSkeleton rows={6} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : !query.data?.items.length ? (
        <EmptyState icon={PackageCheck} title={filtered ? "No invoice matches" : "No invoices"}
                    description={filtered ? "No invoice matches these filters." : "No invoice has been processed for this store."} />
      ) : (
        <StoreInvoiceTable rows={query.data.items} page={page} total={query.data.total} onPageChange={setPage}
                           onReceive={setReceiving} />
      )}
      {receiving && <ReceiptDialog invoiceId={receiving} onClose={() => setReceiving(null)} />}
    </div>
  );
}

function StoreInvoiceTable({ rows, page, total, onPageChange, onReceive }: {
  rows: StoreInvoiceRow[]; page: number; total: number; onPageChange: (page: number) => void; onReceive: (id: string) => void;
}) {
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
          {rows.map((row) => (
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
                        onClick={() => onReceive(row.invoice_id)}>
                  {receiptAction(row).label}
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Pagination page={page} pageSize={INVOICE_PAGE_SIZE} total={total} onPageChange={onPageChange} />
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
