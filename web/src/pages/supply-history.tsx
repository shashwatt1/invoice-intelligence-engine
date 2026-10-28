import { ArrowLeft, Boxes, CalendarRange, Store, Truck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { SupplyParams, SupplyRow } from "@/api/types";
import { ReceiptDialog } from "@/components/inventory/receipt-dialog";
import { PageHeader } from "@/components/layout/page-header";
import { ActiveFilters, FilterBar } from "@/components/shared/filter-bar";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { StatusPill } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useInventoryStatus, useSupply, useSupplyOptions } from "@/hooks/use-api";
import { formatDate } from "@/lib/format";
import { RECEIPT_STATUS, receivableReason } from "@/lib/inventory";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;
const ALL = "ALL";

/** How the delivery date narrows the list. "on"/"between" use the posted receipt's date. */
type DeliveryMode = "any" | "on" | "between" | "missing";
const DELIVERY_MODES: { value: DeliveryMode; label: string }[] = [
  { value: "any", label: "Any delivery date" },
  { value: "on", label: "Delivered on…" },
  { value: "between", label: "Delivered between…" },
  { value: "missing", label: "Not received yet" },
];

/**
 * Supply history: recorded deliveries (invoices and their receipts) across the stores this
 * account holds a grant for, filtered by vendor, store and delivery date — on the server, so
 * results are complete however many records there are.
 *
 * The delivery date is the posted receipt's "Received on" date. It is never the invoice date
 * or the processing time; invoices without a posted receipt have no delivery date and are
 * only found with "Not received yet". This is a history of supplies, not stock on hand.
 */
