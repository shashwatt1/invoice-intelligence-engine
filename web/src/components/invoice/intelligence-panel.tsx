import { CheckCircle2, ClipboardCheck, FileScan, Gauge, ListOrdered, MapPin, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";

import type { InvoiceDetail } from "@/api/types";
import { ConfidenceMeter } from "@/components/shared/confidence-meter";
import { StatusBadge, StatusPill } from "@/components/shared/status-badge";
import { StoreChip } from "@/components/shared/store-chip";
import { formatDuration, formatPercent, formatTokens } from "@/lib/format";
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
 * What the backend actually knows about this invoice, in one column:
 * validation, confidence, what still needs a person, mapping and store
 * state, and whether an EDI can be produced. Nothing here is generated
 * prose — every line is a recorded value.
 */
export function IntelligencePanel({ detail: d }: { detail: InvoiceDetail }) {
  const report = d.validation_report;
  const failed = report?.checks.filter((c) => c.status === "FAILED") ?? [];
  const warnings = report?.checks.filter((c) => c.status === "WARNING") ?? [];
  const mapped = d.case_mappings.filter((r) => r.mapped).length;
  const unmapped = d.case_mappings.filter((r) => !r.mapped);
  const corrected = d.line_items.filter((i) => i.corrected_fields.length || i.entry_source === "manual").length + d.corrected_fields.length;

  return (
    <aside className="surface sticky top-[4.5rem] divide-y px-4" aria-label="Invoice intelligence" data-testid="intelligence-panel">
      <div className="py-3">
        <div className="flex items-center justify-between gap-2">
          <span className="t-section">Intelligence</span>
          <StatusBadge status={d.status} />
        </div>
        <div className="mt-3">
          <ConfidenceMeter score={d.composite_confidence} />
          {report ? (
            <div className="t-meta mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 tabular-nums">
              <span>OCR {formatPercent(report.confidence.ocr_confidence, 0)}</span>
              <span>model {formatPercent(report.confidence.ai_confidence, 0)}</span>
              <span>checks {formatPercent(report.confidence.validation_score, 0)}</span>
            </div>
          ) : null}
        </div>
      </div>

      <Row icon={CheckCircle2} label="Validation">
        {report ? (
          <>
            <div className="flex flex-wrap gap-1.5">
              <StatusPill size="xs" tone="success" label={`${report.summary.passed} passed`} />
              {report.summary.failed ? <StatusPill size="xs" tone="danger" label={`${report.summary.failed} failed`} /> : null}
              {report.summary.warnings ? <StatusPill size="xs" tone="warning" label={`${report.summary.warnings} warning${report.summary.warnings === 1 ? "" : "s"}`} /> : null}
              {report.summary.skipped ? <StatusPill size="xs" tone="neutral" label={`${report.summary.skipped} skipped`} /> : null}
            </div>
            {failed.length ? (
              <ul className="mt-2 space-y-1">
                {failed.map((c, i) => (
                  <li key={`${c.name}-${i}`} className="rounded-md bg-danger-soft/70 px-2 py-1.5 text-[0.74rem] leading-snug">
                    <span className="font-mono text-[0.68rem] font-semibold text-danger">{c.name}</span>
                    {c.field ? <span className="ml-1 font-mono text-[0.66rem] text-muted-foreground">{c.field}</span> : null}
                    <div className="mt-0.5 text-foreground/80">{c.message}</div>
                    {c.expected !== null && c.expected !== undefined ? (
                      <div className="t-meta mt-0.5 tabular-nums">expected {c.expected} · got {c.actual}</div>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
            {warnings.length && !failed.length ? (
              <ul className="mt-2 space-y-1">
                {warnings.map((c, i) => (
                  <li key={`${c.name}-${i}`} className="rounded-md bg-warning-soft/70 px-2 py-1.5 text-[0.74rem] leading-snug">
                    <span className="font-mono text-[0.68rem] font-semibold text-warning">{c.name}</span>
                    <div className="mt-0.5 text-foreground/80">{c.message}</div>
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <span className="t-meta">No validation report recorded.</span>
        )}
      </Row>

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
              <div className="t-meta mt-1.5"><Link to={`/data-review?invoice=${d.invoice_id}`} className="text-warning hover:underline">{d.review.pending} awaiting approval in Master Data Review</Link></div>
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

      <Row icon={FileScan} label="Extraction">
        <div className="t-meta space-y-0.5 tabular-nums">
          <div>{d.photos.length > 1 ? `${d.photos.length} photos combined` : d.source_type === "digital_pdf" ? "digital PDF text" : "OCR"}{d.ocr_text ? ` · ${formatTokens(d.ocr_text.length)} chars` : ""}</div>
          {d.llm_metadata ? <div>{d.llm_metadata.model} · {formatTokens(d.llm_metadata.total_tokens)} tokens · {formatDuration(d.llm_metadata.latency_ms)}</div> : null}
          {corrected ? <div className="text-foreground">{corrected} manual correction{corrected === 1 ? "" : "s"} on record</div> : null}
        </div>
      </Row>

      <Row icon={Gauge} label="Review">
        <div className="t-meta">
          {d.review.status === "NONE" ? "No master-data proposals raised from this invoice." : `${d.review.approved} approved · ${d.review.pending} pending · ${d.review.rejected} rejected`}
        </div>
      </Row>
    </aside>
  );
}
