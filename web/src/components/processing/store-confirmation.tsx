import { CheckCircle2, MapPin, Search } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type { DocumentStatusData, StoreDirectoryEntry } from "@/api/types";
import { StoreChip } from "@/components/shared/store-chip";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useConfirmDocumentStore } from "@/hooks/use-api";
import { cn } from "@/lib/utils";

/**
 * The run is paused: text is extracted, and a person has to say which
 * store the document is for. What identification found is shown with
 * its evidence — a candidate is offered, never applied — and any store
 * from the directory can be chosen instead.
 */
export function StoreConfirmation({
  status,
  stores,
}: {
  status: DocumentStatusData;
  stores: StoreDirectoryEntry[];
}) {
  const confirm = useConfirmDocumentStore(status.document_id);
  const candidates = status.store_candidates;
  const chosenUpFront = status.store;
  const [selected, setSelected] = useState<string>(
    candidates.length === 1 ? candidates[0].store_id : "",
  );
  const [confirmedBy, setConfirmedBy] = useState("");

  const conflict = chosenUpFront && candidates.length > 0 &&
    !candidates.some((c) => c.store_id === chosenUpFront.id);

  const submit = () => {
    if (!selected) return;
    confirm.mutate(
      { storeId: selected, confirmedBy: confirmedBy.trim() || null },
      {
        onSuccess: (data) => toast.success(`Store confirmed: ${data.store?.label ?? "store"}. Processing continues.`),
        onError: (error) => toast.error(error instanceof Error ? error.message : "Could not confirm the store."),
      },
    );
  };

  return (
    <div className="border-warning/50 bg-warning-soft/40 rounded-lg border p-3.5 text-[0.82rem]" data-testid="store-confirmation">
      <div className="text-warning flex items-center gap-2 font-semibold">
        <MapPin className="size-4" /> Which store is this document for?
      </div>
      <p className="mt-0.5 text-muted-foreground">
        {conflict
          ? "The document names a different store than the one chosen at upload. Decide which is right; nothing has been processed under either."
          : candidates.length === 0
            ? "Nothing on the document matched a known store exactly. Choose the store — the system will not guess."
            : candidates.length === 1
              ? "The document names one store. Confirm it, or choose another."
              : "The document names more than one store. Choose which."}
      </p>

      {chosenUpFront ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[0.75rem]">
          <span className="text-muted-foreground">Chosen at upload:</span>
          <StoreChip store={chosenUpFront} link={false} />
          {conflict ? <span className="text-danger font-medium">— contradicted by the document</span> : null}
        </div>
      ) : null}

      {candidates.length > 0 ? (
        <ul className="mt-3 space-y-2">
          {candidates.map((c) => (
            <li key={c.store_id}>
              <button
                type="button"
                onClick={() => setSelected(c.store_id)}
                className={cn(
                  "w-full rounded-md border bg-card px-3 py-2 text-left transition-colors",
                  selected === c.store_id ? "border-primary ring-2 ring-primary/30" : "hover:bg-secondary",
                )}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn("font-semibold", c.identity_status !== "confirmed" && "font-mono")}>{c.label}</span>
                  {c.identity_status !== "confirmed" ? (
                    <span className="bg-warning-soft text-warning rounded px-1.5 py-0.5 text-[0.68rem] font-medium">
                      location not confirmed
                    </span>
                  ) : null}
                  {c.address ? <span className="text-[0.72rem] text-muted-foreground">{c.address}</span> : null}
                  {selected === c.store_id ? <CheckCircle2 className="ml-auto size-4 text-primary" /> : null}
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {c.matched_on.map((m, i) => (
                    <span
                      key={i}
                      className={cn(
                        "rounded border px-1.5 py-0.5 font-mono text-[0.68rem]",
                        m.verified ? "text-foreground" : "border-warning/40 text-warning",
                      )}
                      title={m.verified ? "A verified identifier" : "Evidence observed on documents, not yet verified by a person"}
                    >
                      {m.kind}: {m.value}{m.verified ? "" : " (unverified)"}
                    </span>
                  ))}
                </div>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-1">
          <label className="text-[0.72rem] font-medium">
            {candidates.length ? "Or choose any store" : "Store"} <span className="text-danger">*</span>
          </label>
          <Select value={selected} onValueChange={setSelected} disabled={confirm.isPending}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select a store from the directory" />
            </SelectTrigger>
            <SelectContent>
              {stores.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.label}
                  {s.address ? <span className="ml-2 text-[0.72rem] text-muted-foreground">{s.address}</span> : null}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <label htmlFor="confirmed-by" className="text-[0.72rem] font-medium">Confirmed by</label>
          <Input
            id="confirmed-by"
            value={confirmedBy}
            onChange={(event) => setConfirmedBy(event.target.value)}
            placeholder="your name (recorded on the document)"
            disabled={confirm.isPending}
          />
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" disabled={!selected || confirm.isPending} onClick={submit}>
          <Search className="size-3.5" />
          {confirm.isPending ? "Confirming…" : "Confirm store and continue"}
        </Button>
        <span className="text-[0.7rem] text-muted-foreground">
          Recorded on the document. Structuring, validation and persistence run for this store only.
        </span>
      </div>
    </div>
  );
}
