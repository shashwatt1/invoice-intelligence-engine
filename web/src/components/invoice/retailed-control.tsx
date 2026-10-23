import { CheckCheck, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import type { Completion, ReceiptPreview } from "@/api/types";
import { StatusPill } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/hooks/use-auth";
import { useInventoryStatus, useReceiptPreview, useRetailed } from "@/hooks/use-api";
import { formatDate, formatDateTime } from "@/lib/format";
import { RECEIPT_STATUS, staleReason } from "@/lib/inventory";

/**
 * Retailed, beside the PDI generator: the person's attestation that they checked
 * the item list, quantities, prices, line totals and totals against the source
 * invoice. Not goods receipt; no inventory effect. The server decides whether it
 * can be set (status VALIDATED, and a ledger-recorded PDI file identical to the
 * one the invoice produces now); this shows its answer, the reason when it is not
 * yet possible, and the status: Retailed, or "re-verify" when anything attested
 * changed or the invoice is no longer VALIDATED.
 *
 * With inventory switched on, marking Retailed also submits the invoice's receipt for
 * approval; its state is shown beside the mark (InventoryReceiptStatus) for an account
 * that may use the invoice's store — and nothing at all otherwise.
 */
export function RetailedControl({ invoiceId, completion }: { invoiceId: string; completion: Completion | null | undefined }) {
  const { hasRole } = useAuth();
  const canAct = hasRole("MANAGER");
  const retailed = useRetailed(invoiceId);
  const [reopening, setReopening] = useState(false);
  const [note, setNote] = useState("");
  if (!completion) return null;

  const run = (action: "mark" | "reopen") => {
    retailed.mutate({ action, note: note.trim() || null }, {
      onSuccess: () => {
        setReopening(false);
        setNote("");
        toast.success(action === "mark" ? "Marked Retailed." : "Retailed mark removed.");
      },
      onError: (error) => toast.error(error instanceof Error ? error.message : "Not changed."),
    });
  };

  const by = completion.retailed_by ? ` by ${completion.retailed_by}` : "";
  const at = completion.retailed_at ? ` · ${formatDateTime(completion.retailed_at)}` : "";
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="retailed-control">
      {completion.status === "retailed" ? (
        <StatusPill tone="success" label="Retailed" meaning={`Marked${by}${at}`} />
      ) : completion.status === "retailed_stale" ? (
        <StatusPill tone="warning" label="Retailed · re-verify"
                    meaning={`Marked${by}${at}; the invoice, its totals or mappings changed, or it is no longer VALIDATED — verify again and re-mark`} />
      ) : null}
      {canAct ? <InventoryReceiptStatus invoiceId={invoiceId} /> : null}
      {canAct && completion.status !== "retailed" ? (
        <Button size="sm" variant="outline" onClick={() => run("mark")}
                disabled={!completion.can_mark || retailed.isPending}
                title={completion.can_mark
                  ? "Records that you verified the item list, quantities, unit prices, line totals and totals against the source invoice. Does not change inventory."
                  : completion.reason ?? undefined}
                data-testid="retailed-mark">
          <CheckCheck className="size-3.5" /> {completion.status === "retailed_stale" ? "Mark Retailed again" : "Mark Retailed"}
        </Button>
      ) : null}
      {canAct && completion.status !== "not_retailed" ? (
        reopening ? (
          <span className="flex items-center gap-1.5">
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Reason (optional)"
                   className="h-8 w-44" aria-label="Reason for reopening" maxLength={500} />
            <Button size="sm" variant="ghost" onClick={() => run("reopen")} disabled={retailed.isPending}>Reopen</Button>
            <Button size="sm" variant="ghost" onClick={() => setReopening(false)} disabled={retailed.isPending}>Cancel</Button>
          </span>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => setReopening(true)} data-testid="retailed-reopen">
            <RotateCcw className="size-3.5" /> Reopen
          </Button>
        )
      ) : null}
      {canAct && completion.status !== "retailed" && completion.can_mark ? (
        <span className="t-meta max-w-72 basis-full" data-testid="retailed-attestation-note">
          Before marking, check the item list, quantities, unit prices, line totals and totals against the source invoice.
        </span>
      ) : null}
      {canAct && completion.status !== "retailed" && !completion.can_mark && completion.reason ? (
        <span className="t-meta max-w-72 basis-full" data-testid="retailed-reason">{completion.reason}</span>
      ) : null}
    </div>
  );
}

function receiptPill(preview: ReceiptPreview): { tone: "success" | "warning" | "danger" | "info"; label: string; meaning: string } | null {
  if (preview.receipt) {
    const r = preview.receipt;
    return { tone: "success", label: `Inventory · received ${formatDate(r.received_on)}`,
             meaning: `Approved by ${r.confirmed_by ?? "—"}${r.self_approved ? " (self-approved)" : ""}` };
  }
  if (preview.submission) {
    const s = preview.submission;
    if (s.stale_reasons.length) {
      return { tone: "warning", label: "Inventory · submission out of date",
               meaning: `${s.stale_reasons.map(staleReason).join("; ")}. Re-verify and mark Retailed again.` };
    }
    return { tone: "info", label: "Inventory · awaiting approval",
             meaning: `Submitted by ${s.receipt.submitted_by ?? "—"}; no stock moves until a manager approves it.` };
  }
  const last = preview.history[preview.history.length - 1];
  if (last?.status === "REJECTED") {
    return { tone: "danger", label: "Inventory · rejected", meaning: `By ${last.rejected_by ?? "—"}: ${last.rejection_reason ?? ""}` };
  }
  if (last?.status === "REVERSED") {
    return { tone: "warning", label: "Inventory · receipt reversed", meaning: RECEIPT_STATUS.REVERSED.meaning };
  }
  return null;
}

/**
 * The invoice's inventory receipt state, beside the Retailed mark. Asked only by a
 * MANAGER/ADMIN while inventory is switched on; the server answers 404 for a store the
 * account has no grant for, and then nothing is shown — not even that a receipt exists.
 */
export function InventoryReceiptStatus({ invoiceId }: { invoiceId: string }) {
  const status = useInventoryStatus();
  const preview = useReceiptPreview(invoiceId, { enabled: status.data?.enabled === true, quiet: true });
  if (!preview.data) return null;
  const pill = receiptPill(preview.data);
  if (!pill) return null;
  const store = preview.data.store;
  return (
    <span className="flex items-center gap-1.5" data-testid="inventory-receipt-status">
      <StatusPill tone={pill.tone} label={pill.label} meaning={pill.meaning} />
      {store ? (
        <Link className="t-meta underline-offset-2 hover:underline"
              to={`/inventory?store=${store.id}&tab=${preview.data.submission ? "approvals" : "invoices"}`}>
          {preview.data.submission ? "Review" : "Open"}
        </Link>
      ) : null}
    </span>
  );
}
