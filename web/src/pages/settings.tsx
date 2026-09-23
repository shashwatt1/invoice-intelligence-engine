import { Activity, Cloud, Database, ScanText, ShieldCheck, Sparkles } from "lucide-react";
import type { ReactNode } from "react";

import { PageHeader, SectionHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard } from "@/hooks/use-api";
import { formatDuration, formatTokens } from "@/lib/format";

const CONFIG_ROWS = [
  {
    icon: ScanText,
    label: "OCR provider",
    value: "Google Vision",
    hint: "Digital PDFs bypass OCR via pdfplumber",
  },
  {
    icon: Sparkles,
    label: "LLM provider",
    value: "OpenAI · gpt-4o-mini",
    hint: "Structured outputs, versioned prompts",
  },
  {
    icon: ShieldCheck,
    label: "Review threshold",
    value: "85% composite confidence",
    hint: "Below threshold routes to manual review",
  },
  {
    icon: Database,
    label: "Rounding tolerance",
    value: "±0.02",
    hint: "Applied to all mathematical validation checks",
  },
  {
    icon: Cloud,
    label: "Upload limits",
    value: "PDF · PNG · JPEG, max 25 MB",
    hint: "SHA-256 duplicate detection on ingest",
  },
];

function Row({ label, hint, value }: { label: string; hint: string; value: ReactNode }) {
  return (
    <div className="flex items-center gap-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="text-[0.83rem] font-medium">{label}</div>
        <div className="text-[0.75rem] text-muted-foreground">{hint}</div>
      </div>
      <div className="text-[0.83rem] font-semibold whitespace-nowrap tabular-nums">{value}</div>
    </div>
  );
}

/**
 * Read-only configuration and the pipeline's own telemetry. The
 * technical numbers live here on purpose: an operator's dashboard answers
 * what needs doing; this page answers how the machinery is running.
 */
export function SettingsPage() {
  const diagnostics = useDashboard();

  return (
    <>
      <PageHeader
        title="Settings"
        description="Platform configuration and processing diagnostics."
        actions={<Badge variant="secondary">Read-only</Badge>}
      />

      <div className="grid max-w-5xl grid-cols-2 items-start gap-6 max-lg:grid-cols-1">
        <section className="surface overflow-hidden">
          <SectionHeader title="Processing configuration" description="Managed through backend environment variables" />
          <div className="divide-y border-t px-5">
            {CONFIG_ROWS.map(({ icon: Icon, label, value, hint }) => (
              <div key={label} className="flex items-center gap-3.5 py-3">
                <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-surface-3">
                  <Icon className="size-4 text-muted-foreground" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-[0.83rem] font-medium">{label}</div>
                  <div className="text-[0.75rem] text-muted-foreground">{hint}</div>
                </div>
                <div className="text-[0.83rem] font-semibold whitespace-nowrap">{value}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="surface overflow-hidden" data-testid="diagnostics">
          <SectionHeader
            title={<span className="inline-flex items-center gap-2"><Activity className="size-4 text-muted-foreground" aria-hidden /> Diagnostics</span>}
            description="Aggregate telemetry across every processed document"
          />
          <div className="divide-y border-t px-5">
            {diagnostics.isPending ? (
              <div className="space-y-3 py-3">
                <Skeleton className="h-9" />
                <Skeleton className="h-9" />
                <Skeleton className="h-9" />
              </div>
            ) : diagnostics.isError || !diagnostics.data ? (
              <p className="py-3 text-[0.78rem] text-muted-foreground">Telemetry is unavailable while the API is unreachable.</p>
            ) : (
              <>
                <Row label="Average processing time" hint="Upload to persisted, per document" value={formatDuration(diagnostics.data.average_processing_ms)} />
                <Row label="Extraction tokens" hint="Prompt and completion tokens, all documents" value={formatTokens(diagnostics.data.total_tokens)} />
                <Row label="Estimated AI cost" hint="Summed from each extraction's per-model token pricing" value={`$${diagnostics.data.total_estimated_cost_usd.toFixed(4)}`} />
                <Row label="Average confidence" hint="Composite extraction confidence" value={diagnostics.data.average_confidence !== null ? `${(diagnostics.data.average_confidence * 100).toFixed(1)}%` : "—"} />
              </>
            )}
          </div>
        </section>
      </div>

      <p className="mt-4 text-[0.75rem] text-muted-foreground">
        Values are managed through backend environment variables (<code>.env</code>). This page
        becomes editable when multi-tenant configuration ships.
      </p>
    </>
  );
}
