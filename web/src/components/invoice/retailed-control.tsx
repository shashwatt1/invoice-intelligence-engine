import { CheckCheck, RotateCcw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { Completion } from "@/api/types";
import { StatusPill } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/hooks/use-auth";
import { useRetailed } from "@/hooks/use-api";
import { formatDateTime } from "@/lib/format";

/**
 * Retailed, beside the PDI generator: the person's attestation that they checked
 * the item list, quantities, prices, line totals and totals against the source
 * invoice. Not goods receipt; no inventory effect. The server decides whether it
 * can be set (status VALIDATED, and a ledger-recorded PDI file identical to the
 * one the invoice produces now); this shows its answer, the reason when it is not
 * yet possible, and the status: Retailed, or "re-verify" when anything attested
 * changed or the invoice is no longer VALIDATED.
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
