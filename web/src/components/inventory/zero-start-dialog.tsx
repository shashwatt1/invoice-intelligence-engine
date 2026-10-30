import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
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
import { useStartStoreAtZero, useZeroStartPreview } from "@/hooks/use-api";
import { formatDate } from "@/lib/format";
import { formatUnits } from "@/lib/inventory";

/**
 * Declares that a store starts tracking at zero — an administrator's statement, NOT a physical count. Before
 * anything is written the dialog shows what it will do: the date products start at zero, how many products
 * get an explicit zero opening (and stop needing an opening count), and the available units afterwards. A
 * store that already has receipts or movements may have held stock before them, so it also needs an explicit
 * acknowledgement; counting it instead is the other choice. It writes one baseline (who, when, why) and
 * nothing else: no movement, no receipt, and no change to a product that has its own count. Asking again for
 * a store that already has one changes nothing.
 *
 * The start date is the server's call. The dialog never sends the browser's own "today" (a calendar that can be
 * a day ahead of the server's UTC date, which the server rightly refuses as the future): it sends a date only
 * when the person picks one, and otherwise the server applies its default. A failed preview shows the server's
 * own message and request id, and never lets the start proceed.
 */
function previewFailure(error: unknown): { message: string; requestId: string | null; code: string | null } {
  if (error instanceof ApiError) {
    return { message: error.message, requestId: error.requestId, code: error.errorCode };
  }
  return { message: error instanceof Error ? error.message : "The request failed.", requestId: null, code: null };
}

