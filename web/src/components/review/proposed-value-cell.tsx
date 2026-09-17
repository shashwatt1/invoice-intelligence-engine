import { Check, Pencil, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { ProposalRow } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useReviseProposal } from "@/hooks/use-api";
import { isEditable, parseUnits } from "@/lib/proposals";
import { rememberedReviewer, rememberReviewer } from "@/lib/reviewer";

/**
 * The Proposed column: a number, and for a pending units-per-case row a
 * pencil that turns it into an input. Saving revises the proposal — a new
 * pending proposal under the reviewer's name replaces this row, the
 * original is frozen as superseded — so the table never edits history and
 * never touches master data. The revision still needs approval.
 */
export function ProposedValueCell({
  row,
  onRevised,
}: {
  row: ProposalRow;
  onRevised: (supersededId: string, revisionId: string) => void;
}) {
  const revise = useReviseProposal();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [reviewer, setReviewer] = useState(rememberedReviewer);

  const stop = (event: React.SyntheticEvent) => event.stopPropagation();
  const name = reviewer.trim();
  const value = parseUnits(draft);
  const unchanged = value === Number(row.proposed_value);

  const begin = () => {
    setDraft(String(row.proposed_value));
    setReviewer(rememberedReviewer());
    setEditing(true);
  };

  const save = () => {
    if (value === null || unchanged || !name || revise.isPending) return;
    revise.mutate(
      { proposalId: row.id, revision: { proposed_value: value, proposed_by: name } },
      {
        onSuccess: (result) => {
          rememberReviewer(name);
          setEditing(false);
          onRevised(row.id, result.proposal.id);
          toast.success(
            `${row.entity_key}: ${String(row.proposed_value)} → ${value}. New proposal pending approval.`,
          );
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "Revision failed."),
      },
    );
  };

  if (!editing) {
    return (
      <span className="inline-flex items-center justify-end gap-1">
        <span className="font-semibold tabular-nums">{String(row.proposed_value)}</span>
        {isEditable(row) ? (
          <Button
            variant="ghost"
            size="icon-xs"
            className="text-muted-foreground"
            aria-label={`Edit proposed value for ${row.entity_key}`}
            onClick={(event) => {
              stop(event);
              begin();
            }}
          >
            <Pencil />
          </Button>
        ) : null}
      </span>
    );
  }

  return (
    <span
      className="inline-flex items-center justify-end gap-1"
      onClick={stop}
      onKeyDown={(event) => {
        stop(event);
        if (event.key === "Escape") setEditing(false);
        if (event.key === "Enter") save();
      }}
      data-testid="inline-edit"
    >
      {!rememberedReviewer() ? (
        <Input
          aria-label="Changed by"
          value={reviewer}
          onChange={(event) => setReviewer(event.target.value)}
          placeholder="your name"
          className="h-7 w-32 text-[0.75rem]"
          disabled={revise.isPending}
        />
      ) : null}
      <Input
        aria-label={`New units per case for ${row.entity_key}`}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        inputMode="numeric"
        autoFocus
        className="h-7 w-16 text-right font-mono tabular-nums"
        aria-invalid={value === null}
        disabled={revise.isPending}
      />
      <Button
        size="icon-xs"
        aria-label="Save"
        disabled={value === null || unchanged || !name || revise.isPending}
        onClick={save}
      >
        <Check strokeWidth={3} />
      </Button>
      <Button size="icon-xs" variant="ghost" aria-label="Cancel" disabled={revise.isPending} onClick={() => setEditing(false)}>
        <X />
      </Button>
    </span>
  );
}
