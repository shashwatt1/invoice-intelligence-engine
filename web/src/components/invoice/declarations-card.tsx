import { CalendarDays, Clock, Lock, Store, TriangleAlert, Truck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { InvoiceDetail } from "@/api/types";
import { VendorPicker, type VendorChoice } from "@/components/processing/vendor-picker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StoreChip } from "@/components/shared/store-chip";
import { useChangeInvoiceStore, useChooseInvoiceVendor, useSetDeliveryDate, useStores } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { formatPlainDate } from "@/lib/format";
import { todayIso } from "@/lib/inventory";
import { physicalStores } from "@/lib/stores";
import { plainDate } from "@/lib/vendors";

/** Why a change was refused, in the server's own words (a POSTED receipt, a future date, ...). */
function why(error: unknown): string {
  if (error instanceof ApiError) return error.userMessage;
  return error instanceof Error ? error.message : "Not changed.";
}

/**
 * What a person declares about an invoice rather than reads off the document: the store it was received at, the vendor it
 * belongs to and the day its goods arrived. The vendor and the day are asked at upload; whoever missed them, and a manager
 * correcting any of the three, sets them here. All three stay changeable until the invoice is marked Retailed (the person's
 * attestation that it is right); after that, reopen the mark first. Mark Retailed waits for the vendor and the day. They also
 * lock while a receipt is POSTED (reverse the receipt first).
 *
 * A vendor the document prints differently from the one chosen is shown here in plain words, for every role: the choice
 * stands, a person decides which is right.
 */
export function DeclarationsCard({ detail }: { detail: InvoiceDetail }) {
  const { hasRole } = useAuth();
  const canEdit = hasRole("MANAGER");
  const setDate = useSetDeliveryDate(detail.invoice_id);
  const chooseVendor = useChooseInvoiceVendor(detail.invoice_id);
  const [editingDate, setEditingDate] = useState(false);
  const [dateText, setDateText] = useState("");
  const [changingVendor, setChangingVendor] = useState(false);
  const [vendorPick, setVendorPick] = useState<VendorChoice | null>(null);
  const changeStore = useChangeInvoiceStore(detail.invoice_id);
  const stores = useStores();
  const [changingStore, setChangingStore] = useState(false);
  const [newStore, setNewStore] = useState("");
  // Marked Retailed (even if it needs re-verifying): the declarations are fixed until the mark is reopened.
  const retailed = detail.completion?.status === "retailed" || detail.completion?.status === "retailed_stale";
  const editable = canEdit && !retailed;
  const otherStores = physicalStores(stores.data).filter((s) => s.id !== detail.store?.id);

  const typed = dateText.trim() ? plainDate(dateText) : null;
  const waiting = detail.vendor_source === "requested";
  const rejected = !waiting && !detail.vendor && detail.vendor_request?.status === "REJECTED";
  const vendorName = waiting
    ? (detail.vendor_request?.name ?? "a requested vendor")
    : (detail.vendor?.display_name ?? detail.vendor?.name ?? null);
  const chosenByPerson = detail.vendor_source === "operator";
  // A vendor the invoice never had (a rejected request, an older invoice) can still be filled in after Retailed: only the
  // missing pointer is set. Changing a vendor it already has needs the Retailed mark reopened.
  const vendorEditable = canEdit && (!retailed || !detail.vendor);

  const saveDate = () => {
    if (!typed) return;
    setDate.mutate({ deliveryDate: typed }, {
      onSuccess: () => {
        setEditingDate(false);
        toast.success(`Delivery date set to ${formatPlainDate(typed)}.`);
      },
      onError: (error) => toast.error(why(error), { duration: 10_000 }),
    });
  };

  const saveStore = () => {
    if (!newStore) return;
    changeStore.mutate({ storeId: newStore }, {
      onSuccess: (moved) => {
        setChangingStore(false);
        setNewStore("");
        toast.success(`Moved to ${moved.store_label}.`);
        if (moved.export_files_for_old_store > 0) {
          toast.warning(
            `${moved.export_files_for_old_store} export file${moved.export_files_for_old_store === 1 ? " was" : "s were"} ` +
              `already generated for ${moved.previous_store_label ?? "the old store"}. Generate it again for the new store.`,
            { duration: 15_000 },
          );
        }
      },
      onError: (error) => toast.error(why(error), { duration: 12_000 }),
    });
  };

  const saveVendor = (choice: VendorChoice | null) => {
    setVendorPick(choice);
    if (!choice) return;
    chooseVendor.mutate(
      choice.kind === "vendor" ? { vendorId: choice.id } : { vendorRequestId: choice.id },
      {
        onSuccess: () => {
          setChangingVendor(false);
          setVendorPick(null);
          toast.success(choice.kind === "vendor" ? `Vendor set to ${choice.name}.` : `Waiting for ${choice.name} to be approved.`);
        },
        onError: (error) => {
          setVendorPick(null);
          toast.error(why(error), { duration: 10_000 });
        },
      },
    );
  };

  return (
    <section id="declarations" className="surface space-y-3 px-5 py-4" aria-label="Store, vendor and delivery date" data-testid="declarations">
      {retailed ? (
        <p className="flex items-center gap-2 text-[0.78rem] text-muted-foreground" data-testid="declarations-locked">
          <Lock className="size-3.5 shrink-0" aria-hidden />
          Marked Retailed{detail.completion?.retailed_by ? ` by ${detail.completion.retailed_by}` : ""}. Reopen it to change the
          store, vendor or delivery date.
        </p>
      ) : null}
      <div className="grid grid-cols-3 gap-x-8 gap-y-3 max-lg:grid-cols-2 max-md:grid-cols-1">
        {detail.store ? (
          <div>
            <div className="t-eyebrow flex items-center gap-1.5"><Store className="size-3.5" aria-hidden /> Store</div>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.85rem]">
              <span data-testid="declared-store"><StoreChip store={detail.store} link={false} /></span>
              {editable && !changingStore ? (
                <Button type="button" variant="ghost" size="sm" className="h-6 px-2" onClick={() => setChangingStore(true)}>
                  Change
                </Button>
              ) : null}
            </div>
            {changingStore ? (
              <div className="mt-2 max-w-xs space-y-2">
                <Select value={newStore} onValueChange={setNewStore}>
                  <SelectTrigger aria-label="New store"><SelectValue placeholder="Choose the correct store" /></SelectTrigger>
                  <SelectContent>
                    {otherStores.map((s) => <SelectItem key={s.id} value={s.id}>{s.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <p className="text-[0.72rem] text-muted-foreground">
                  The new store's mappings and review queue apply from now on. Proposals made from this invoice must be
                  decided first; export files already generated have to be generated again.
                </p>
                <div className="flex gap-2">
                  <Button type="button" size="sm" disabled={!newStore || changeStore.isPending} onClick={saveStore}>Move invoice</Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => { setChangingStore(false); setNewStore(""); }}>Cancel</Button>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
        <div>
          <div className="t-eyebrow flex items-center gap-1.5"><Truck className="size-3.5" aria-hidden /> Vendor</div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.85rem]">
            {waiting ? <Clock className="text-warning size-4" aria-hidden /> : null}
            <span className="font-medium" data-testid="declared-vendor">{vendorName ?? "Not chosen"}</span>
            <span className="text-[0.72rem] text-muted-foreground" data-testid="vendor-source">
              {waiting
                ? "waiting for an administrator to approve it; the invoice can be finished meanwhile"
                : rejected
                  ? `the request was rejected${detail.vendor_request?.name ? ` (${detail.vendor_request.name})` : ""}: choose a vendor from the list`
                  : chosenByPerson
                  ? "chosen from the vendor list"
                  : "read from the document, not yet chosen from the list"}
            </span>
            {vendorEditable && !changingVendor ? (
              <Button type="button" variant="ghost" size="sm" className="h-6 px-2" onClick={() => setChangingVendor(true)}>
                {chosenByPerson ? "Change" : "Choose"}
              </Button>
            ) : null}
          </div>
          {changingVendor ? (
            <div className="mt-2 max-w-md space-y-2">
              <VendorPicker storeId={detail.store?.id ?? null} value={vendorPick} onChange={saveVendor} disabled={chooseVendor.isPending} />
              <Button type="button" variant="ghost" size="sm" onClick={() => { setChangingVendor(false); setVendorPick(null); }}>
                Cancel
              </Button>
            </div>
          ) : null}
        </div>

        <div>
          <div className="t-eyebrow flex items-center gap-1.5"><CalendarDays className="size-3.5" aria-hidden /> Delivery date</div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.85rem]">
            <span className="font-medium" data-testid="declared-date">
              {detail.delivery_date ? formatPlainDate(detail.delivery_date) : "Not set"}
            </span>
            <span className="text-[0.72rem] text-muted-foreground">the day the goods arrived</span>
            {editable && !editingDate ? (
              <Button
                type="button" variant="ghost" size="sm" className="h-6 px-2"
                onClick={() => { setDateText(detail.delivery_date ?? ""); setEditingDate(true); }}
              >
                {detail.delivery_date ? "Change" : "Set"}
              </Button>
            ) : null}
          </div>
          {editingDate ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Input
                type="date" className="w-[170px]" aria-label="Delivery date" value={dateText} max={todayIso()} min="2000-01-01"
                onChange={(event) => setDateText(event.target.value)}
                aria-invalid={Boolean(dateText.trim()) && !typed}
              />
              <Button type="button" size="sm" disabled={!typed || setDate.isPending} onClick={saveDate}>Save</Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setEditingDate(false)}>Cancel</Button>
            </div>
          ) : null}
        </div>
      </div>

      {detail.vendor_mismatch ? (
        <p className="bg-warning-soft/60 flex items-start gap-2 rounded-md px-3 py-2 text-[0.8rem]" role="alert" data-testid="vendor-mismatch">
          <TriangleAlert className="text-warning mt-0.5 size-4 shrink-0" aria-hidden />
          <span>{detail.vendor_mismatch}</span>
        </p>
      ) : null}

      {detail.declarations_missing ? (
        <p className="text-[0.78rem] text-muted-foreground" data-testid="declarations-missing">{detail.declarations_missing}</p>
      ) : null}
    </section>
  );
}
