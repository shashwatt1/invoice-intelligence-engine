import { ClipboardCheck, Gauge, ListOrdered, MapPin, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";

import type { InvoiceDetail } from "@/api/types";
import { StatusPill } from "@/components/shared/status-badge";
import { StoreChip } from "@/components/shared/store-chip";
import { useAuth } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

function Row({ icon: Icon, label, children }: { icon: React.ElementType; label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 py-2.5">
      <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="t-eyebrow">{label}</div>
        <div className="mt-1 text-[0.8rem]">{children}</div>
      </div>
    </div>
  );
}

/**
 * The business-relevant half of what IntelligencePanel used to show in
 * one column: mapping status, store, EDI readiness, and (MANAGER+ only)
 * the master-data review state. Every role that can open this page sees
 * this panel — it carries none of the developer/technical fields
 * (confidence, validation checks, extraction model) IntelligencePanel
 * keeps, so it renders correctly even when the backend has redacted
 * those fields to null for MANAGER/USER.
 *
 * The `review` row is gated to MANAGER+ here (not by data absence):
 * showing "nothing raised" to a USER when `review` is simply hidden
 * from them (see app.api.v1.invoices._redact_invoice_detail) would be
 * misleading rather than accurate — a USER checks its own submissions
 * through GET /proposals instead.
 */
export function BusinessStatusPanel({ detail: d, className }: { detail: InvoiceDetail; className?: string }) {
  const { hasRole } = useAuth();
  const canReview = hasRole("MANAGER");
  const mapped = d.case_mappings.filter((r) => r.mapped).length;
  const unmapped = d.case_mappings.filter((r) => !r.mapped);

  return (
    <aside className={cn("surface sticky top-[4.5rem] divide-y px-4", className)} aria-label="Invoice status" data-testid="business-status-panel">
      <Row icon={ClipboardCheck} label="Master data">
        {d.store_pending || !d.store ? (
          <span className="text-warning">Waiting for a store before mappings apply.</span>
        ) : (
          <>
            <div className="flex items-center justify-between">
              <span className="tabular-nums"><span className="font-semibold">{mapped}</span>/{d.case_mappings.length} product mappings approved</span>
            </div>
            <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-3" role="meter" aria-valuenow={mapped} aria-valuemin={0} aria-valuemax={d.case_mappings.length} aria-label="Approved mappings">
              <div className={cn("h-full rounded-full", unmapped.length ? "bg-warning" : "bg-success")} style={{ width: d.case_mappings.length ? `${(mapped / d.case_mappings.length) * 100}%` : "0%" }} />
            </div>
            {d.review.pending ? (
              canReview ? (
                <div className="t-meta mt-1.5">
                  <Link
                    to={`/data-review?invoice=${d.invoice_id}${d.store ? `&store=${d.store.id}` : ""}`}
                    className="text-warning font-medium hover:underline"
                    data-testid="awaiting-master-data-approval"
                  >
                    Awaiting Master Data Approval ({d.review.pending})
                  </Link>
                </div>
              ) : null
            ) : unmapped.length ? (
              <div className="t-meta mt-1.5">{unmapped.length} product{unmapped.length === 1 ? "" : "s"} still need a units-per-case mapping</div>
            ) : null}
          </>
        )}
      </Row>

      <Row icon={MapPin} label="Store">
        <div className="flex flex-wrap items-center gap-1.5">
          <StoreChip store={d.store} />
        </div>
        {d.store?.address ? <div className="t-meta mt-1">{d.store.address}</div> : null}
        {d.store && d.store.identity_status !== "confirmed" ? (
          <div className="t-meta mt-1 flex items-center gap-1"><ShieldAlert className="size-3 text-warning" /> Store identity needs confirmation — mappings still apply to it.</div>
        ) : null}
      </Row>

      <Row icon={ListOrdered} label="EDI readiness">
        {d.pdi_export_allowed ? (
          d.pdi_export_requires_confirmation ? (
            <StatusPill tone="warning" label="Ready — review flagged" meaning="The PDI file can be generated; validation asked for a person to look first." />
          ) : (
            <StatusPill tone="success" label="Ready" meaning="Every gate is clear; the PDI file can be generated." />
          )
        ) : (
          <>
            <StatusPill tone="danger" label="Blocked" meaning={d.pdi_export_blocked_reason ?? undefined} />
            <div className="t-meta mt-1.5 leading-snug">{d.pdi_export_blocked_reason}</div>
          </>
        )}
      </Row>

      {canReview ? (
        <Row icon={Gauge} label="Review">
          <div className="t-meta">
            {d.review.status === "NONE" ? "No master-data proposals raised from this invoice." : `${d.review.approved} approved · ${d.review.pending} pending · ${d.review.rejected} rejected`}
          </div>
        </Row>
      ) : null}
    </aside>
  );
}
