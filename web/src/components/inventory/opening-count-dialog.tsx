import { useMemo, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { InventoryItemRow } from "@/api/types";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useInventoryItems, useRecordOpeningCounts } from "@/hooks/use-api";
import { parseCountLines, todayIso } from "@/lib/inventory";

/**
 * Audited opening stock: a physical count, in sellable units, for the date
 * it was taken, with who counted and how. One per product per store — the
 * server refuses a second; a recount is a later adjustment. A product left
 * blank is simply not counted yet and keeps "Opening stock required". The
 * POS inventory figure is never offered as a starting point.
 */
export function OpeningCountDialog({ storeId, storeLabel, onClose }: { storeId: string; storeLabel: string; onClose: () => void }) {
  const awaiting = useInventoryItems(storeId, { status: "OPENING_STOCK_REQUIRED", page: 1, page_size: 500 });
  const record = useRecordOpeningCounts();
  const [countDate, setCountDate] = useState(todayIso());
  const [reason, setReason] = useState("");
  const [counts, setCounts] = useState<Record<string, string>>({});
  const [extra, setExtra] = useState("");
  const [serverProblems, setServerProblems] = useState<Record<string, string>>({});

  const listed: InventoryItemRow[] = awaiting.data?.items ?? [];
  const parsedExtra = useMemo(() => parseCountLines(extra), [extra]);
  const listedEntries = listed
    .filter((row) => (counts[row.item_code] ?? "").trim() !== "")
    .map((row) => ({ item_code: row.item_code, quantity: Number(counts[row.item_code]), description: row.description }));
  const badListed = listedEntries.filter((e) => !Number.isInteger(e.quantity) || e.quantity < 0).map((e) => e.item_code);
  const entries = [...listedEntries, ...parsedExtra.entries];
  const ready = entries.length > 0 && badListed.length === 0 && parsedExtra.errors.length === 0 && reason.trim() !== "" && countDate !== "";

  const submit = () => {
    setServerProblems({});
    record.mutate(
      { storeId, body: { count_date: countDate, reason: reason.trim(), entries } },
      {
        onSuccess: (result) => { toast.success(`${result.recorded} opening count${result.recorded === 1 ? "" : "s"} recorded.`); onClose(); },
        onError: (error) => {
          const detail = error instanceof ApiError ? (error.detail as { entries?: Record<string, string> } | null) : null;
          if (detail?.entries) setServerProblems(detail.entries);
          toast.error(error instanceof ApiError ? error.userMessage : "Nothing was recorded.");
        },
      },
    );
  };

  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">Record opening counts · {storeLabel}</AlertDialogTitle>
          <AlertDialogDescription>
            Enter what was physically counted, in sellable units (cans, bottles, packs as sold). The count is the
            closing stock of its date: receipts on or before that date are treated as already counted.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-4 text-sm">
          <div className="grid gap-3 sm:grid-cols-[180px_1fr]">
            <label className="space-y-1">
              <span className="text-xs font-medium text-muted-foreground">Count date</span>
              <Input type="date" value={countDate} max={todayIso()} onChange={(e) => setCountDate(e.target.value)} aria-label="Count date" />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-muted-foreground">How it was counted (required)</span>
              <Input value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)}
                     placeholder="e.g. full shelf and cooler count by two people" aria-label="How it was counted" />
            </label>
          </div>

          {listed.length ? (
            <section className="space-y-1.5">
              <h3 className="text-xs font-medium text-muted-foreground">
                Products at this store without an opening count ({awaiting.data?.total ?? listed.length})
              </h3>
              <ul className="max-h-[40vh] divide-y overflow-y-auto rounded-md border">
                {listed.map((row) => (
                  <li key={row.item_code} className="flex items-center justify-between gap-3 px-3 py-1.5">
                    <span className="min-w-0">
                      <span className="block truncate">{row.description ?? row.item_code}</span>
                      <span className="font-mono text-xs text-muted-foreground">{row.item_code}</span>
                      {serverProblems[row.item_code] ? <span className="block text-xs text-danger">{serverProblems[row.item_code]}</span> : null}
                    </span>
                    <Input type="number" min={0} step={1} inputMode="numeric" className="w-24"
                           aria-label={`Counted units for ${row.description ?? row.item_code}`}
                           aria-invalid={badListed.includes(row.item_code) || undefined}
                           value={counts[row.item_code] ?? ""}
                           onChange={(e) => setCounts((prev) => ({ ...prev, [row.item_code]: e.target.value }))} />
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted-foreground">Other products — one per line: item code, quantity</span>
            <textarea
              className="min-h-[80px] w-full rounded-md border bg-transparent px-3 py-2 font-mono text-xs"
              value={extra} onChange={(e) => setExtra(e.target.value)} aria-label="Other products"
              placeholder={"01820053030, 48\n08769283102, 12"}
            />
          </label>
          {parsedExtra.errors.map((e) => <p key={e} className="text-xs text-danger">{e}</p>)}
          {Object.entries(serverProblems).filter(([code]) => !listed.some((r) => r.item_code === code)).map(([code, why]) => (
            <p key={code} className="text-xs text-danger">{code}: {why}</p>
          ))}

          <p className="text-xs text-muted-foreground">
            {entries.length} product{entries.length === 1 ? "" : "s"} counted. Recorded as you, all or nothing; a product
            can have one opening count.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} disabled={record.isPending}>Cancel</Button>
            <Button onClick={submit} disabled={!ready || record.isPending} data-testid="record-counts">
              {record.isPending ? "Recording…" : "Record counts"}
            </Button>
          </div>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
