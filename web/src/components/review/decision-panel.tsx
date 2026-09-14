import { Check, X } from "lucide-react";
import { useState } from "react";
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
import { useDecideProposal } from "@/hooks/use-api";

const REVIEWER_KEY = "data-review.reviewer";

function rememberedReviewer(): string {
  try {
    return localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

/**
 * Approve / Reject for one PENDING proposal.
 *
 * There is no login. The reviewer types their name and it is recorded on
 * the proposal exactly as the CLI's --by would record it — a name on the
 * record, not a proof of identity. Each decision is confirmed in a dialog
 * that restates what approving will write, because approval is the one
 * action in this UI that changes master data.
 */
export function DecisionPanel({ proposal }: { proposal: ProposalDetail }) {
  const decide = useDecideProposal();
  const [reviewer, setReviewer] = useState(rememberedReviewer);
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);

  const name = reviewer.trim();
  const proposed = String(proposal.proposed_value);
  const current = proposal.current_master_value;

  const submit = () => {
    if (!confirming || !name) return;
    const action = confirming;
    decide.mutate(
      { proposalId: proposal.id, action, decision: { reviewed_by: name, note: note.trim() || null } },
      {
        onSuccess: (result) => {
          try {
            localStorage.setItem(REVIEWER_KEY, name);
          } catch {
            /* per-browser convenience only */
          }
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
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div className="space-y-1">
          <label htmlFor="reviewer" className="text-[0.75rem] font-medium">
            Reviewed by <span className="text-danger">*</span>
          </label>
          <Input
            id="reviewer"
            value={reviewer}
            onChange={(event) => setReviewer(event.target.value)}
            placeholder="e.g. data-team:shashwat"
            disabled={decide.isPending}
            autoComplete="off"
          />
          <p className="text-[0.68rem] text-muted-foreground">
            Recorded as typed. No login yet — this is the same contract as the CLI's <code>--by</code>.
          </p>
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
            disabled={decide.isPending}
            maxLength={2000}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={!name || decide.isPending}
          onClick={() => setConfirming("approve")}
        >
          <Check className="size-3.5" strokeWidth={3} /> Approve
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!name || decide.isPending}
          onClick={() => setConfirming("reject")}
        >
          <X className="size-3.5" /> Reject
        </Button>
        {!name ? (
          <span className="text-[0.72rem] text-muted-foreground">Enter a reviewer name to decide.</span>
        ) : null}
      </div>

      <AlertDialog open={confirming !== null} onOpenChange={(open) => !open && !decide.isPending && setConfirming(null)}>
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
                      This writes <span className="font-semibold text-foreground">{proposed} {proposal.field.replace(/_/g, " ")}</span> for
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
            <AlertDialogCancel disabled={decide.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className={buttonVariants({ variant: confirming === "reject" ? "destructive" : "default" })}
              disabled={decide.isPending}
              onClick={(event) => {
                event.preventDefault(); // keep the dialog open until the request settles
                submit();
              }}
            >
              {decide.isPending
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
