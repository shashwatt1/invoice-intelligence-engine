import { Cloud, Database, ScanText, ShieldCheck, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

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

export function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Settings"
        description="Platform configuration. Editable settings arrive with the review-workflow phase."
        actions={<Badge variant="secondary">Read-only</Badge>}
      />
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle className="text-[0.95rem]">Processing configuration</CardTitle>
        </CardHeader>
        <CardContent className="divide-y">
          {CONFIG_ROWS.map(({ icon: Icon, label, value, hint }) => (
            <div key={label} className="flex items-center gap-3.5 py-3 first:pt-0 last:pb-0">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-secondary">
                <Icon className="size-4 text-muted-foreground" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[0.83rem] font-medium">{label}</div>
                <div className="text-[0.75rem] text-muted-foreground">{hint}</div>
              </div>
              <div className="text-[0.83rem] font-semibold whitespace-nowrap">{value}</div>
            </div>
          ))}
        </CardContent>
      </Card>
      <p className="mt-4 text-[0.75rem] text-muted-foreground">
        Values are managed through backend environment variables (<code>.env</code>). This page
        becomes editable when multi-tenant configuration ships.
      </p>
    </>
  );
}
