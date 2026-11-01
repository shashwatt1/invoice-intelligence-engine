import { AlertTriangle, CheckCircle2, Clock } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { InventoryReceiptSummary, ReceiptBlocker, ReceiptPreview, ReceiptSubmission } from "@/api/types";
import { ErrorState, PanelSkeleton } from "@/components/shared/states";
import { StatusPill } from "@/components/shared/status-badge";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApproveReceipt, useReceiptPreview, useRejectReceipt, useReverseReceipt } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { formatDate, formatDateTime, formatPlainDate } from "@/lib/format";
import {
  formatUnits,
  isDiscrepancy,
  lineProblem,
  type LineEntry,
  RECEIPT_STATUS,
  receivableReason,
  staleReason,
  todayIso,
} from "@/lib/inventory";

/**
 * An invoice's inventory receipt, for a MANAGER/ADMIN with a grant for its store.
 *
 * A receipt is never entered here from scratch: marking the invoice Retailed submits
 * it (a proposal; no stock moves). Here an approver reviews the submission against the
 * invoice as it is now — every change since, every blocker, and whether it is stale —
 * then approves it (accepted units prefilled with what was proposed; any difference
 * needs a reason) or rejects it with a reason. Approval posts the stock once.
 * Approving your own submission is allowed and recorded as such. A stale submission
 * cannot be approved: re-verify the invoice and mark it Retailed again. The server
 * decides and re-checks all of it.
 *
 * A posted receipt locks the invoice against correction until it is reversed (with a
 * reason); reversing compensates every movement and leaves the Retailed mark as it is.
 */
export function ReceiptDialog({ invoiceId, onClose }: { invoiceId: string; onClose: () => void }) {
  const preview = useReceiptPreview(invoiceId);
  const data = preview.data;
  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-h-[88vh] max-w-4xl overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">
            Inventory receipt {data?.invoice_number ? `· invoice #${data.invoice_number}` : ""}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {data?.store ? `${data.store.label}. ` : ""}Quantities are sellable units.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {preview.isLoading ? (
          <PanelSkeleton className="h-48" />
        ) : preview.isError ? (
          <ErrorState error={preview.error} onRetry={() => void preview.refetch()} />
        ) : data ? (
          <ReceiptBody preview={data} onClose={onClose} />
        ) : null}
      </AlertDialogContent>
    </AlertDialog>
  );
}

function historyLine(r: InventoryReceiptSummary): string {
  const submitted = r.submitted_by ? `submitted by ${r.submitted_by}${r.submitted_at ? ` on ${formatDate(r.submitted_at)}` : ""}` : "";
  switch (r.status) {
    case "REJECTED": return `${submitted}; rejected by ${r.rejected_by ?? "—"}: ${r.rejection_reason ?? ""}`;
    case "WITHDRAWN": return `${submitted}; withdrawn by ${r.withdrawn_by ?? "—"}: ${r.withdrawal_reason ?? ""}`;
    case "SUPERSEDED": return `${submitted}; replaced by a newer submission`;
    case "REVERSED":
      return `received ${formatDate(r.received_on)} (approved by ${r.confirmed_by ?? "—"}), reversed by ${r.reversed_by ?? "—"}` +
        `${r.reversed_at ? ` on ${formatDate(r.reversed_at)}` : ""}: ${r.reversal_reason ?? ""}`;
    default: return submitted;
  }
}

/** Every earlier receipt for the invoice — rejected, withdrawn, replaced or reversed — kept as history. */
function ReceiptHistory({ history }: { history: InventoryReceiptSummary[] }) {
  const earlier = history.filter((r) => r.status !== "SUBMITTED" && r.status !== "POSTED");
  if (!earlier.length) return null;
  return (
    <ul className="space-y-0.5 text-xs text-muted-foreground" data-testid="receipt-history">
      {earlier.map((r) => (
        <li key={r.id}>
          <span className="font-medium">{RECEIPT_STATUS[r.status].label}</span> — {historyLine(r)}
        </li>
      ))}
    </ul>
  );
}

