import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { InventoryReceiptSummary, ReceiptPreview } from "@/api/types";
import { ErrorState, PanelSkeleton } from "@/components/shared/states";
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
import { usePostReceipt, useReceiptPreview, useReverseReceipt } from "@/hooks/use-api";
import { formatDate } from "@/lib/format";
import { formatUnits, isDiscrepancy, lineProblem, type LineEntry, receivableReason, todayIso } from "@/lib/inventory";

/**
 * Confirming that an invoice's goods arrived. Never inferred from the invoice
 * being validated or exported: a person states, per line, the sellable units
 * accepted. Each line is prefilled with what the invoice and its approved
 * units per case expect; a different figure needs a reason. Any product line
 * without an approved conversion blocks the whole receipt and is named here.
 * The invoice itself is never changed. The server enforces all of it.
 *
 * A received invoice is locked against correction. Reversing the receipt
 * (with a reason) compensates every movement, keeps the receipt as history,
 * and lets the invoice be corrected and received again.
 */
export function ReceiptDialog({ invoiceId, onClose }: { invoiceId: string; onClose: () => void }) {
  const preview = useReceiptPreview(invoiceId);
  const data = preview.data;
  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-h-[88vh] max-w-4xl overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">
            Receive invoice {data?.invoice_number ? `#${data.invoice_number}` : ""}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {data?.store ? `Into ${data.store.label}. ` : ""}Quantities are sellable units.
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

function ReversedHistory({ history }: { history: InventoryReceiptSummary[] }) {
  const reversed = history.filter((r) => r.status === "REVERSED");
  if (!reversed.length) return null;
  return (
    <ul className="space-y-0.5 text-xs text-muted-foreground" data-testid="reversed-receipts">
      {reversed.map((r) => (
        <li key={r.id}>
          Earlier receipt of {formatDate(r.received_on)} by {r.confirmed_by}, reversed by {r.reversed_by}
          {r.reversed_at ? ` on ${formatDate(r.reversed_at)}` : ""}: {r.reversal_reason}
        </li>
      ))}
    </ul>
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
        Confirmed by {r.confirmed_by} ({r.confirmed_role.toLowerCase()}) · {r.line_count} line{r.line_count === 1 ? "" : "s"}
        {r.discrepancy_count ? ` · ${r.discrepancy_count} differed from the invoice` : " · all as invoiced"}.
        While received, the invoice's lines, store and document cannot be changed.
      </p>
      <ReversedHistory history={preview.history} />
      {reversing ? (
        <div className="space-y-2 rounded-md border p-3" data-testid="reverse-form">
          <p className="text-xs text-muted-foreground">
            Reversing records a compensating movement for every line and keeps this receipt as history. Stock for these
            products returns to what it was before the receipt; the invoice can then be corrected and received again.
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

function ReceiptBody({ preview, onClose }: { preview: ReceiptPreview; onClose: () => void }) {
  if (preview.receipt) return <PostedReceipt preview={preview} receipt={preview.receipt} onClose={onClose} />;
  if (preview.eligibility_reasons.length) {
    return (
      <div className="space-y-3 text-sm" data-testid="receipt-not-eligible">
        <p className="font-medium">This invoice cannot be received yet:</p>
        <ul className="list-disc space-y-0.5 pl-5">
          {preview.eligibility_reasons.map((code) => <li key={code}>{receivableReason(code)}</li>)}
        </ul>
        <div className="flex justify-end"><Button variant="outline" onClick={onClose}>Close</Button></div>
      </div>
    );
  }
  if (preview.blockers.length) {
    return (
      <div className="space-y-3 text-sm" data-testid="receipt-blocked">
        <p className="flex items-start gap-2 font-medium text-danger">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          {preview.blockers.length} product line{preview.blockers.length === 1 ? " has" : "s have"} no approved
          units-per-case conversion. Nothing can be received until every product line has one.
        </p>
        <Table>
          <TableHeader>
            <TableRow><TableHead>Line</TableHead><TableHead>Product</TableHead><TableHead>Item code</TableHead><TableHead>Why</TableHead></TableRow>
          </TableHeader>
          <TableBody>
            {preview.blockers.map((b) => (
              <TableRow key={`${b.sort_order}-${b.item_code}`}>
                <TableCell className="tabular-nums">{b.sort_order}</TableCell>
                <TableCell>{b.description ?? "—"}</TableCell>
                <TableCell className="font-mono text-xs">{b.item_code ?? "—"}</TableCell>
                <TableCell className="text-muted-foreground">{b.reason}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Close</Button>
          <Button asChild><Link to={`/invoices/${preview.invoice_id}`}>Open the invoice to map them</Link></Button>
        </div>
      </div>
    );
  }
  return <ReceiptForm preview={preview} onClose={onClose} />;
}

function ReceiptForm({ preview, onClose }: { preview: ReceiptPreview; onClose: () => void }) {
  const post = usePostReceipt();
  const [receivedOn, setReceivedOn] = useState(todayIso());
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
    post.mutate(
      {
        invoiceId: preview.invoice_id,
        body: {
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
        onSuccess: () => { toast.success("Receipt recorded."); onClose(); },
        onError: (error) => {
          const detail = error instanceof ApiError ? (error.detail as { lines?: Record<string, string> } | null) : null;
          if (detail?.lines) setServerProblems(detail.lines);
          toast.error(error instanceof ApiError ? error.userMessage : "The receipt was not recorded.");
        },
      },
    );
  };

  return (
    <div className="space-y-4 text-sm">
      <ReversedHistory history={preview.history} />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Product</TableHead>
            <TableHead className="text-right">Invoiced</TableHead>
            <TableHead className="text-right">Expected units</TableHead>
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
                    <span className="text-xs text-muted-foreground">As invoiced</span>
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
          <Input type="date" value={receivedOn} max={todayIso()} onChange={(e) => setReceivedOn(e.target.value)} aria-label="Received on" />
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium text-muted-foreground">Note (optional)</span>
          <Input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder="e.g. driver, delivery condition" aria-label="Note" />
        </label>
      </div>

      <p className="text-xs text-muted-foreground">
        Records {formatUnits(totalUnits)} sellable unit{totalUnits === 1 ? "" : "s"} across {preview.lines.length} line
        {preview.lines.length === 1 ? "" : "s"}{discrepancies ? `, ${discrepancies} different from the invoice` : ""}.
        A receipt is recorded once and cannot be edited; the invoice is not changed.
      </p>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose} disabled={post.isPending}>Cancel</Button>
        <Button onClick={submit} disabled={invalid || post.isPending} data-testid="confirm-receipt">
          {post.isPending ? "Recording…" : "Confirm receipt"}
        </Button>
      </div>
    </div>
  );
}
