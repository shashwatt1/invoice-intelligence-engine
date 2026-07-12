import { Check, X } from "lucide-react";

import type { DatabaseConfirmation } from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

function Flag({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 py-1">
      <span
        className={cn(
          "flex size-4.5 items-center justify-center rounded-full",
          ok ? "bg-success-soft text-success" : "bg-danger-soft text-danger",
        )}
      >
        {ok ? <Check className="size-3" strokeWidth={3} /> : <X className="size-3" strokeWidth={3} />}
      </span>
      <span className="text-[0.82rem]">{label}</span>
    </div>
  );
}

/** Proof of persistence — what this run actually wrote to PostgreSQL. */
export function DatabaseConfirmationCard({ database }: { database: DatabaseConfirmation }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-[0.95rem]">Database persistence</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-x-4 max-sm:grid-cols-1">
          <Flag ok={database.vendor_saved} label="Vendor saved" />
          <Flag ok={database.invoice_saved} label="Invoice saved" />
          <Flag
            ok={database.items_saved > 0}
            label={`Line items saved (${database.items_saved})`}
          />
          <Flag ok={database.logs_saved > 0} label={`Processing logs (${database.logs_saved})`} />
          <Flag ok={database.duplicate_check_passed} label="Duplicate check passed" />
        </div>
        <p className="mt-2 border-t pt-2.5 text-[0.75rem] text-muted-foreground">
          Total pipeline duration:{" "}
          <span className="font-semibold text-foreground tabular-nums">
            {formatDuration(database.processing_duration_ms)}
          </span>
        </p>
      </CardContent>
    </Card>
  );
}
