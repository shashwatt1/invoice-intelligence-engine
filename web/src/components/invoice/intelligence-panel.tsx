import { CheckCircle2, FileScan } from "lucide-react";

import type { InvoiceDetail } from "@/api/types";
import { ConfidenceMeter } from "@/components/shared/confidence-meter";
import { StatusBadge, StatusPill } from "@/components/shared/status-badge";
import { formatPercent } from "@/lib/format";

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
 * What the backend knows about HOW this invoice was extracted and
 * validated: confidence, the check-by-check validation report, and
 * extraction stats. ADMIN-only — every field this panel reads
 * (composite_confidence, validation_report, llm_metadata, ocr_text,
 * corrected_fields at the header level) is redacted to null/empty for
 * MANAGER/USER by the backend (see
 * app.api.v1.invoices._redact_invoice_detail), and the caller
 * (invoice-detail.tsx) only mounts this panel for ADMIN. Business status
 * that every role needs — mapping progress, store, EDI readiness — lives
 * in BusinessStatusPanel instead, which is never redacted away.
 */
export function IntelligencePanel({ detail: d }: { detail: InvoiceDetail }) {
  const report = d.validation_report;
  const failed = report?.checks.filter((c) => c.status === "FAILED") ?? [];
  const warnings = report?.checks.filter((c) => c.status === "WARNING") ?? [];
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

      <Row icon={FileScan} label="Extraction">
        <div className="t-meta space-y-0.5 tabular-nums">
          <div>{d.photos.length > 1 ? `${d.photos.length} photos combined` : d.source_type === "digital_pdf" ? "digital PDF text" : "photo read by OCR"} · {d.line_items.length} line{d.line_items.length === 1 ? "" : "s"} extracted</div>
          {d.llm_metadata?.prompt_version ? <div>prompt {d.llm_metadata.prompt_version} · model, tokens and timing in the developer panel</div> : null}
          {corrected ? <div className="text-foreground">{corrected} manual correction{corrected === 1 ? "" : "s"} on record</div> : null}
        </div>
      </Row>
    </aside>
  );
}
