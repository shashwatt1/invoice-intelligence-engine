import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { CrossStoreDifference } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useCrossStoreDifferences, useResolveCrossStoreDifference } from "@/hooks/use-api";

/**
 * Items whose approved units-per-case disagree between stores, or with the
 * shared value — the places where approved data conflicts (for example one
 * store holding 18 and another 1 for the same C-18 case). Nothing is chosen
 * automatically: with Product Master resolution on, an affected line is a
 * conflict until a reviewer settles it here.
 *
 * Settling sets one shared value. Each store either adopts it (its own mapping
 * is changed through an approved, audited proposal) or is kept as a recorded
 * store override. A note is required and is kept with every record written.
 */
export function CrossStoreDifferences() {
  const differences = useCrossStoreDifferences();
  const rows = differences.data ?? [];
  if (differences.isPending || rows.length === 0) return null;
  return (
    <section className="mb-4 rounded-md border border-warning/40 bg-warning/5 p-3" data-testid="cross-store-differences"
             aria-labelledby="differences-heading">
      <h2 id="differences-heading" className="flex items-center gap-1.5 text-[0.9rem] font-semibold">
        <AlertTriangle className="size-4 text-warning" aria-hidden />
        {rows.length} item{rows.length === 1 ? "" : "s"} with different approved values across stores
      </h2>
      <p className="mt-0.5 text-[0.75rem] text-muted-foreground">
        Settle each one: choose the value every store uses, and keep a store&apos;s own value only where it
        genuinely sells the item differently.
      </p>
      <ul className="mt-2 space-y-2">
        {rows.map((d) => <DifferenceRow key={d.item_code} difference={d} />)}
      </ul>
    </section>
  );
}

function DifferenceRow({ difference }: { difference: CrossStoreDifference }) {
  const resolve = useResolveCrossStoreDifference();
  const [open, setOpen] = useState(false);
  const [units, setUnits] = useState<number | null>(difference.shared_units ?? null);
  const [keep, setKeep] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");
  const differing = difference.stores.filter((s) => units !== null && s.units !== units);
  const canSave = units !== null && note.trim().length > 0 && !resolve.isPending;

  const save = () => {
    if (!canSave || units === null) return;
    resolve.mutate(
      { itemCode: difference.item_code,
        body: { units, keep_store_ids: [...keep].filter((id) => differing.some((s) => s.store_id === id)),
                note: note.trim() } },
      {
        onSuccess: (result) => {
          setOpen(false);
          toast.success(`${difference.item_code}: ${result.units} for every store — ${result.adopted_store_ids.length} adopted, `
            + `${result.kept_store_ids.length} kept as override${result.kept_store_ids.length === 1 ? "" : "s"}.`);
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "Nothing was changed."),
      },
    );
  };

  return (
    <li className="rounded border bg-background px-3 py-2 text-[0.8rem]" data-testid="difference-row">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono">{difference.item_code}</span>
        <span>
          {difference.stores.map((s) => `${s.store}: ${s.units}`).join(" · ")}
          {difference.shared_units !== null ? ` · shared value: ${difference.shared_units}` : ""}
        </span>
        {difference.overrides.length > 0 ? (
          <span className="text-muted-foreground">
            overrides: {difference.overrides.map((s) => `${s.store} ${s.units}`).join(", ")}
          </span>
        ) : null}
        <Button size="sm" variant="outline" className="ml-auto h-7" onClick={() => setOpen((v) => !v)}
                aria-expanded={open}>
          {open ? "Close" : "Settle"}
        </Button>
      </div>
      {open ? (
        <div className="mt-2 space-y-2 border-t pt-2">
          <fieldset className="flex flex-wrap items-center gap-3">
            <legend className="text-[0.75rem] font-medium">Value for every store</legend>
            {difference.distinct_values.map((v) => (
              <label key={v} className="flex items-center gap-1">
                <input type="radio" name={`units-${difference.item_code}`} checked={units === v}
                       onChange={() => setUnits(v)} disabled={resolve.isPending} />
                {v}
              </label>
            ))}
          </fieldset>
          {differing.length > 0 ? (
            <div className="space-y-1">
              <span className="text-[0.75rem] font-medium">Stores that hold a different value</span>
              {differing.map((s) => (
                <label key={s.store_id} className="flex items-center gap-2">
                  <Checkbox checked={keep.has(s.store_id)} disabled={resolve.isPending}
                            aria-label={`Keep ${s.store}'s value ${s.units}`}
                            onCheckedChange={(v) => setKeep((prev) => {
                              const next = new Set(prev);
                              if (v === true) next.add(s.store_id); else next.delete(s.store_id);
                              return next;
                            })} />
                  Keep {s.store}&apos;s {s.units} as a store override
                  <span className="text-muted-foreground">(otherwise it adopts {units})</span>
                </label>
              ))}
            </div>
          ) : null}
          <div className="space-y-1">
            <label htmlFor={`note-${difference.item_code}`} className="text-[0.75rem] font-medium">
              Why this value <span className="text-muted-foreground">(required, kept with every record)</span>
            </label>
            <Input id={`note-${difference.item_code}`} value={note} onChange={(e) => setNote(e.target.value)}
                   maxLength={1000} placeholder="e.g. distributor sheet: the 18-pack is one PDI item"
                   disabled={resolve.isPending} />
          </div>
          <Button size="sm" onClick={save} disabled={!canSave} data-testid="difference-save">
            {resolve.isPending ? "Saving…" : "Settle"}
          </Button>
        </div>
      ) : null}
    </li>
  );
}
