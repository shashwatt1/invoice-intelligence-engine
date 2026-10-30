import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useStartStoreAtZero } from "@/hooks/use-api";
import { todayIso } from "@/lib/inventory";

/**
 * Declares that a store starts tracking at zero on a date — an administrator's statement, NOT a physical
 * count. It writes one baseline (who, when, why) and nothing else: no movement, no receipt, and no change
 * to a product that has its own count. Asking again for a store that already has one changes nothing.
 */
export function ZeroStartDialog({ storeId, storeLabel, onClose }: { storeId: string; storeLabel: string; onClose: () => void }) {
  const start = useStartStoreAtZero();
  const [date, setDate] = useState(todayIso());
  const [reason, setReason] = useState("");
  const ready = reason.trim() !== "" && date !== "" && !start.isPending;

  const submit = () => start.mutate(
    { storeId, body: { reason: reason.trim(), initialization_date: date } },
    {
      onSuccess: (result) => {
        toast.success(result.created ? `${storeLabel} now starts at zero.` : `${storeLabel} already had a zero start; nothing changed.`);
        onClose();
      },
      onError: (error) => toast.error(error instanceof ApiError ? error.userMessage : "Nothing was recorded."),
    },
  );

  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-w-lg" data-testid="zero-start-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>Start {storeLabel} at zero</AlertDialogTitle>
          <AlertDialogDescription>
            Use this only when the store genuinely began with no stock. It is a declaration, not a physical count:
            the store is shown as &ldquo;Zero start&rdquo;, and products without their own opening count start at
            zero on this date. Existing counts, receipts and balances are not changed.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-3 text-sm">
          <label className="block">
            <span className="mb-1 block text-xs font-medium">Start date</span>
            <Input type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} disabled={start.isPending} />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium">Why <span className="text-muted-foreground">(required, kept for good)</span></span>
            <Input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} disabled={start.isPending}
                   placeholder="e.g. new store, opened with no stock on hand" />
          </label>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" size="sm" onClick={onClose} disabled={start.isPending}>Cancel</Button>
          <Button size="sm" onClick={submit} disabled={!ready} data-testid="zero-start-confirm">
            {start.isPending ? "Saving…" : "Start at zero"}
          </Button>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
