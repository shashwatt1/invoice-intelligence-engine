import { ChevronDown, Terminal } from "lucide-react";
import { useState } from "react";

import type { InvoiceDetail } from "@/api/types";
import { StructuredOutput } from "@/components/invoice/structured-output";
import { Card, CardContent } from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-secondary/40 px-3 py-2">
      <div className="text-[0.65rem] font-semibold tracking-wider text-muted-foreground uppercase">
        {label}
      </div>
      <div className="mt-0.5 truncate text-[0.85rem] font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto rounded-lg border bg-secondary/40 p-3 font-mono text-[0.72rem] leading-relaxed">
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}

/**
 * Developer panel — collapsed by default. Everything an engineer needs
 * to debug one extraction without touching the terminal.
 */
export function DeveloperPanel({ detail }: { detail: InvoiceDetail }) {
  const [open, setOpen] = useState(false);
  const llm = detail.llm_metadata;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className="gap-0 p-0">
        <CollapsibleTrigger className="flex w-full items-center gap-2.5 px-5 py-3.5 text-left">
          <Terminal className="size-4 text-muted-foreground" />
          <span className="flex-1 text-[0.88rem] font-semibold">Developer panel</span>
          <span className="text-[0.72rem] text-muted-foreground">
            {open ? "Hide" : "OCR · prompts · tokens · raw JSON"}
          </span>
          <ChevronDown
            className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")}
          />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <CardContent className="border-t px-5 pt-4 pb-5">
            <div className="mb-4 grid grid-cols-6 gap-2 max-lg:grid-cols-3 max-sm:grid-cols-2">
              <Metric label="Model" value={llm?.model ?? "—"} />
              <Metric label="Prompt" value={llm?.prompt_version ?? "—"} />
              <Metric label="Latency" value={formatDuration(llm?.latency_ms)} />
              <Metric
                label="Tokens in / out"
                value={
                  llm ? `${llm.input_tokens.toLocaleString()} / ${llm.output_tokens.toLocaleString()}` : "—"
                }
              />
              <Metric
                label="Est. cost"
                value={
                  llm?.estimated_cost_usd != null ? `$${llm.estimated_cost_usd.toFixed(6)}` : "—"
                }
              />
              <Metric label="Finish" value={llm?.finish_reason ?? "—"} />
            </div>

            <Tabs defaultValue="structured">
              <TabsList>
                <TabsTrigger value="structured">Structured output</TabsTrigger>
                <TabsTrigger value="ocr">OCR text</TabsTrigger>
                <TabsTrigger value="raw">Raw structured output</TabsTrigger>
                <TabsTrigger value="llm">LLM metadata</TabsTrigger>
                <TabsTrigger value="validation">Validation metadata</TabsTrigger>
                <TabsTrigger value="ids">Database IDs</TabsTrigger>
              </TabsList>
              <TabsContent value="structured">
                <StructuredOutput invoiceId={detail.invoice_id} enabled={open} />
              </TabsContent>
              <TabsContent value="ocr">
                <JsonBlock value={detail.ocr_text ?? "(no text stored)"} />
              </TabsContent>
              <TabsContent value="raw">
                <JsonBlock value={detail.raw_extraction ?? {}} />
              </TabsContent>
              <TabsContent value="llm">
                <JsonBlock value={llm ?? {}} />
              </TabsContent>
              <TabsContent value="validation">
                <JsonBlock value={detail.validation_report ?? {}} />
              </TabsContent>
              <TabsContent value="ids">
                <JsonBlock
                  value={{
                    invoice_id: detail.invoice_id,
                    document_id: detail.document_id,
                    vendor_id: detail.vendor?.id ?? null,
                    extraction_model: detail.extraction_model,
                    source_type: detail.source_type,
                  }}
                />
              </TabsContent>
            </Tabs>
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}
