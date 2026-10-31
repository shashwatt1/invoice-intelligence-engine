import { CalendarDays, Clock, TriangleAlert, Truck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { InvoiceDetail } from "@/api/types";
import { VendorPicker, type VendorChoice } from "@/components/processing/vendor-picker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useChooseInvoiceVendor, useSetDeliveryDate } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { formatPlainDate } from "@/lib/format";
import { todayIso } from "@/lib/inventory";
import { plainDate } from "@/lib/vendors";

/** Why a change was refused, in the server's own words (a POSTED receipt, a future date, ...). */
function why(error: unknown): string {
  if (error instanceof ApiError) return error.userMessage;
  return error instanceof Error ? error.message : "Not changed.";
}

/**
 * The two things a person declares about an invoice rather than reading off the document: the vendor it belongs to and
 * the day its goods arrived. Both are asked at upload; whoever missed them, and a manager correcting them, sets them
 * here. Mark Retailed waits for both. They lock once a receipt is POSTED (reverse the receipt first).
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

  const typed = dateText.trim() ? plainDate(dateText) : null;
  const waiting = detail.vendor_source === "requested";
  const vendorName = waiting
    ? (detail.vendor_request?.name ?? "a requested vendor")
    : (detail.vendor?.display_name ?? detail.vendor?.name ?? null);
  const chosenByPerson = detail.vendor_source === "operator";

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
    <section className="surface space-y-3 px-5 py-4" aria-label="Vendor and delivery date" data-testid="declarations">
      <div className="grid grid-cols-2 gap-x-8 gap-y-3 max-md:grid-cols-1">
        <div>
          <div className="t-eyebrow flex items-center gap-1.5"><Truck className="size-3.5" aria-hidden /> Vendor</div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.85rem]">
            {waiting ? <Clock className="text-warning size-4" aria-hidden /> : null}
            <span className="font-medium" data-testid="declared-vendor">{vendorName ?? "Not chosen"}</span>
            <span className="text-[0.72rem] text-muted-foreground" data-testid="vendor-source">
              {waiting
                ? "waiting for a manager or administrator to approve it"
                : chosenByPerson
                  ? "chosen from the vendor list"
                  : "read from the document, not yet chosen from the list"}
            </span>
            {canEdit && !changingVendor ? (
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
            {canEdit && !editingDate ? (
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
