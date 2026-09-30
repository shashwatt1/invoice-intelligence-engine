import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { VendorRow } from "@/api/types";
import { StoreChip } from "@/components/shared/store-chip";
import { VendorIdentityPill } from "@/components/vendors/vendor-identity-pill";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConfirmVendor, useReopenVendor, useVendor } from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { roleLabel } from "@/lib/commercial";
import { formatDate, formatMoney } from "@/lib/format";

interface Props {
  vendor: VendorRow;
  onClose: () => void;
}

/**
 * The evidence a vendor identity decision rests on, and the decision.
 *
 * VENDOR IDENTITY → SOURCE EVIDENCE → DECISION HISTORY → DECISION. Confirming
 * names the canonical vendor and records who (the signed-in account), when
 * and why; it never merges vendors or changes what an invoice printed.
 * Deciding is MANAGER/ADMIN — the backend enforces this independently.
 */
export function VendorReviewPanel({ vendor, onClose }: Props) {
  const detail = useVendor(vendor.id);
  const confirm = useConfirmVendor();
  const reopen = useReopenVendor();
  const { user } = useAuth();
  const canDecide = user?.role === "MANAGER" || user?.role === "ADMIN";
  const confirmed = vendor.identity_status === "confirmed";

  const [canonical, setCanonical] = useState(vendor.display_name ?? vendor.name);
  const [basis, setBasis] = useState("");
  const basisGiven = basis.trim().length > 0;
  const data = detail.data;

  const onError = (error: unknown) => {
    toast.error(error instanceof ApiError ? error.userMessage : "The decision was not saved.");
  };

  const submitConfirm = () => {
    confirm.mutate(
      { id: vendor.id, displayName: canonical.trim(), basis: basis.trim() },
      { onSuccess: () => { toast.success("Vendor identity confirmed."); onClose(); }, onError },
    );
  };
  const submitReopen = () => {
    reopen.mutate(
      { id: vendor.id, basis: basis.trim() },
      { onSuccess: () => { toast.success("Vendor returned to unresolved."); onClose(); }, onError },
    );
  };

  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">{vendor.label}</AlertDialogTitle>
          <AlertDialogDescription className="flex items-center gap-2">
            <VendorIdentityPill status={vendor.identity_status} />
            <span>{vendor.invoices} invoice{vendor.invoices === 1 ? "" : "s"}</span>
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-6">
          <section className="space-y-2">
            <h3 className="text-sm font-medium">Vendor identity</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Canonical name</dt>
              <dd>{confirmed && vendor.display_name ? vendor.display_name : "Not confirmed"}</dd>
              <dt className="text-muted-foreground">First printed as</dt>
              <dd>{vendor.name}</dd>
              <dt className="text-muted-foreground">Tax id</dt>
              <dd className="font-mono text-xs">{vendor.tax_id ?? "—"}</dd>
            </dl>
            <p className="text-xs text-muted-foreground">
              The vendor is identified by its record, not by any name. Invoices keep the wording they printed.
            </p>
          </section>

          <section className="space-y-4 border-t pt-4">
            <h3 className="text-sm font-medium">Source evidence</h3>
            <div className="space-y-1">
              <h4 className="text-xs font-medium text-muted-foreground">Names printed on invoices</h4>
              {data?.observed_name_list.length ? (
                <ul className="space-y-0.5 text-sm" data-testid="observed-names">
                  {data.observed_name_list.map((observed) => (
                    <li key={observed.name} className="flex flex-wrap justify-between gap-x-4">
                      <span>{observed.name}</span>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {observed.invoices} invoice{observed.invoices === 1 ? "" : "s"}
                        {observed.last_seen_at ? ` · last ${formatDate(observed.last_seen_at)}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">{detail.isLoading ? "Loading…" : "No invoice wording on record."}</p>
              )}
            </div>
            {data?.observed_tax_ids.length ? (
              <div className="space-y-1">
                <h4 className="text-xs font-medium text-muted-foreground">Tax ids printed on invoices</h4>
                <ul className="space-y-0.5 text-sm">
                  {data.observed_tax_ids.map((t) => (
                    <li key={t.tax_id} className="flex justify-between gap-4">
                      <span className="font-mono text-xs">{t.tax_id}</span>
                      <span className="text-xs text-muted-foreground tabular-nums">{t.invoices} invoice{t.invoices === 1 ? "" : "s"}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {data && (data.address || data.phone || data.email) ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-sm">
                {data.address ? (<><dt className="text-muted-foreground">Address</dt><dd>{data.address}</dd></>) : null}
                {data.phone ? (<><dt className="text-muted-foreground">Phone</dt><dd>{data.phone}</dd></>) : null}
                {data.email ? (<><dt className="text-muted-foreground">Email</dt><dd>{data.email}</dd></>) : null}
              </dl>
            ) : null}
            {data?.recent_invoices.length ? (
              <div className="space-y-1">
                <h4 className="text-xs font-medium text-muted-foreground">Recent invoices</h4>
                <ul className="space-y-1 text-sm" data-testid="vendor-invoices">
                  {data.recent_invoices.map((invoice) => (
                    <li key={invoice.invoice_id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
                      <span className="tabular-nums">
                        {invoice.invoice_number ? `#${invoice.invoice_number}` : "(no number)"}
                        <span className="text-muted-foreground"> · {formatDate(invoice.invoice_date)}</span>
                      </span>
                      <span className="text-xs text-muted-foreground">printed “{invoice.observed_vendor_name ?? "—"}”</span>
                      <StoreChip store={invoice.store} compact link={false} />
                      <span className="tabular-nums">
                        {invoice.grand_total === null ? "—" : formatMoney(Number(invoice.grand_total))}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>

          {data?.discrepancies?.length ? (
            <section className="space-y-2 border-t pt-4" data-testid="vendor-discrepancies">
              <h3 className="text-sm font-medium">Reading discrepancies</h3>
              <p className="text-xs text-muted-foreground">
                Reprocessing read a different vendor on these invoices. They were kept on this confirmed vendor; review
                whether that is right.
              </p>
              <ul className="space-y-1 text-sm">
                {data.discrepancies.map((d, index) => (
                  <li key={index} className="flex flex-wrap justify-between gap-x-4">
                    <span className="tabular-nums">{d.invoice_number ? `#${d.invoice_number}` : "(no number)"}</span>
                    <span className="text-muted-foreground">read as “{d.observed_vendor_name ?? "no vendor"}”</span>
                    <span className="text-xs text-muted-foreground">{formatDate(d.recorded_at)}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {data?.history.length ? (
            <section className="space-y-2 border-t pt-4">
              <h3 className="text-sm font-medium">Decision history</h3>
              <ul className="space-y-1 text-sm text-muted-foreground">
                {data.history.map((entry, index) => (
                  <li key={index}>
                    {entry.decision === "CONFIRM" ? "confirmed" : "reopened"} by {entry.reviewer}
                    {entry.reviewer_role ? ` (${roleLabel(entry.reviewer_role)})` : ""} · {formatDate(entry.decided_at)}
                    {entry.new_display_name ? ` · as “${entry.new_display_name}”` : ""} · {entry.basis}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section className="space-y-3 border-t pt-4">
            <h3 className="text-sm font-medium">Decision</h3>
            {canDecide ? (
              <>
                {!confirmed && (
                  <div className="space-y-2">
                    <label className="text-sm font-medium" htmlFor="canonical">Canonical vendor name</label>
                    <Input id="canonical" value={canonical} onChange={(event) => setCanonical(event.target.value)} />
                  </div>
                )}
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="basis">Decision basis</label>
                  <Input
                    id="basis" value={basis} aria-required
                    onChange={(event) => setBasis(event.target.value)}
                    placeholder={confirmed ? "Why this vendor is reopened" : "What this confirmation rests on"}
                  />
                  <p className="text-xs text-muted-foreground">Required. Recorded with the decision.</p>
                </div>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm" data-testid="decision-maker">
                  <dt className="text-muted-foreground">Decision by</dt>
                  <dd>
                    <span className="font-medium">{user?.username ?? "—"}</span>
                    <span className="text-muted-foreground"> · {roleLabel(user?.role)}</span>
                    <div className="text-xs text-muted-foreground">Recorded from your signed-in account.</div>
                  </dd>
                </dl>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                Confirming a vendor&apos;s identity is a manager decision. You can see the same evidence here.
              </p>
            )}
          </section>

          <div className="sticky -bottom-5 -mx-5 -mb-5 flex gap-2 border-t bg-popover px-5 pt-3 pb-5">
            {canDecide && !confirmed && (
              <Button onClick={submitConfirm} disabled={confirm.isPending || !basisGiven || !canonical.trim()}>
                Confirm vendor identity
              </Button>
            )}
            {canDecide && confirmed && (
              <Button variant="outline" onClick={submitReopen} disabled={reopen.isPending || !basisGiven}>
                Reopen
              </Button>
            )}
            <Button variant="ghost" onClick={onClose} className="ml-auto">Close</Button>
          </div>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
