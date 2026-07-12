import JsonView from "@uiw/react-json-view";
import { Copy, FileJson, FileSpreadsheet, FileText } from "lucide-react";
import { toast } from "sonner";

import { invoiceExportUrl } from "@/api/endpoints";
import { ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useInvoiceExport } from "@/hooks/use-api";

/** JSON viewer theme mapped onto the app's design tokens. */
const jsonViewerTheme = {
  "--w-rjv-font-family": "ui-monospace, SFMono-Regular, Menlo, monospace",
  "--w-rjv-background-color": "transparent",
  "--w-rjv-color": "var(--foreground)",
  "--w-rjv-key-string": "var(--accent-foreground)",
  "--w-rjv-type-string-color": "var(--success)",
  "--w-rjv-type-int-color": "var(--info)",
  "--w-rjv-type-float-color": "var(--info)",
  "--w-rjv-type-boolean-color": "var(--warning)",
  "--w-rjv-type-null-color": "var(--muted-foreground)",
  "--w-rjv-line-color": "var(--border)",
  "--w-rjv-arrow-color": "var(--muted-foreground)",
  "--w-rjv-curlybraces-color": "var(--muted-foreground)",
  "--w-rjv-brackets-color": "var(--muted-foreground)",
  "--w-rjv-info-color": "var(--muted-foreground)",
  "--w-rjv-colon-color": "var(--muted-foreground)",
} as React.CSSProperties;

/**
 * Structured Output — the final validated invoice object (as persisted),
 * with copy + ERP-ready downloads. NOT the raw LLM response.
 */
export function StructuredOutput({
  invoiceId,
  enabled,
}: {
  invoiceId: string;
  enabled: boolean;
}) {
  const { data, isPending, isError, error, refetch } = useInvoiceExport(invoiceId, enabled);

  const copyJson = async () => {
    if (!data) return;
    await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    toast.success("Validated invoice JSON copied to clipboard");
  };

  if (isError) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={copyJson} disabled={!data}>
          <Copy className="size-3.5" /> Copy JSON
        </Button>
        <span className="mx-1 h-4 w-px bg-border" />
        <Button asChild variant="outline" size="sm">
          <a href={invoiceExportUrl(invoiceId, "json")} download>
            <FileJson className="size-3.5" /> Download JSON
          </a>
        </Button>
        <Button asChild variant="outline" size="sm">
          <a href={invoiceExportUrl(invoiceId, "txt")} download>
            <FileText className="size-3.5" /> Download TXT
          </a>
        </Button>
        <Button asChild variant="outline" size="sm">
          <a href={invoiceExportUrl(invoiceId, "csv")} download>
            <FileSpreadsheet className="size-3.5" /> Download CSV
          </a>
        </Button>
        <span className="ml-auto text-[0.7rem] text-muted-foreground">
          Final validated invoice — post-validation, as persisted
        </span>
      </div>

      {isPending || !data ? (
        <div className="space-y-2 rounded-lg border bg-secondary/40 p-3">
          <Skeleton className="h-4 w-2/5" />
          <Skeleton className="h-4 w-3/5" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ) : (
        <div className="max-h-96 overflow-auto rounded-lg border bg-secondary/40 p-3">
          <JsonView
            value={data}
            style={jsonViewerTheme}
            collapsed={2}
            displayDataTypes={false}
            shortenTextAfterLength={120}
          />
        </div>
      )}
    </div>
  );
}
