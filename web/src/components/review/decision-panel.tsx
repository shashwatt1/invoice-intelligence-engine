import { Check, Save, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import type { ProposalDetail } from "@/api/types";
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
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useDecideProposal, useReviseProposal } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { roleLabel } from "@/lib/commercial";
import { isEditable, parseUnits } from "@/lib/proposals";

/**
 * Approve / Reject for one PENDING proposal.
 *
 * The reviewer is the signed-in account — the server records it from the
 * session, never from a typed name. A store value the Product Master governs
 * or disputes cannot be approved here (product_master_block); it can still be
 * rejected. Each decision is confirmed in a dialog
 * that restates what approving will write, because approval is the one
 * action in this UI that changes master data.
 *
 * A pending units-per-case value can be corrected here before deciding.
 * A proposal is immutable, so a changed value is saved as a revision (a new
 * proposal in the reviewer's name; the original is frozen as superseded) and
 * "approve" then approves that revision. Master data is written only by the
 * approval, exactly as without an edit.
 */
export function DecisionPanel({ proposal }: { proposal: ProposalDetail }) {
  const decide = useDecideProposal();
  const revise = useReviseProposal();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);
  const editable = isEditable(proposal);
  const [unitsText, setUnitsText] = useState(String(proposal.proposed_value));
  const units = editable ? parseUnits(unitsText) : null;
  const changed = editable && units !== null && units !== Number(proposal.proposed_value);
  const busy = decide.isPending || revise.isPending;

  const name = user?.username ?? "";
  const blocked = proposal.product_master_block ?? null;
  const proposed = String(proposal.proposed_value);
  const current = proposal.current_master_value;

  // Save the corrected value as a revision; returns the new proposal's id.
  const saveRevision = async (): Promise<string> => {
    const result = await revise.mutateAsync({
      proposalId: proposal.id,
      revision: { proposed_value: units as number, proposed_by: name, note: note.trim() || null },
    });
    return result.proposal.id;
  };

  const saveOnly = async () => {
    if (!changed || !name || busy) return;
    try {
      const newId = await saveRevision();
      toast.success(`${proposal.entity_key}: ${proposed} → ${units}. New proposal pending approval.`);
      navigate(`/data-review/proposals/${newId}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The value was not saved.");
    }
  };

  const approveChanged = async () => {
    let newId: string;
    try {
      newId = await saveRevision();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "The value was not saved; nothing was approved.");
      return;
    }
    try {
      const result = await decide.mutateAsync({
        proposalId: newId, action: "approve", decision: { reviewed_by: name, note: note.trim() || null },
      });
      setConfirming(null);
      toast.success(`Approved ${units} — wrote ${result.applied_to ?? "the mapping"}.`);
    } catch (error) {
      setConfirming(null);
      toast.error(`${units} was saved as a new pending proposal, but approving it failed: ${
        error instanceof Error ? error.message : "unknown error"}`);
    }
    navigate(`/data-review/proposals/${newId}`);
  };

  const submit = () => {
    if (!confirming || !name) return;
    const action = confirming;
    if (action === "approve" && changed) {
      void approveChanged();
      return;
    }
    decide.mutate(
      { proposalId: proposal.id, action, decision: { reviewed_by: name, note: note.trim() || null } },
      {
        onSuccess: (result) => {
          setConfirming(null);
          toast.success(
            action === "approve"
              ? `Approved — wrote ${result.applied_to ?? "the mapping"}.`
              : "Rejected — master data untouched.",
          );
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Decision failed.");
        },
      },
    );
  };

  return (
    <div className="space-y-3">
      {editable ? (
        <div className="space-y-1">
          <label htmlFor="review-units" className="text-[0.75rem] font-medium">Units per case</label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="review-units"
              value={unitsText}
              onChange={(event) => setUnitsText(event.target.value)}
              inputMode="numeric"
              className="h-8 w-24 text-right font-mono tabular-nums"
              aria-invalid={units === null}
              disabled={busy}
              data-testid="decision-units"
            />
            {units === null ? (
              <span className="text-danger text-[0.72rem]" role="alert">A whole number from 1 to 9999.</span>
            ) : changed ? (
              <span className="text-[0.72rem] text-muted-foreground" data-testid="decision-units-changed">
                Proposed {proposed} → your value {units}. Saved as a new proposal in your name; the original is kept as superseded.
              </span>
            ) : (
              <span className="text-[0.72rem] text-muted-foreground">Change it if the proposed value is wrong, then approve.</span>
            )}
          </div>
        </div>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div className="space-y-1">
          <span className="text-[0.75rem] font-medium">Reviewed by</span>
          <p className="text-[0.82rem]" data-testid="decision-reviewer">{name || "—"} · {roleLabel(user?.role)}</p>
          <p className="text-[0.68rem] text-muted-foreground">Recorded from your signed-in account.</p>
        </div>
        <div className="space-y-1">
          <label htmlFor="review-note" className="text-[0.75rem] font-medium">
            Note <span className="text-muted-foreground">(optional)</span>
          </label>
          <Input
            id="review-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Why — e.g. ratio and package string agree; verified on shelf"
            disabled={busy}
            maxLength={2000}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={!name || busy || blocked !== null || (editable && units === null)}
          onClick={() => setConfirming("approve")}
          data-testid="decision-approve"
        >
          <Check className="size-3.5" strokeWidth={3} /> {changed ? `Approve ${units}` : "Approve"}
        </Button>
        {changed ? (
          <Button size="sm" variant="outline" disabled={!name || busy} onClick={() => void saveOnly()}
                  data-testid="decision-save-only">
            <Save className="size-3.5" /> Save {units} without approving
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="outline"
          disabled={!name || busy || changed}
          title={changed ? "Reject applies to the proposed value; undo your change first." : undefined}
          onClick={() => setConfirming("reject")}
        >
          <X className="size-3.5" /> Reject
        </Button>
        {blocked ? (
          <span className="text-danger text-[0.72rem]" role="alert" data-testid="product-master-block">{blocked}</span>
        ) : null}
      </div>

      <AlertDialog open={confirming !== null} onOpenChange={(open) => !open && !busy && setConfirming(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirming === "approve" ? "Approve this proposal?" : "Reject this proposal?"}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-[0.82rem]">
                {confirming === "approve" ? (
                  <>
                    <p>
                      {changed ? (
                        <>Your value <span className="font-semibold text-foreground">{units}</span> replaces the proposed {proposed}: it is
                        saved as a new proposal in your name (the original is kept as superseded) and approved. </>
                      ) : null}
                      This writes <span className="font-semibold text-foreground">{changed ? units : proposed} {proposal.field.replace(/_/g, " ")}</span> for
                      UPC <span className="font-mono text-foreground">{proposal.entity_key}</span> as authoritative
                      master data. Every invoice carrying this product — past and future — will build its EDI from it.
                    </p>
                    {current !== null && current !== undefined ? (
                      <p>
                        It replaces the current value <span className="font-semibold text-foreground">{String(current)}</span>.
                      </p>
                    ) : (
                      <p>There is no current value; this product is unmapped today.</p>
                    )}
                  </>
                ) : (
                  <p>
                    The proposal is frozen as rejected. Master data is not touched
                    {current !== null && current !== undefined ? (
                      <> — the current value <span className="font-semibold text-foreground">{String(current)}</span> stands.</>
                    ) : (
                      <> — the product stays unmapped.</>
                    )}
                  </p>
                )}
                <p>
                  Recorded as reviewed by <span className="font-semibold text-foreground">{name}</span>
                  {note.trim() ? <> with note “{note.trim()}”</> : null}. A reviewed proposal cannot be changed;
                  a different value later is a new proposal.
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className={buttonVariants({ variant: confirming === "reject" ? "destructive" : "default" })}
              disabled={busy}
              onClick={(event) => {
                event.preventDefault(); // keep the dialog open until the request settles
                submit();
              }}
            >
              {busy
                ? "Saving…"
                : confirming === "approve"
                  ? "Approve and write mapping"
                  : "Reject"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