export function ZeroStartDialog({ storeId, storeLabel, onClose }: { storeId: string; storeLabel: string; onClose: () => void }) {
  const start = useStartStoreAtZero();
  const [date, setDate] = useState("");                    // "" = not chosen: the server applies its own default
  const [serverToday, setServerToday] = useState<string | undefined>();
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const preview = useZeroStartPreview(storeId, date || undefined);
  const p = preview.data;
  const history = p?.has_history === true;
  // Never started from a failed or still-loading preview: what the person confirms is exactly what was checked.
  const checked = Boolean(p) && !preview.isError && !preview.isFetching;
  const ready = checked && !p?.already_started && reason.trim() !== "" && confirm
    && (!history || acknowledge) && !start.isPending;
  const failure = preview.isError ? previewFailure(preview.error) : null;

  // Without a chosen date, a store with no history starts on the server's "today": the latest date it will accept.
  useEffect(() => {
    if (!date && p && !p.has_history && !p.already_started) setServerToday(p.effective_date);
  }, [date, p]);

  const submit = () => start.mutate(
    { storeId, body: { reason: reason.trim(), ...(date ? { initialization_date: date } : {}), confirm,
                       acknowledge_prior_stock: acknowledge } },
    {
      onSuccess: (result) => {
        toast.success(result.created
          ? `${storeLabel} now starts at zero: ${result.products_initialized} product${result.products_initialized === 1 ? "" : "s"} initialized.`
          : `${storeLabel} already had a zero start; nothing changed.`);
        onClose();
      },
      onError: (error) => toast.error(error instanceof ApiError ? error.userMessage : "Nothing was recorded."),
    },
  );

  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-h-[88vh] max-w-lg overflow-y-auto" data-testid="zero-start-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>Start {storeLabel} at zero</AlertDialogTitle>
          <AlertDialogDescription>
            This is a declaration, not a physical count. Products without their own opening count start at an
            explicit zero, every approved receipt is added once, and counts, receipts and movements already
            recorded are not changed.
          </AlertDialogDescription>
        </AlertDialogHeader>

        {preview.isLoading ? <p className="t-meta">Checking what this would do…</p> : null}
        {failure ? (
          <div role="alert" className="space-y-1 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm" data-testid="zero-start-error">
            <p className="font-medium text-destructive">The effect could not be checked, so nothing can be started.</p>
            <p data-testid="zero-start-error-message">{failure.message}</p>
            {failure.code ? <p className="t-meta">Error {failure.code}</p> : null}
            {failure.requestId ? <p className="t-meta">Request ID <span className="font-mono" data-testid="zero-start-error-request">{failure.requestId}</span></p> : null}
          </div>
        ) : null}
        {p ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-md border bg-muted/30 p-3 text-sm" data-testid="zero-start-effect">
            <dt className="text-muted-foreground">Start date</dt>
            <dd data-testid="zero-start-date">{formatDate(p.effective_date)}{p.basis === "EARLIEST_POSTED_RECEIPT" ? " (first received invoice)" : ""}</dd>
            <dt className="text-muted-foreground">Products at an explicit zero</dt>
            <dd data-testid="zero-start-products">{p.products_to_initialize}</dd>
            {p.catalogue_only_products > 0 ? (
              <>
                <dt className="text-muted-foreground">…of which listed by the store with no stock movement yet</dt>
                <dd data-testid="zero-start-catalogue">{p.catalogue_only_products}</dd>
              </>
            ) : null}
            <dt className="text-muted-foreground">Products with their own count (kept)</dt>
            <dd>{p.counted_products}</dd>
            <dt className="text-muted-foreground">Still needing an opening count after</dt>
            <dd>{p.opening_required_after}</dd>
            <dt className="text-muted-foreground">Available units after</dt>
            <dd data-testid="zero-start-available">{formatUnits(p.available_units_after)}</dd>
          </dl>
        ) : null}

        {p?.already_started ? <p className="text-sm">This store already has a zero start. Nothing can be changed here.</p> : null}

        {p && !p.already_started ? (
          <div className="space-y-3 text-sm">
            {history ? (
              <div className="rounded-md border border-warning/40 bg-warning/5 p-3" data-testid="zero-start-history">
                <p>
                  This store already has {p.posted_receipts} received invoice{p.posted_receipts === 1 ? "" : "s"} and {p.products} product
                  {p.products === 1 ? "" : "s"} with movements. It may have held stock before
                  {p.first_receipt_date ? ` ${formatDate(p.first_receipt_date)}` : " its first movement"}. If so, record physical
                  opening counts instead.
                </p>
                <label className="mt-2 flex items-start gap-2">
                  <Checkbox checked={acknowledge} onCheckedChange={(v) => setAcknowledge(v === true)} disabled={start.isPending}
                            aria-label="Confirm no stock before the first receipt" />
                  <span>I confirm this store held no stock of these products before that date.</span>
                </label>
              </div>
            ) : (
              <label className="block">
                <span className="mb-1 block text-xs font-medium">Start date <span className="text-muted-foreground">(optional)</span></span>
                <Input type="date" value={date} max={serverToday} onChange={(e) => setDate(e.target.value)} disabled={start.isPending} />
                {!date ? (
                  <span className="t-meta mt-1 block" data-testid="zero-start-default-date">
                    Without a date it starts on {formatDate(p.effective_date)}, the server&apos;s date today.
                  </span>
                ) : null}
              </label>
            )}
            <label className="block">
              <span className="mb-1 block text-xs font-medium">Why <span className="text-muted-foreground">(required, kept for good)</span></span>
              <Input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={400} disabled={start.isPending}
                     placeholder="e.g. new store, opened with no stock on hand" />
            </label>
            <label className="flex items-start gap-2">
              <Checkbox checked={confirm} onCheckedChange={(v) => setConfirm(v === true)} disabled={start.isPending}
                        aria-label="Confirm the zero start" />
              <span>I understand this records a zero start from {formatDate(p.effective_date)}; it is not a physical count.</span>
            </label>
          </div>
        ) : null}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" size="sm" onClick={onClose} disabled={start.isPending}>Cancel</Button>
          <Button size="sm" onClick={submit} disabled={!ready} data-testid="zero-start-confirm">
            {start.isPending ? "Saving…" : "Start at zero"}
          </Button>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