function BlockerTable({ blockers }: { blockers: ReceiptBlocker[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow><TableHead>Line</TableHead><TableHead>Product</TableHead><TableHead>Item code</TableHead><TableHead>Why</TableHead></TableRow>
      </TableHeader>
      <TableBody>
        {blockers.map((b) => (
          <TableRow key={`${b.sort_order}-${b.item_code}`}>
            <TableCell className="tabular-nums">{b.sort_order}</TableCell>
            <TableCell>{b.description ?? "—"}</TableCell>
            <TableCell className="font-mono text-xs">{b.item_code ?? "—"}</TableCell>
            <TableCell className="text-muted-foreground">{b.reason}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function PostedReceipt({ preview, receipt: r, onClose }: {
  preview: ReceiptPreview; receipt: InventoryReceiptSummary; onClose: () => void;
}) {
  const reverse = useReverseReceipt();
  const [reversing, setReversing] = useState(false);
  const [reason, setReason] = useState("");
  const submit = () => reverse.mutate(
    { invoiceId: preview.invoice_id, receiptId: r.id, reason: reason.trim() },
    {
      onSuccess: (result) => {
        toast.success(result.already_reversed ? "This receipt was already reversed." :
          `Receipt reversed: ${result.movements_reversed} movement${result.movements_reversed === 1 ? "" : "s"} compensated. ` +
          "The invoice can now be corrected.");
        onClose();
      },
      onError: (error) => toast.error(error instanceof ApiError ? error.userMessage : "The receipt was not reversed."),
    },
  );
  return (
    <div className="space-y-3 text-sm" data-testid="receipt-posted">
      <p className="flex items-center gap-2 font-medium">
        <CheckCircle2 className="size-4 text-success" aria-hidden /> Received on {formatDate(r.received_on)}
      </p>
      <p className="text-muted-foreground">
        {r.submitted_by ? `Submitted by ${r.submitted_by} · ` : ""}
        Approved by {r.confirmed_by} ({(r.confirmed_role ?? "").toLowerCase()}){r.self_approved ? " — self-approved" : ""}
        {" · "}{r.line_count} line{r.line_count === 1 ? "" : "s"}
        {r.discrepancy_count ? ` · ${r.discrepancy_count} differed from the invoice` : " · all as invoiced"}.
        While received, the invoice's lines, store and document cannot be changed.
      </p>
      <ReceiptHistory history={preview.history} />
      {reversing ? (
        <div className="space-y-2 rounded-md border p-3" data-testid="reverse-form">
          <p className="text-xs text-muted-foreground">
            Reversing records a compensating movement for every line and keeps this receipt as history. Stock for these
            products returns to what it was before the receipt. The invoice's Retailed mark is not changed; after
            correcting the invoice, mark it Retailed again to submit a new receipt.
          </p>
          <Input aria-label="Why the receipt is being reversed" placeholder="e.g. lager line was 2 cases, not 3"
                 value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setReversing(false)} disabled={reverse.isPending}>Keep receipt</Button>
            <Button variant="destructive" onClick={submit} disabled={!reason.trim() || reverse.isPending}
                    data-testid="confirm-reverse">
              {reverse.isPending ? "Reversing…" : "Reverse receipt"}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setReversing(true)}>Reverse receipt…</Button>
          <Button variant="outline" onClick={onClose}>Close</Button>
        </div>
      )}
    </div>
  );
}

function NoSubmission({ preview, onClose }: { preview: ReceiptPreview; onClose: () => void }) {
  return (
    <div className="space-y-3 text-sm" data-testid="receipt-not-submitted">
      <p className="font-medium">Nothing awaits approval for this invoice.</p>
      <p className="text-muted-foreground">
        A receipt is submitted when the invoice is marked Retailed on the invoice page. It is then approved here by a
        manager or administrator with access to this store, and only then does stock move.
      </p>
      {preview.eligibility_reasons.length ? (
        <div data-testid="receipt-not-eligible">
          <p className="font-medium">This invoice cannot be received yet:</p>
          <ul className="list-disc space-y-0.5 pl-5">
            {preview.eligibility_reasons.map((code) => <li key={code}>{receivableReason(code)}</li>)}
          </ul>
        </div>
      ) : null}
      {preview.blockers.length ? (
        <div className="space-y-2" data-testid="receipt-blocked">
          <p className="flex items-start gap-2 font-medium text-danger">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            {preview.blockers.length} product line{preview.blockers.length === 1 ? " has" : "s have"} no approved
            units-per-case conversion.
          </p>
          <BlockerTable blockers={preview.blockers} />
        </div>
      ) : null}
      <ReceiptHistory history={preview.history} />
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>Close</Button>
        <Button asChild><Link to={`/invoices/${preview.invoice_id}`}>Open the invoice</Link></Button>
      </div>
    </div>
  );
}

function ReceiptBody({ preview, onClose }: { preview: ReceiptPreview; onClose: () => void }) {
  if (preview.receipt) return <PostedReceipt preview={preview} receipt={preview.receipt} onClose={onClose} />;
  if (preview.submission) return <SubmissionReview preview={preview} submission={preview.submission} onClose={onClose} />;
  return <NoSubmission preview={preview} onClose={onClose} />;
}

function SubmissionReview({ preview, submission, onClose }: {
  preview: ReceiptPreview; submission: ReceiptSubmission; onClose: () => void;
}) {
  const { user } = useAuth();
  const r = submission.receipt;
  const stale = submission.stale_reasons.length > 0;
  const ownSubmission = Boolean(user && r.submitted_by === user.username);
  return (
    <div className="space-y-4 text-sm" data-testid="receipt-submission">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={stale ? "warning" : "info"} label={stale ? "Out of date" : "Awaiting approval"}
                    meaning={stale ? "The invoice changed after it was submitted" : RECEIPT_STATUS.SUBMITTED.meaning} />
        <span className="flex items-center gap-1 text-muted-foreground">
          <Clock className="size-3.5" aria-hidden />
          Submitted by {r.submitted_by} ({(r.submitted_role ?? "").toLowerCase()})
          {r.submitted_at ? ` on ${formatDateTime(r.submitted_at)}` : ""} by marking it Retailed
          {submission.export_number ? ` (PDI export #${submission.export_number})` : ""}.
        </span>
      </div>
      <ReceiptHistory history={preview.history} />

      {stale ? (
        <div className="space-y-2 rounded-md border border-warning p-3" data-testid="submission-stale">
          <p className="flex items-start gap-2 font-medium">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
            This submission no longer matches the invoice and cannot be approved.
          </p>
          <ul className="list-disc space-y-0.5 pl-5">
            {submission.stale_reasons.map((code) => <li key={code}>{staleReason(code)}</li>)}
          </ul>
          {submission.line_changes.length ? (
            <Table data-testid="submission-changes">
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead><TableHead>Change</TableHead>
                  <TableHead className="text-right">Submitted units</TableHead><TableHead className="text-right">Now</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {submission.line_changes.map((c) => (
                  <TableRow key={c.invoice_item_id}>
                    <TableCell>{c.description ?? c.item_code ?? "—"}</TableCell>
                    <TableCell>{c.change}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatUnits(c.proposed_units)}{c.proposed_units_per_case ? ` (×${c.proposed_units_per_case})` : ""}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatUnits(c.current_units)}{c.current_units_per_case ? ` (×${c.current_units_per_case})` : ""}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
          <p className="text-xs text-muted-foreground">
            Re-verify the invoice and mark it Retailed again to submit it afresh, or reject this submission.
          </p>
        </div>
      ) : null}

      {preview.eligibility_reasons.length ? (
        <div data-testid="receipt-not-eligible">
          <p className="font-medium">This invoice cannot be received:</p>
          <ul className="list-disc space-y-0.5 pl-5">
            {preview.eligibility_reasons.map((code) => <li key={code}>{receivableReason(code)}</li>)}
          </ul>
        </div>
      ) : null}
      {preview.blockers.length ? (
        <div className="space-y-2" data-testid="receipt-blocked">
          <p className="flex items-start gap-2 font-medium text-danger">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            {preview.blockers.length} product line{preview.blockers.length === 1 ? " has" : "s have"} no approved
            units-per-case conversion. It cannot be approved until every product line has one.
          </p>
          <BlockerTable blockers={preview.blockers} />
        </div>
      ) : null}

      {preview.can_approve ? (
        <ApprovalForm preview={preview} submission={submission} ownSubmission={ownSubmission} onClose={onClose} />
      ) : (
        <RejectOnly preview={preview} submission={submission} onClose={onClose} />
      )}
    </div>
  );
}

function RejectControl({ preview, submission, onDone, disabled }: {
  preview: ReceiptPreview; submission: ReceiptSubmission; onDone: () => void; disabled?: boolean;
}) {
  const reject = useRejectReceipt();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!open) {
    return <Button variant="outline" onClick={() => setOpen(true)} disabled={disabled}>Reject…</Button>;
  }
  const submit = () => reject.mutate(
    { invoiceId: preview.invoice_id, receiptId: submission.receipt.id, reason: reason.trim() },
    {
      onSuccess: () => { toast.success("Submission rejected. No stock moved."); onDone(); },
      onError: (error) => toast.error(error instanceof ApiError ? error.userMessage : "The submission was not rejected."),
    },
  );
  return (
    <div className="flex w-full flex-wrap items-center justify-end gap-2" data-testid="reject-form">
      <Input aria-label="Why the submission is rejected" placeholder="e.g. delivery refused at the door"
             value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} className="max-w-sm" />
      <Button variant="ghost" onClick={() => setOpen(false)} disabled={reject.isPending}>Cancel</Button>
      <Button variant="destructive" onClick={submit} disabled={!reason.trim() || reject.isPending} data-testid="confirm-reject">
        {reject.isPending ? "Rejecting…" : "Reject submission"}
      </Button>
    </div>
  );
}

function RejectOnly({ preview, submission, onClose }: { preview: ReceiptPreview; submission: ReceiptSubmission; onClose: () => void }) {
  return (
    <div className="flex flex-wrap justify-end gap-2">
      <Button variant="outline" asChild><Link to={`/invoices/${preview.invoice_id}`}>Open the invoice</Link></Button>
      <RejectControl preview={preview} submission={submission} onDone={onClose} />
      <Button variant="outline" onClick={onClose}>Close</Button>
    </div>
  );
}

function ApprovalForm({ preview, submission, ownSubmission, onClose }: {
  preview: ReceiptPreview; submission: ReceiptSubmission; ownSubmission: boolean; onClose: () => void;
}) {
  const approve = useApproveReceipt();
  // The day a person declared the goods arrived, when one did; otherwise today, as before. Never the browser's clock when
  // a delivery day is known: that is the guess the declaration exists to replace.
  const declared = preview.declared_delivery_date ?? null;
  const [receivedOn, setReceivedOn] = useState(declared ?? todayIso());
  const [note, setNote] = useState("");
  const [entries, setEntries] = useState<Record<string, LineEntry>>({});
  const [serverProblems, setServerProblems] = useState<Record<string, string>>({});

  useEffect(() => {
    setEntries(Object.fromEntries(preview.lines.map((l) => [l.invoice_item_id, { accepted: String(Number(l.expected_units)), reason: "" }])));
  }, [preview.lines]);

  const problems = useMemo(
    () => Object.fromEntries(preview.lines.map((l) => [l.invoice_item_id, entries[l.invoice_item_id] ? lineProblem(l, entries[l.invoice_item_id]) : "…"])),
    [preview.lines, entries],
  );
  const invalid = Object.values(problems).some(Boolean) || !receivedOn;
  const discrepancies = preview.lines.filter((l) => entries[l.invoice_item_id] && isDiscrepancy(l, entries[l.invoice_item_id])).length;
  const totalUnits = preview.lines.reduce((sum, l) => sum + (Number(entries[l.invoice_item_id]?.accepted) || 0), 0);

  const set = (id: string, patch: Partial<LineEntry>) => setEntries((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));

  const submit = () => {
    setServerProblems({});
    approve.mutate(
      {
        invoiceId: preview.invoice_id,
        body: {
          receipt_id: submission.receipt.id,
          received_on: receivedOn,
          note: note.trim() || null,
          lines: preview.lines.map((l) => ({
            invoice_item_id: l.invoice_item_id,
            accepted_units: Number(entries[l.invoice_item_id].accepted),
            reason: entries[l.invoice_item_id].reason.trim() || null,
          })),
        },
      },
      {
        onSuccess: () => { toast.success("Receipt approved and posted."); onClose(); },
        onError: (error) => {
          const detail = error instanceof ApiError ? (error.detail as { lines?: Record<string, string> } | null) : null;
          if (detail?.lines) setServerProblems(detail.lines);
          toast.error(error instanceof ApiError ? error.userMessage : "The receipt was not approved.");
        },
      },
    );
  };

  return (
    <div className="space-y-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Product</TableHead>
            <TableHead className="text-right">Invoiced</TableHead>
            <TableHead className="text-right">Proposed units</TableHead>
            <TableHead className="w-[110px]">Accepted units</TableHead>
            <TableHead>Reason (if different)</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {preview.lines.map((line) => {
            const entry = entries[line.invoice_item_id] ?? { accepted: "", reason: "" };
            const problem = serverProblems[line.invoice_item_id] ?? problems[line.invoice_item_id];
            const differs = isDiscrepancy(line, entry);
            const name = line.description ?? line.item_code;
            return (
              <TableRow key={line.invoice_item_id} data-testid="receipt-line">
                <TableCell className="min-w-[200px]">
                  <div className="font-medium">{name}</div>
                  <div className="font-mono text-xs text-muted-foreground">{line.item_code}</div>
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground" title={line.conversion_scope ?? line.conversion_path}>
                  {formatUnits(line.cases)} × {line.units_per_case}
                </TableCell>
                <TableCell className="text-right tabular-nums">{formatUnits(line.expected_units)}</TableCell>
                <TableCell>
                  <Input
                    type="number" min={0} step={1} inputMode="numeric"
                    aria-label={`Accepted units for ${name}`}
                    aria-invalid={Boolean(problem) || undefined}
                    value={entry.accepted}
                    onChange={(e) => set(line.invoice_item_id, { accepted: e.target.value })}
                    className={differs ? "border-warning" : undefined}
                  />
                </TableCell>
                <TableCell className="min-w-[200px]">
                  {differs ? (
                    <Input
                      aria-label={`Reason for ${name}`}
                      placeholder="e.g. 2 cans damaged, refused"
                      value={entry.reason}
                      maxLength={500}
                      onChange={(e) => set(line.invoice_item_id, { reason: e.target.value })}
                    />
                  ) : (
                    <span className="text-xs text-muted-foreground">As proposed</span>
                  )}
                  {problem && problem !== "…" ? <p className="mt-1 text-xs text-danger">{problem}</p> : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <div className="grid gap-3 sm:grid-cols-[180px_1fr]">
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted-foreground">Received on</span>
          <Input type="date" value={receivedOn} max={declared && declared > todayIso() ? declared : todayIso()}
                 onChange={(e) => setReceivedOn(e.target.value)} aria-label="Received on" />
          {declared ? (
            <span className="block text-[0.7rem] text-muted-foreground" data-testid="received-on-hint">
              {receivedOn === declared
                ? "The delivery date declared at upload."
                : `Differs from the delivery date declared at upload (${formatPlainDate(declared)}). That is allowed and recorded.`}
            </span>
          ) : null}
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted-foreground">Note (optional)</span>
          <Input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder="e.g. driver, delivery condition" aria-label="Note" />
        </label>
      </div>

      <p className="text-xs text-muted-foreground">
        Posts {formatUnits(totalUnits)} sellable unit{totalUnits === 1 ? "" : "s"} across {preview.lines.length} line
        {preview.lines.length === 1 ? "" : "s"}{discrepancies ? `, ${discrepancies} different from the proposal` : ""}.
        Approval is recorded once and cannot be edited; the invoice is not changed.
        {ownSubmission ? " You submitted this receipt; approving it yourself is recorded as self-approval." : ""}
      </p>
      <div className="flex flex-wrap justify-end gap-2">
        <RejectControl preview={preview} submission={submission} onDone={onClose} disabled={approve.isPending} />
        <Button variant="outline" onClick={onClose} disabled={approve.isPending}>Cancel</Button>
        <Button onClick={submit} disabled={invalid || approve.isPending} data-testid="confirm-approve">
          {approve.isPending ? "Posting…" : "Approve and post"}
        </Button>
      </div>
    </div>
  );
}
