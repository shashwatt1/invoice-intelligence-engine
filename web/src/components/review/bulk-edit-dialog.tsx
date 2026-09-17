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
import { Input } from "@/components/ui/input";
import { useReviseProposal } from "@/hooks/use-api";
import { isEditable, parseUnits } from "@/lib/proposals";
import { rememberedReviewer, rememberReviewer } from "@/lib/reviewer";

/**
 * Edit the proposed value of several selected proposals — one input per
 * row, each keeping its own value. There is deliberately no "apply this
 * value to all": units-per-case is product-specific, and a reviewer who
 * wants the same number on ten rows can type it ten times.
 *
 * Saving revises only the rows that changed. Each revision is its own
 * request: a new pending proposal replaces the original in the queue, and
 * the original is frozen as superseded. Nothing authoritative changes here;
 * the revised rows still have to be approved.
 */
export function BulkEditDialog({
  open,
  rows,
  onOpenChange,
  onDone,
}: {
  open: boolean;
  rows: ProposalRow[];
  onOpenChange: (open: boolean) => void;
  /** Maps each superseded id to the id of the revision that replaced it. */
  onDone: (replaced: Record<string, string>) => void;
}) {
  const revise = useReviseProposal();
  const [reviewer, setReviewer] = useState(rememberedReviewer);
  const [note, setNote] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setDrafts(Object.fromEntries(rows.map((row) => [row.id, String(row.proposed_value)])));
      setErrors({});
      setNote("");
    }
    // Rows are captured when the dialog opens; edits are against that snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const name = reviewer.trim();
  const editable = rows.filter(isEditable);
  const changed = editable.filter((row) => {
    const value = parseUnits(drafts[row.id] ?? "");
    return value !== null && value !== Number(row.proposed_value);
  });
  const invalid = editable.filter((row) => parseUnits(drafts[row.id] ?? "") === null);

  const submit = async () => {
    if (!name || saving || changed.length === 0 || invalid.length > 0) return;
    setSaving(true);
    const replaced: Record<string, string> = {};
    const failed: Record<string, string> = {};
    for (const row of changed) {
      try {
        const result = await revise.mutateAsync({
          proposalId: row.id,
          revision: { proposed_value: parseUnits(drafts[row.id])!, proposed_by: name, note: note.trim() || null },
        });
        replaced[row.id] = result.proposal.id;
      } catch (error) {
        failed[row.id] = error instanceof Error ? error.message : "Revision failed.";
      }
    }
    setSaving(false);
    rememberReviewer(name);
    setErrors(failed);
    onDone(replaced);
    const saved = Object.keys(replaced).length;
    const lost = Object.keys(failed).length;
    if (lost === 0) {
      onOpenChange(false);
      toast.success(`Revised ${saved} value${saved === 1 ? "" : "s"} — still pending approval.`);
    } else {
      toast.error(`${saved} revised, ${lost} failed. The failed rows are marked below.`);
    }
  };

  return (
    <AlertDialog open={open} onOpenChange={(next) => !next && !saving && onOpenChange(false)}>
      <AlertDialogContent className="sm:max-w-xl" data-testid="bulk-edit-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>
            Edit {editable.length} selected value{editable.length === 1 ? "" : "s"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            Each row keeps its own value; change only the ones that need it. A change is a new
            proposal under your name — the original is frozen as superseded — and it still needs
            approval before it becomes master data.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
          {editable.map((row) => {
            const draft = drafts[row.id] ?? "";
            const value = parseUnits(draft);
            const isChanged = value !== null && value !== Number(row.proposed_value);
            return (
              <div
                key={row.id}
                className="grid grid-cols-[minmax(0,1fr)_auto_5.5rem] items-center gap-2 rounded-md border px-2.5 py-1.5"
                data-testid="bulk-edit-row"
              >
                <div className="min-w-0">
                  <div className="font-mono text-[0.78rem] font-medium">{row.entity_key}</div>
                  <div className="truncate text-[0.68rem] text-muted-foreground">
                    {row.store.label} · {row.field.replace(/_/g, " ")}
                    {errors[row.id] ? <span className="text-danger"> · {errors[row.id]}</span> : null}
                  </div>
                </div>
                <span className="text-[0.72rem] text-muted-foreground tabular-nums">
                  {isChanged ? (
                    <>
                      {String(row.proposed_value)} → <span className="font-semibold text-foreground">{value}</span>
                    </>
                  ) : (
                    `proposed ${String(row.proposed_value)}`
                  )}
                </span>
                <Input
                  aria-label={`Units per case for ${row.entity_key}`}
                  value={draft}
                  onChange={(event) => setDrafts((prev) => ({ ...prev, [row.id]: event.target.value }))}
                  inputMode="numeric"
                  className="h-7 text-right font-mono tabular-nums"
                  aria-invalid={value === null}
                  disabled={saving}
                />
              </div>
            );
          })}
          {rows.length > editable.length ? (
            <p className="px-1 text-[0.7rem] text-muted-foreground">
              {rows.length - editable.length} selected row{rows.length - editable.length === 1 ? " is" : "s are"} not
              editable (already decided, or a field this table cannot edit).
            </p>
          ) : null}
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <div className="space-y-1">
            <label htmlFor="edit-reviewer" className="text-[0.75rem] font-medium">
              Changed by <span className="text-danger">*</span>
            </label>
            <Input
              id="edit-reviewer"
              value={reviewer}
              onChange={(event) => setReviewer(event.target.value)}
              placeholder="e.g. data-team:shashwat"
              disabled={saving}
              autoComplete="off"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="edit-note" className="text-[0.75rem] font-medium">
              Note <span className="text-muted-foreground">(optional)</span>
            </label>
            <Input
              id="edit-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="e.g. printed package says 15"
              disabled={saving}
              maxLength={2000}
            />
          </div>
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={saving}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: "default" })}
            disabled={!name || saving || changed.length === 0 || invalid.length > 0}
            onClick={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            {saving
              ? "Saving…"
              : invalid.length > 0
                ? "Fix invalid values"
                : `Save ${changed.length} change${changed.length === 1 ? "" : "s"}`}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
