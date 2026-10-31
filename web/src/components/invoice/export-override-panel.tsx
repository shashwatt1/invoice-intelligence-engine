import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { invoiceReviewCopyUrl } from "@/api/endpoints";
import type { InvoiceDetail } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useExportOverride } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { formatDateTime } from "@/lib/format";

/** The same floor the server enforces; the server's answer is the one that counts. */
const MIN_OVERRIDE_REASON = 10;

/**
 * Export block and administrator override, shown to anyone who can export (MANAGER+).
 *
 * While completeness or financial reconciliation fails, no PDI, TXT or CSV file is produced. Two things are
 * offered: a labelled review copy (TXT/CSV, "not for import" inside the file and in its name) for investigating,
 * and — for an administrator only — an audited override with a required reason. An override changes nothing
 * about the invoice: it stays REVIEW_REQUIRED with the same failed checks, and cannot be marked Retailed. It
 * stops applying the moment the invoice changes. The server decides all of this; this only shows its answer.
 */
export function ExportOverridePanel({ detail }: { detail: InvoiceDetail }) {
  const { hasRole } = useAuth();
  const mutation = useExportOverride(detail.invoice_id);
  const [dialog, setDialog] = useState<"grant" | "revoke" | null>(null);
  const [reason, setReason] = useState("");
  const blockers = detail.blocking_checks ?? [];
  const override = detail.export_override ?? null;
  const active = override?.state === "ACTIVE" && detail.pdi_export_overridden === true;
  if (!hasRole("MANAGER")) return null;
  if (blockers.length === 0 && !override) return null;

  const isAdmin = hasRole("ADMIN");
  const ready = reason.trim().length >= MIN_OVERRIDE_REASON;
  const close = () => { setDialog(null); setReason(""); };
  const submit = () => {
    if (!ready || mutation.isPending) return;
    const action = dialog === "revoke" && override
      ? { kind: "revoke" as const, overrideId: override.id, reason: reason.trim() }
      : { kind: "grant" as const, reason: reason.trim() };
    mutation.mutate(action, {
      onSuccess: () => {
        toast.success(action.kind === "grant" ? "Export override recorded. The invoice itself is unchanged."
                                              : "Export override withdrawn.");
        close();
      },
      onError: (error) => toast.error(error instanceof Error ? error.message : "Nothing was changed."),
    });
  };

  return (
    <section
      className={`mb-4 rounded-md border p-3 text-[0.82rem] ${active ? "border-warning/50 bg-warning/10" : "border-destructive/40 bg-destructive/5"}`}
      aria-labelledby="export-override-heading" data-testid="export-override-panel"
    >
      <h2 id="export-override-heading" className="flex items-center gap-1.5 font-semibold">
        <ShieldAlert className="size-4" aria-hidden />
        {active ? "Export block overridden by an administrator" : blockers.length > 0 ? "Export blocked" : "Export override no longer applies"}
      </h2>

      {blockers.length > 0 ? (
        <p className="mt-1" data-testid="override-checks">
          Critical checks failing: <span className="font-mono">{blockers.join(", ")}</span>.{" "}
          {active
            ? "They still fail and the invoice is still not validated; the override only allows the files to be produced."
            : "No PDI, TXT or CSV file is produced until they pass."}
        </p>
      ) : null}

      {override ? (
        <p className="mt-1 text-muted-foreground" data-testid="override-detail">
          {override.state === "ACTIVE" ? "Override" : override.state === "STALE" ? "Earlier override (the invoice changed afterwards, so it no longer applies)" : "Earlier override (revoked)"}
          {" "}by {override.granted_by}, {formatDateTime(override.created_at)}: “{override.reason}”
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {blockers.length > 0 ? (
          <>
            <Button asChild size="sm" variant="outline">
              <a href={invoiceReviewCopyUrl(detail.invoice_id, "txt")} data-testid="review-copy-txt">Review copy (TXT, not for import)</a>
            </Button>
            <Button asChild size="sm" variant="outline">
              <a href={invoiceReviewCopyUrl(detail.invoice_id, "csv")} data-testid="review-copy-csv">Review copy (CSV, not for import)</a>
            </Button>
          </>
        ) : null}
        {isAdmin && blockers.length > 0 && !active ? (
          <Button size="sm" variant="destructive" onClick={() => setDialog("grant")} data-testid="override-open">
            Override export block…
          </Button>
        ) : null}
        {isAdmin && active ? (
          <Button size="sm" variant="outline" onClick={() => setDialog("revoke")} data-testid="override-revoke-open">
            Revoke override…
          </Button>
        ) : null}
      </div>

      {dialog ? (
        <div className="mt-3 space-y-2 border-t pt-3" role="group" aria-label={dialog === "grant" ? "Override export block" : "Revoke override"}>
          <p className="text-[0.78rem]">
            {dialog === "grant"
              ? "This allows PDI, TXT and CSV files to be produced for this invoice exactly as it stands. The failed checks are kept on the record and the invoice stays unvalidated. Any change to the invoice cancels the override."
              : "The export block applies again immediately."}
          </p>
          <label htmlFor="override-reason" className="block text-[0.75rem] font-medium">
            Reason <span className="text-muted-foreground">(required, at least {MIN_OVERRIDE_REASON} characters; kept in the audit trail)</span>
          </label>
          <textarea
            id="override-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} rows={3}
            disabled={mutation.isPending}
            className="w-full rounded-md border bg-background px-2 py-1.5 text-[0.8rem]"
            placeholder="e.g. Vendor confirmed by phone that the missing row is a credit that is not on this copy"
          />
          <div className="flex gap-2">
            <Button size="sm" variant={dialog === "grant" ? "destructive" : "default"} onClick={submit}
                    disabled={!ready || mutation.isPending} data-testid="override-submit">
              {mutation.isPending ? "Saving…" : dialog === "grant" ? "Record override" : "Revoke override"}
            </Button>
            <Button size="sm" variant="ghost" onClick={close} disabled={mutation.isPending}>Cancel</Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
