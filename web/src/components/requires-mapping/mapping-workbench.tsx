import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import type { MappingQueueRow } from "@/api/types";
import { Evidence } from "@/components/invoice/case-mapping-card";
import { StoreChip } from "@/components/shared/store-chip";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfirmCaseMappings, useInvoice } from "@/hooks/use-api";
import { parseUnits } from "@/lib/proposals";

/**
 * Work a mapping directly from the Requires Mapping queue, without
 * navigating away to the invoice that happened to raise it.
 *
 * Deliberately reuses, rather than duplicates:
 *   - useConfirmCaseMappings / POST /invoices/{id}/case-mappings — the
 *     SAME governed submission path CaseMappingCard uses. This writes a
 *     PENDING proposal only; product_case_mappings is still written
 *     exclusively by proposal_service.approve() on Master Data Review.
 *   - useInvoice — the SAME invoice-detail read CaseMappingCard uses,
 *     so the evidence shown here (store catalogue match, suggested
 *     value, pending state) is computed by the one existing service
 *     (case_mapping_service.build_case_mapping_status), never
 *     recomputed here.
 *   - <Evidence/> — the exact evidence phrasing CaseMappingCard renders
 *     per row, so the two entry points can never describe the same
 *     value differently.
 *
 * One of the affected invoices is used as the submission's evidence
 * anchor (occurrences[0]) — the mapping itself is still one proposal
 * for the (store, UPC) pair, never one per invoice.
 */
export function MappingWorkbench({
  row,
  open,
  onOpenChange,
}: {
  row: MappingQueueRow | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const primaryInvoiceId = row?.occurrences[0]?.invoice_id;
  const invoice = useInvoice(open ? primaryInvoiceId : undefined);
  const confirm = useConfirmCaseMappings(primaryInvoiceId);
  const [draft, setDraft] = useState("");
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (open && row) {
      setDraft(row.pending_value !== null ? String(row.pending_value) : "");
      setTouched(false);
    }
  }, [open, row]);

  if (!row) return null;

  const evidenceRow = invoice.data?.case_mappings.find((r) => r.item_code === row.item_code) ?? null;
  const uniqueInvoices = Array.from(new Map(row.occurrences.map((o) => [o.invoice_id, o])).values());
  const primary = uniqueInvoices[0];
  const shown = uniqueInvoices.slice(0, 3);
  const overflow = row.invoice_count - shown.length;

  const units = parseUnits(draft);
  const invalid = touched && draft.trim() !== "" && units === null;

  const submit = () => {
    if (units === null || !primaryInvoiceId) {
      setTouched(true);
      return;
    }
    confirm.mutate(
      [{ item_code: row.item_code, units_per_case: units, description: row.description }],
      {
        onSuccess: (result) => {
          onOpenChange(false);
          toast.success(
            `Submitted ${result.saved} value${result.saved === 1 ? "" : "s"} for approval — ` +
              "review it on Master Data Review.",
          );
        },
        onError: (error) =>
          toast.error(error instanceof Error ? error.message : "Failed to submit the proposal."),
      },
    );
  };

  return (
    <AlertDialog open={open} onOpenChange={(next) => !next && !confirm.isPending && onOpenChange(false)}>
      <AlertDialogContent className="sm:max-w-lg" data-testid="mapping-workbench">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex flex-wrap items-center gap-2">
            Map {row.description ?? row.item_code}
            <StoreChip store={row.store} link={false} compact />
          </AlertDialogTitle>
          <AlertDialogDescription>
            Submits a units-per-case value for data-team approval — the same governed workflow as
            confirming from the invoice itself. This does not write master data directly.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-3 text-[0.82rem]">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
            <div>
              <div className="t-label">UPC</div>
              <div className="t-mono font-medium">{row.item_code}</div>
            </div>
            <div>
              <div className="t-label">Affected invoices ({row.invoice_count})</div>
              <div className="flex flex-wrap items-center gap-1.5">
                {shown.map((occurrence) => (
                  <Link
                    key={occurrence.invoice_id}
                    to={`/invoices/${occurrence.invoice_id}`}
                    className="rounded-md bg-accent px-1.5 py-0.5 text-[0.72rem] font-medium text-accent-foreground hover:underline"
                    data-testid="workbench-invoice-link"
                  >
                    {occurrence.invoice_number || occurrence.invoice_id.slice(0, 8)}
                  </Link>
                ))}
                {overflow > 0 ? <span className="t-meta">+{overflow} more</span> : null}
              </div>
            </div>
          </div>

          {primary ? (
            <div className="rounded-md border bg-muted/30 p-2.5">
              <div className="t-label mb-1">Invoice evidence</div>
              <div className="text-[0.78rem] text-muted-foreground">
                qty {primary.quantity}
                {primary.unit_price !== null ? ` @ $${primary.unit_price.toFixed(2)}` : ""}
                {primary.pack_size ? ` · pack “${primary.pack_size}”` : ""}
              </div>
            </div>
          ) : null}

          <div className="rounded-md border p-2.5">
            <div className="t-label mb-1">Store catalogue / suggestion</div>
            {invoice.isPending ? (
              <Skeleton className="h-4 w-40" />
            ) : evidenceRow ? (
              <Evidence row={evidenceRow} />
            ) : (
              <span className="text-muted-foreground">no match</span>
            )}
          </div>

          {row.pending_value !== null ? (
            <div
              className="rounded-md border border-warning/40 bg-warning/10 p-2.5"
              data-testid="workbench-pending"
            >
              <div className="t-label mb-1 text-warning">Pending proposal</div>
              <div className="text-[0.8rem]">
                Units/Case: <span className="font-semibold">{row.pending_value}</span>
                {row.pending_proposed_by ? (
                  <>
                    {" "}· Proposed by <span className="font-mono">{row.pending_proposed_by}</span>
                  </>
                ) : null}
                {" "}· Status: PENDING
              </div>
            </div>
          ) : null}

          <div className="space-y-1">
            <label htmlFor="workbench-units" className="text-[0.78rem] font-medium">
              Units per Case
            </label>
            <Input
              id="workbench-units"
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                setTouched(true);
              }}
              onBlur={() => setTouched(true)}
              inputMode="numeric"
              placeholder="e.g. 12"
              className="w-32 font-mono tabular-nums"
              aria-invalid={invalid}
              disabled={confirm.isPending}
              autoFocus
            />
            {invalid ? (
              <p className="text-[0.72rem] text-danger" data-testid="workbench-error">
                Enter a whole number from 1 to 9999.
              </p>
            ) : null}
          </div>

          <Link
            to={`/data-review/products/${encodeURIComponent(row.item_code)}?store=${row.store.id}`}
            className="text-[0.76rem] text-muted-foreground hover:underline"
          >
            View full history for this product
          </Link>
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={confirm.isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: "default" })}
            disabled={units === null || confirm.isPending}
            onClick={(event) => {
              event.preventDefault();
              submit();
            }}
            data-testid="workbench-submit"
          >
            {confirm.isPending ? "Submitting…" : "Submit for Approval"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
