import { useEffect, useState } from "react";
import { toast } from "sonner";

import type { ProposalRow } from "@/api/types";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useBulkDecideProposals } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { roleLabel } from "@/lib/commercial";
import { batchFailures } from "@/lib/proposals";

export type BulkAction = "approve" | "reject";

/**
 * Approve / Reject for a selection of PENDING proposals.
 *
 * The reviewer is the signed-in account, recorded by the server on every
 * row — never a typed name. A note is optional for
 * routine approvals. The batch is all-or-nothing on the backend: if one
 * selected row was decided elsewhere meanwhile, nothing lands and the
 * offending rows are named here rather than the UI claiming success.
 */
export function BulkDecisionDialog({
  action,
  rows,
  onOpenChange,
  onDone,
}: {
  action: BulkAction | null;
  rows: ProposalRow[];
  onOpenChange: (open: boolean) => void;
  onDone: (decidedIds: string[]) => void;
}) {
  const decide = useBulkDecideProposals();
  const { user } = useAuth();
  const [note, setNote] = useState("");
  const [shared, setShared] = useState(true);
  const [keepStores, setKeepStores] = useState(false);
  const [failures, setFailures] = useState<Record<string, string>>({});
  const open = action !== null && rows.length > 0;

  useEffect(() => {
    if (open) {
      setFailures({});
      setNote("");
      setShared(true);
      setKeepStores(false);
    }
  }, [open, action]);

  const name = user?.username ?? "";
  const count = rows.length;
  const verb = action === "approve" ? "Approve" : "Reject";
  const byId = new Map(rows.map((row) => [row.id, row]));

  const submit = () => {
    if (!action || !name || decide.isPending) return;
    decide.mutate(
      {
        action,
        decision: {
          proposal_ids: rows.map((row) => row.id), reviewed_by: name, note: note.trim() || null,
          ...(action === "approve" ? { scope: shared ? "shared" : "store", keep_store_values: shared && keepStores } : {}),
        },
      },
      {
        onSuccess: (result) => {
          onDone(result.decided.map((outcome) => outcome.id));
          onOpenChange(false);
          toast.success(
            action === "approve"
              ? `Approved ${result.decided.length}${shared ? " for every store" : ""} — wrote ${result.decided.length} mapping${result.decided.length === 1 ? "" : "s"}.`
              : `Rejected ${result.decided.length} — master data untouched.`,
          );
        },
        onError: (error) => {
          const named = batchFailures(error);
          setFailures(named);
          toast.error(
            Object.keys(named).length > 0
              ? `Nothing was ${action === "approve" ? "approved" : "rejected"} — ${Object.keys(named).length} of ${count} could not be decided.`
              : error instanceof Error
                ? error.message
                : "Decision failed.",
          );
        },
      },
    );
  };

  return (
    <AlertDialog open={open} onOpenChange={(next) => !next && !decide.isPending && onOpenChange(false)}>
      <AlertDialogContent data-testid="bulk-decision-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>
            {verb} {count} record{count === 1 ? "" : "s"}
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-[0.82rem]">
              {action === "approve" ? (
                <p>
                  Each proposal is approved on its own row and writes its own authoritative units-per-case
                  mapping — exactly as approving it individually would. Every invoice carrying these
                  products will build its EDI from them.
                  {shared ? " Used for every store, the same products resolve at the other stores too." : ""}
                </p>
              ) : (
                <p>The proposals are frozen as rejected. Master data is not touched.</p>
              )}
              <p className="font-mono text-[0.72rem] text-muted-foreground">
                {rows.slice(0, 6).map((row) => `${row.entity_key} → ${String(row.proposed_value)}`).join(" · ")}
                {rows.length > 6 ? ` · +${rows.length - 6} more` : ""}
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-2">
          <div className="space-y-1">
            <span className="text-[0.75rem] font-medium">{action === "approve" ? "Approved by" : "Rejected by"}</span>
            <p className="text-[0.82rem]" data-testid="bulk-reviewer">{name || "—"} · {roleLabel(user?.role)}</p>
          </div>
          {action === "approve" ? (
            <div className="space-y-1.5 rounded-md border px-3 py-2" data-testid="bulk-scope">
              <label className="flex items-start gap-2 text-[0.8rem]">
                <Checkbox checked={shared} onCheckedChange={(v) => setShared(v === true)} disabled={decide.isPending}
                          aria-label="Use for every store" className="mt-0.5" />
                <span>
                  <span className="font-medium">Use for every store</span>
                  <span className="block text-[0.72rem] text-muted-foreground">
                    Refused, with the stores named, if another store already holds a different approved value.
                  </span>
                </span>
              </label>
              {shared ? (
                <label className="flex items-start gap-2 text-[0.8rem]">
                  <Checkbox checked={keepStores} onCheckedChange={(v) => setKeepStores(v === true)}
                            disabled={decide.isPending} aria-label="Keep other stores' different values" className="mt-0.5" />
                  <span>
                    Keep other stores&apos; different values as store overrides
                    <span className="block text-[0.72rem] text-muted-foreground">Each is recorded with the reason.</span>
                  </span>
                </label>
              ) : null}
            </div>
          ) : null}
          <div className="space-y-1">
            <label htmlFor="bulk-note" className="text-[0.75rem] font-medium">
              Note <span className="text-muted-foreground">(optional, recorded on every row)</span>
            </label>
            <Input
              id="bulk-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder={action === "approve" ? "e.g. printed count and store ratio agree" : "e.g. test run; invoice deleted"}
              disabled={decide.isPending}
              maxLength={2000}
            />
          </div>
          {Object.keys(failures).length > 0 ? (
            <div
              role="alert"
              className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-[0.75rem]"
              data-testid="bulk-failures"
            >
              <p className="font-medium">Nothing was changed. These rows could not be decided:</p>
              <ul className="mt-1 space-y-0.5 font-mono text-[0.7rem]">
                {Object.entries(failures).map(([id, reason]) => (
                  <li key={id}>
                    {byId.get(id)?.entity_key ?? id.slice(0, 8)} — {reason}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-muted-foreground">Refresh the queue, reselect, and try again.</p>
            </div>
          ) : null}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={decide.isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: action === "reject" ? "destructive" : "default" })}
            disabled={!name || decide.isPending}
            onClick={(event) => {
              event.preventDefault(); // keep the dialog open until the request settles
              submit();
            }}
          >
            {decide.isPending ? "Saving…" : `${verb} ${count}`}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
