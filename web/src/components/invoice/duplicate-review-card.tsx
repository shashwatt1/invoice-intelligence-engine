import { Copy, Split } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { InvoiceDetail, LineItem } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useDecideDuplicate } from "@/hooks/use-api";
import { formatMoney } from "@/lib/format";
import { rememberedReviewer, rememberReviewer } from "@/lib/reviewer";

function RowFacts({ item, label }: { item: LineItem; label: string }) {
  return (
    <div className="rounded-md border px-3 py-2 text-[0.78rem]">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-semibold">{label}</span>
        <span className="text-[0.68rem] text-muted-foreground">
          row {item.sort_order}
          {item.source_pages.length ? ` · photo ${item.source_pages.join(", ")}` : ""}
        </span>
      </div>
      <div className="font-medium">{item.description}</div>
      <div className="mt-0.5 grid grid-cols-3 gap-x-3 text-muted-foreground tabular-nums">
        <span>qty {item.quantity}</span>
        <span>price {item.unit_price !== null ? formatMoney(item.unit_price) : "—"}</span>
        <span>total {item.line_total !== null ? formatMoney(item.line_total) : "—"}</span>
      </div>
    </div>
  );
}

/**
 * Possible duplicates from overlapping photos, one pair at a time.
 *
 * The model kept both rows and said why it could not decide; nothing
 * was merged. A person decides: the flagged row is the earlier row seen
 * again (kept for audit, typed duplicate, out of totals and EDI) or a
 * legitimate second row (the flag is cleared). Validation runs again
 * either way, against the printed totals, and no quantity is changed.
 */
export function DuplicateReviewCard({ detail }: { detail: InvoiceDetail }) {
  const decide = useDecideDuplicate(detail.invoice_id);
  const [reviewer, setReviewer] = useState(rememberedReviewer);
  const [note, setNote] = useState("");
  const name = reviewer.trim();

  const bySort = new Map(detail.line_items.map((item) => [item.sort_order, item]));
  const pending = detail.line_items.filter(
    (item) => item.duplicate_candidate && !item.duplicate_candidate.resolution,
  );
  const decided = detail.line_items.filter((item) => item.duplicate_candidate?.resolution);
  if (pending.length === 0 && decided.length === 0) return null;

  const submit = (item: LineItem, decision: "same_row" | "separate_rows") => {
    if (!name) return;
    decide.mutate(
      { sortOrder: item.sort_order, decision: { decision, decided_by: name, note: note.trim() || null } },
      {
        onSuccess: (result) => {
          rememberReviewer(name);
          setNote("");
          toast.success(
            decision === "same_row"
              ? `Row ${item.sort_order} recorded as the same row as ${result.of_sort_order} — now ${result.status.replace("_", " ").toLowerCase()}.`
              : `Rows ${result.of_sort_order} and ${item.sort_order} kept as separate rows — now ${result.status.replace("_", " ").toLowerCase()}.`,
          );
          if (result.status === "REVIEW_REQUIRED") {
            toast.warning(`Still needs review: ${result.review_reasons[0] ?? "see the validation report"}`);
          }
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "Decision failed."),
      },
    );
  };

  return (
    <Card className={pending.length ? "border-warning/50" : undefined} data-testid="duplicate-review">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-[0.95rem]">
          <Copy className="size-4" /> Possible duplicates across photos
          {pending.length ? (
            <Badge variant="secondary" className="bg-warning-soft text-warning">
              {pending.length} to decide
            </Badge>
          ) : (
            <Badge variant="secondary">{decided.length} decided</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {pending.length ? (
          <p className="text-[0.78rem] text-muted-foreground">
            The invoice was read from overlapping photos. For each pair below the model could not tell
            whether it saw one physical row twice or two real rows, so it kept both and stopped. Decide
            from the invoice; the totals are re-checked afterwards and no quantity is ever changed to
            make them fit.
          </p>
        ) : null}
        {pending.length ? (
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-1">
              <label htmlFor="dup-reviewer" className="text-[0.75rem] font-medium">
                Decided by <span className="text-danger">*</span>
              </label>
              <Input id="dup-reviewer" value={reviewer} onChange={(e) => setReviewer(e.target.value)}
                     placeholder="e.g. data-team:shashwat" autoComplete="off" disabled={decide.isPending} />
            </div>
            <div className="space-y-1">
              <label htmlFor="dup-note" className="text-[0.75rem] font-medium">
                Note <span className="text-muted-foreground">(optional)</span>
              </label>
              <Input id="dup-note" value={note} onChange={(e) => setNote(e.target.value)}
                     placeholder="e.g. photo 3 starts where photo 2 ended" maxLength={2000} disabled={decide.isPending} />
            </div>
          </div>
        ) : null}

        {pending.map((item) => {
          const other = bySort.get(item.duplicate_candidate!.of_sort_order);
          return (
            <div key={item.sort_order} className="space-y-2 rounded-lg border border-warning/40 bg-warning-soft/30 p-3" data-testid="duplicate-pair">
              <div className="grid gap-2 sm:grid-cols-2">
                {other ? <RowFacts item={other} label="Earlier row" /> : null}
                <RowFacts item={item} label="Flagged row" />
              </div>
              {item.duplicate_candidate!.reason ? (
                <p className="text-[0.75rem] text-muted-foreground">
                  Model: “{item.duplicate_candidate!.reason}”
                </p>
              ) : null}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={!name || decide.isPending} onClick={() => submit(item, "same_row")}>
                  <Copy className="size-3.5" /> Same row seen twice — count once
                </Button>
                <Button size="sm" variant="outline" disabled={!name || decide.isPending} onClick={() => submit(item, "separate_rows")}>
                  <Split className="size-3.5" /> Two separate rows — keep both
                </Button>
                {!name ? <span className="self-center text-[0.72rem] text-muted-foreground">Enter your name to decide.</span> : null}
              </div>
            </div>
          );
        })}

        {decided.length ? (
          <ul className="space-y-1 text-[0.75rem] text-muted-foreground">
            {decided.map((item) => (
              <li key={item.sort_order}>
                Row {item.sort_order} vs row {item.duplicate_candidate!.of_sort_order}:{" "}
                <span className="font-medium text-foreground">
                  {item.duplicate_candidate!.resolution === "same_row" ? "same row, counted once" : "separate rows, both kept"}
                </span>{" "}
                — {item.duplicate_candidate!.decided_by}
                {item.duplicate_candidate!.note ? ` · “${item.duplicate_candidate!.note}”` : ""}
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}
