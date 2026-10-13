import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { approveProposal, confirmCaseMappings, correctLineItem, getInvoice } from "@/api/endpoints";
import type { CaseMappingRow } from "@/api/types";
import { InvoicePrice } from "@/components/invoice/invoice-price";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/hooks/use-auth";
import { MAX_DESCRIPTION, parseUnitsInput, runLineReview, validateLineReview, type LineReviewOutcome } from "@/lib/line-review";

/**
 * Review one invoice line without leaving the mapping: see the description,
 * the invoice's own prices and the evidence; correct the product code and the
 * description (manager or admin — the existing line-correction path, every
 * change kept in the line's history with the original preserved); enter units
 * per case; then Save (a proposal awaiting Data Review) or, for a manager,
 * Save & approve. Each step is an existing governed call; the first refusal
 * stops the rest, and the outcome says exactly what happened.
 */
export function LineReviewPanel({
  invoiceId,
  row,
  evidence,
  open,
  onOpenChange,
}: {
  invoiceId: string;
  row: CaseMappingRow;
  /** The card's own evidence display for this row. */
  evidence?: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { user, hasRole } = useAuth();
  const isManager = hasRole("MANAGER");
  const queryClient = useQueryClient();
  const [code, setCode] = useState(row.product_code ?? "");
  const [description, setDescription] = useState(row.description ?? "");
  const [units, setUnits] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<LineReviewOutcome | null>(null);

  useEffect(() => {
    if (!open) return;
    setCode(row.product_code ?? "");
    setDescription(row.description ?? "");
    setUnits("");
    setNote("");
    setOutcome(null);
  }, [open, row.product_code, row.description, row.sort_order]);

  if (row.sort_order === null || row.sort_order === undefined) return null;
  const sortOrder = row.sort_order;

  const input = (approve: boolean) => ({
    invoiceId, sortOrder, storedCode: row.product_code ?? null, itemCode: row.item_code,
    storedDescription: row.description, code, description, units, approve, isManager,
    username: user?.username ?? "unknown", note,
  });
  const problems = (approve: boolean) => validateLineReview(input(approve));
  const pending = row.pending_value !== null;
  const approvedUnits = row.mapped ? row.units_per_case : null;
  const typedUnits = parseUnitsInput(units);
  const replacesApproved = approvedUnits !== null && typedUnits !== null && typedUnits !== approvedUnits;

  const submit = async (approve: boolean) => {
    if (busy) return;                               // one submission at a time
    setBusy(true);
    setOutcome(null);
    try {
      const result = await runLineReview(input(approve), {
        correctLine: correctLineItem,
        getInvoice,
        confirm: confirmCaseMappings,
        approve: approveProposal,
      });
      setOutcome(result);
      if (result.steps.length) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["invoice", invoiceId] }),
          queryClient.invalidateQueries({ queryKey: ["requires-mapping"] }),
          queryClient.invalidateQueries({ queryKey: ["proposals"] }),
        ]);
      }
      if (result.status === "done") {
        toast.success(result.message);
        onOpenChange(false);
      }
    } finally {
      setBusy(false);
    }
  };

  const saveProblems = problems(false);
  const approveProblems = problems(true);

  return (
    <AlertDialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <AlertDialogContent className="sm:max-w-xl" data-testid="line-review-panel">
        <AlertDialogHeader>
          <AlertDialogTitle>Review line {sortOrder + 1}</AlertDialogTitle>
          <AlertDialogDescription>
            Corrections are kept in the line's history. Units per case go to Data Review unless a manager approves them
            here; nothing is applied to other stores or to Product Master.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-3 text-[0.82rem]">
          <div className="rounded-md border bg-muted/30 p-2.5">
            <div className="font-medium" data-testid="line-review-description">{row.description ?? "—"}</div>
            {row.original_description ? (
              <div className="t-meta" data-testid="line-review-original">Originally read as “{row.original_description}”</div>
            ) : null}
            <InvoicePrice quantity={row.quantity} unitPrice={row.unit_price} lineTotal={row.line_total}
                          unitDiscount={row.unit_discount} unitsPerCase={typedUnits ?? approvedUnits}
                          unitsLabel={typedUnits !== null ? "as entered" : approvedUnits !== null ? "approved" : undefined} />
            {row.pack_size ? <div className="t-meta">pack “{row.pack_size}”</div> : null}
            {evidence ? <div className="mt-1 text-[0.75rem]">{evidence}</div> : null}
          </div>

          {pending ? (
            <div className="rounded-md border border-warning/40 bg-warning-soft/40 p-2 text-[0.78rem]" data-testid="line-review-pending">
              {row.pending_value} per case is already pending review.{" "}
              {row.pending_proposal_id ? (
                <Link to={`/data-review/proposals/${row.pending_proposal_id}`} className="text-primary hover:underline">
                  Open it in Data Review
                </Link>
              ) : null}
            </div>
          ) : null}
          {approvedUnits !== null ? (
            <div className="t-meta" data-testid="line-review-approved">Approved for this store: {approvedUnits} per case.</div>
          ) : null}

          <div className="grid gap-2 sm:grid-cols-2">
            <label className="space-y-1">
              <span className="t-label">Product code (UPC)</span>
              <Input value={code} onChange={(e) => setCode(e.target.value)} disabled={!isManager || busy}
                     aria-label="Product code (UPC)" className="t-mono" maxLength={64} />
              <span className="t-meta">
                EDI item code now: <span className="t-mono">{row.item_code ?? "none (no usable code)"}</span>
              </span>
            </label>
            <label className="space-y-1">
              <span className="t-label">Units per case</span>
              <Input value={units} onChange={(e) => setUnits(e.target.value)} disabled={busy || pending}
                     inputMode="numeric" aria-label="Units per case" placeholder={pending ? "pending review" : "enter"} />
              {replacesApproved ? (
                <span className="text-warning text-[0.72rem]">Differs from the approved value — Save proposes it for Data Review.</span>
              ) : null}
            </label>
          </div>
          <label className="block space-y-1">
            <span className="t-label">Description</span>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} disabled={!isManager || busy}
                   aria-label="Description" maxLength={MAX_DESCRIPTION} />
          </label>
          <label className="block space-y-1">
            <span className="t-label">Note (optional, kept with the correction and the approval)</span>
            <Input value={note} onChange={(e) => setNote(e.target.value)} disabled={busy} aria-label="Note" maxLength={500} />
          </label>
          {!isManager ? (
            <p className="t-meta">Only a manager or admin can correct the code or description, or approve.</p>
          ) : null}

          {outcome && outcome.status !== "done" ? (
            <div role="alert" data-testid="line-review-outcome"
                 className={outcome.status === "invalid" || outcome.status === "failed" ? "text-danger" : "text-warning"}>
              {outcome.steps.length ? <div>Done: {outcome.steps.join("; ")}.</div> : null}
              <div>{outcome.message}</div>
            </div>
          ) : null}
        </div>

        <AlertDialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>Cancel</Button>
          <Button variant="secondary" onClick={() => void submit(false)} disabled={busy || saveProblems.length > 0}
                  title={saveProblems.join(" ") || undefined}>
            {busy ? "Saving…" : "Save"}
          </Button>
          {isManager ? (
            <Button onClick={() => void submit(true)} disabled={busy || approveProblems.length > 0 || replacesApproved}
                    title={replacesApproved ? "An approved value is not replaced in one step" : approveProblems.join(" ") || undefined}>
              {busy ? "Saving…" : "Save & approve"}
            </Button>
          ) : null}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