export function SupplyHistoryPage() {
  const status = useInventoryStatus();
  const enabled = status.data?.enabled === true;
  const options = useSupplyOptions({ enabled });
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [notice, setNotice] = useState<string | null>(null);
  const [receiving, setReceiving] = useState<string | null>(null);

  const vendors = useMemo(() => options.data?.vendors ?? [], [options.data]);
  const stores = useMemo(() => options.data?.stores ?? [], [options.data]);
  const vendorId = params.get("vendor") ?? "";
  const storeId = params.get("store") ?? "";
  const mode = (DELIVERY_MODES.find((m) => m.value === params.get("delivery"))?.value ?? "any") as DeliveryMode;
  const from = params.get("from") ?? "";
  const to = params.get("to") ?? "";
  const vendor = vendors.find((v) => v.id === vendorId) ?? null;
  const store = stores.find((s) => s.id === storeId) ?? null;
  // With a vendor chosen, offer only the stores it supplies (as recorded by its invoices).
  const storeChoices = vendor ? stores.filter((s) => vendor.store_ids.includes(s.id)) : stores;

  const update = (changes: Record<string, string | null>) => setParams((prev) => {
    const next = new URLSearchParams(prev);
    for (const [k, v] of Object.entries(changes)) {
      if (v) next.set(k, v); else next.delete(k);
    }
    return next;
  }, { replace: true });

  const chooseVendor = (id: string) => {
    const next = vendors.find((v) => v.id === id) ?? null;
    if (next && store && !next.store_ids.includes(store.id)) {
      update({ vendor: id, store: null });
      setNotice(`${store.label} has no invoices from ${next.label}, so the store filter was cleared.`);
    } else {
      update({ vendor: id || null });
      setNotice(null);
    }
  };
  const chooseStore = (id: string) => { update({ store: id || null }); setNotice(null); };
  const chooseMode = (m: DeliveryMode) => update({
    delivery: m === "any" ? null : m,
    from: m === "on" || m === "between" ? from : null,
    to: m === "between" ? to : null,
  });
  const resetAll = () => { setParams(new URLSearchParams(), { replace: true }); setNotice(null); };

  const rangeInvalid = mode === "between" && Boolean(from && to) && from > to;
  const dateIncomplete = (mode === "on" && !from) || (mode === "between" && !from && !to);
  const query: SupplyParams = {
    vendor_id: vendor?.id, store_id: store?.id, page, page_size: PAGE_SIZE,
    ...(mode === "on" && from ? { delivered_from: from, delivered_to: from } : {}),
    ...(mode === "between" ? { delivered_from: from || undefined, delivered_to: to || undefined } : {}),
    ...(mode === "missing" ? { delivery: "missing" as const } : {}),
  };
  const key = JSON.stringify({ ...query, page: 0 });
  useEffect(() => setPage(1), [key]);
  const supply = useSupply(query, { enabled: enabled && options.isSuccess && !rangeInvalid });

  const chips = [
    ...(vendor ? [{ key: "vendor", label: `Vendor: ${vendor.label}`, onRemove: () => chooseVendor("") }] : []),
    ...(store ? [{ key: "store", label: `Store: ${store.label}`, onRemove: () => chooseStore("") }] : []),
    ...(mode === "on" && from ? [{ key: "date", label: `Delivered ${formatDate(from)}`, onRemove: () => chooseMode("any") }] : []),
    ...(mode === "between" && (from || to) ? [{
      key: "date", label: `Delivered ${from ? formatDate(from) : "…"} – ${to ? formatDate(to) : "…"}`, onRemove: () => chooseMode("any"),
    }] : []),
    ...(mode === "missing" ? [{ key: "date", label: "Not received yet", onRemove: () => chooseMode("any") }] : []),
  ];

  if (status.isLoading || (enabled && options.isLoading)) return <><Header /><TableSkeleton rows={6} /></>;
  if (status.isError) return <><Header /><ErrorState error={status.error} onRetry={() => void status.refetch()} /></>;
  if (!enabled) {
    return <><Header /><EmptyState icon={Boxes} title="Inventory is not switched on"
                                    description="Supply history is available once an administrator enables inventory." /></>;
  }
  if (options.isError) return <><Header /><ErrorState error={options.error} onRetry={() => void options.refetch()} /></>;
  if (!stores.length) {
    return <><Header /><EmptyState icon={Store} title="No stores assigned to you"
                                    description="Supply history is shown per granted store. Ask an administrator for access." /></>;
  }

  return (
    <>
      <Header />
      <FilterBar summary={supply.data ? `${supply.data.total} record${supply.data.total === 1 ? "" : "s"}` : null}>
        <Select value={vendor?.id ?? ALL} onValueChange={(v) => chooseVendor(v === ALL ? "" : v)}>
          <SelectTrigger className={cn("h-8 w-56 text-[0.8rem]", vendor && "border-primary/40 bg-accent/40")} aria-label="Vendor">
            <Truck className="size-3.5 text-muted-foreground" /><SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All vendors</SelectItem>
            {vendors.map((v) => <SelectItem key={v.id} value={v.id}>{v.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={store?.id ?? ALL} onValueChange={(v) => chooseStore(v === ALL ? "" : v)}>
          <SelectTrigger className={cn("h-8 w-56 text-[0.8rem]", store && "border-primary/40 bg-accent/40")} aria-label="Store">
            <Store className="size-3.5 text-muted-foreground" /><SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{vendor ? `All of ${vendor.label}'s stores` : "All stores"}</SelectItem>
            {storeChoices.map((s) => <SelectItem key={s.id} value={s.id}>{s.label}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={mode} onValueChange={(v) => chooseMode(v as DeliveryMode)}>
          <SelectTrigger className={cn("h-8 w-52 text-[0.8rem]", mode !== "any" && "border-primary/40 bg-accent/40")} aria-label="Delivery date">
            <CalendarRange className="size-3.5 text-muted-foreground" /><SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DELIVERY_MODES.map((m) => <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>)}
          </SelectContent>
        </Select>
        {mode === "on" || mode === "between" ? (
          <Input type="date" value={from} onChange={(e) => update({ from: e.target.value || null })}
                 className="h-8 w-40 text-[0.8rem]" aria-label={mode === "on" ? "Delivered on" : "Delivered from"} />
        ) : null}
        {mode === "between" ? (
          <Input type="date" value={to} onChange={(e) => update({ to: e.target.value || null })}
                 className="h-8 w-40 text-[0.8rem]" aria-label="Delivered to" aria-invalid={rangeInvalid} />
        ) : null}
        <ActiveFilters chips={chips} onClear={resetAll} />
      </FilterBar>

      {notice ? <p className="t-meta mb-3" role="status" data-testid="filter-notice">{notice}</p> : null}
      {rangeInvalid ? (
        <p className="mb-3 text-[0.8rem] text-danger" role="alert">The end date is before the start date. Adjust the range.</p>
      ) : null}
      {dateIncomplete ? <p className="t-meta mb-3">Choose a date to filter by delivery.</p> : null}

      {rangeInvalid ? null : supply.isError ? (
        <ErrorState error={supply.error} onRetry={() => void supply.refetch()} />
      ) : supply.isPending ? (
        <TableSkeleton rows={6} />
      ) : !supply.data.items.length ? (
        <EmptyState icon={Truck} title="No supply records match"
                    description={chips.length ? "Clear or change a filter." : "No invoices have been recorded for your stores yet."}
                    action={chips.length ? <Button variant="outline" size="sm" onClick={resetAll}>Reset filters</Button> : undefined} />
      ) : (
        <div className={cn("space-y-3 transition-opacity", supply.isPlaceholderData && "opacity-60")} aria-busy={supply.isPlaceholderData}>
          <Table data-testid="supply-table">
            <TableHeader>
              <TableRow>
                <TableHead>Invoice</TableHead>
                <TableHead>Vendor</TableHead>
                <TableHead>Store</TableHead>
                <TableHead>Invoice date</TableHead>
                <TableHead>Delivered on</TableHead>
                <TableHead className="text-right">Product lines</TableHead>
                <TableHead>Receipt</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {supply.data.items.map((row) => <SupplyTableRow key={row.invoice_id} row={row} onOpen={() => setReceiving(row.invoice_id)} />)}
            </TableBody>
          </Table>
          <Pagination page={page} pageSize={PAGE_SIZE} total={supply.data.total} onPageChange={setPage} />
        </div>
      )}
      {receiving && <ReceiptDialog invoiceId={receiving} onClose={() => setReceiving(null)} />}
    </>
  );
}

function Header() {
  return (
    <>
      <Button asChild variant="ghost" size="sm" className="-ml-2 mb-2">
        <Link to="/inventory"><ArrowLeft className="size-4" aria-hidden /> Store inventory</Link>
      </Button>
      <PageHeader
        title="Supply history"
        description="Recorded deliveries by vendor, store and delivery date. This is what was supplied, not what is in stock now."
      />
    </>
  );
}

function SupplyTableRow({ row, onOpen }: { row: SupplyRow; onOpen: () => void }) {
  const r = row.receipt;
  return (
    <TableRow data-testid="supply-row">
      <TableCell>
        <Link className="font-medium underline-offset-2 hover:underline" to={`/invoices/${row.invoice_id}`}>#{row.invoice_number ?? "—"}</Link>
      </TableCell>
      <TableCell className="text-sm">{row.vendor ?? "—"}</TableCell>
      <TableCell className="text-sm">{row.store.label}</TableCell>
      <TableCell className="text-sm text-muted-foreground">{formatDate(row.invoice_date)}</TableCell>
      <TableCell className="text-sm" data-testid="delivered-on">
        {row.delivered_on ? formatDate(row.delivered_on) : <span className="text-muted-foreground">Not received yet</span>}
      </TableCell>
      <TableCell className="text-right tabular-nums">{row.product_lines}</TableCell>
      <TableCell className="text-sm">
        {r ? (
          <button type="button" onClick={onOpen} className="text-left">
            <StatusPill tone={RECEIPT_STATUS[r.status].tone} label={RECEIPT_STATUS[r.status].label} meaning={RECEIPT_STATUS[r.status].meaning} />
          </button>
        ) : row.receivable_reasons.length ? (
          <span className="text-xs text-muted-foreground">{row.receivable_reasons.map(receivableReason).join("; ")}</span>
        ) : (
          <StatusPill tone="neutral" label="Not submitted" meaning="Marking the invoice Retailed submits its receipt for approval." />
        )}
      </TableCell>
    </TableRow>
  );
}
