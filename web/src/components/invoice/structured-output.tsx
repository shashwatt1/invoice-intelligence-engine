import JsonView from "@uiw/react-json-view";
import {
  ChevronDown,
  Copy,
  Download,
  FileJson,
  FileSpreadsheet,
  FileText,
  Search,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { invoiceExportUrl } from "@/api/endpoints";
import { ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
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

type Json = string | number | boolean | null | Json[] | { [key: string]: Json };

/**
 * Prune a JSON tree to the branches whose key or value matches the query
 * (case-insensitive). Parents of a match are kept so paths stay readable.
 */
function filterJson(value: Json, query: string): Json | undefined {
  const needle = query.toLowerCase();
  const matches = (candidate: unknown) =>
    String(candidate).toLowerCase().includes(needle);

  if (Array.isArray(value)) {
    const kept = value
      .map((entry) => filterJson(entry, query))
      .filter((entry): entry is Json => entry !== undefined);
    return kept.length > 0 ? kept : undefined;
  }
  if (value !== null && typeof value === "object") {
    const result: { [key: string]: Json } = {};
    for (const [key, entry] of Object.entries(value)) {
      if (key.toLowerCase().includes(needle)) {
        result[key] = entry;
        continue;
      }
      const child = filterJson(entry, query);
      if (child !== undefined) result[key] = child;
    }
    return Object.keys(result).length > 0 ? result : undefined;
  }
  return matches(value) ? value : undefined;
}

/**
 * Structured Extraction — the final validated invoice object (as
 * persisted), with search, copy, and ERP-ready downloads behind one
 * Export dropdown. NOT the raw LLM response.
 */
export function StructuredOutput({
  invoiceId,
  enabled,
}: {
  invoiceId: string;
  enabled: boolean;
}) {
  const { data, isPending, isError, error, refetch } = useInvoiceExport(invoiceId, enabled);
  const [query, setQuery] = useState("");

  const shown = useMemo(() => {
    if (!data) return undefined;
    if (!query.trim()) return data;
    return (filterJson(data as Json, query.trim()) ?? {}) as object;
  }, [data, query]);

  const copyJson = async () => {
    if (!data) return;
    await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    toast.success("Validated invoice JSON copied to clipboard");
  };

  const download = (format: "json" | "txt" | "csv") => {
    // Anchor-free download keeps the dropdown item semantics simple.
    window.location.assign(invoiceExportUrl(invoiceId, format));
  };

  if (isError) {
    return <ErrorState error={error} onRetry={() => void refetch()} />;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-56 flex-1">
          <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search keys and values…"
            className="h-8 pl-8 text-[0.8rem]"
          />
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" disabled={!data}>
              <Download className="size-3.5" /> Export
              <ChevronDown className="size-3.5 text-muted-foreground" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => download("json")}>
              <FileJson className="size-3.5" /> Download JSON
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => download("txt")}>
              <FileText className="size-3.5" /> Download TXT
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => download("csv")}>
              <FileSpreadsheet className="size-3.5" /> Download CSV
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => void copyJson()}>
              <Copy className="size-3.5" /> Copy JSON
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
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
          {query.trim() && Object.keys(shown as object).length === 0 ? (
            <p className="px-1 py-2 text-[0.8rem] text-muted-foreground">
              No keys or values match “{query.trim()}”.
            </p>
          ) : (
            <JsonView
              value={shown as object}
              style={jsonViewerTheme}
              collapsed={query.trim() ? false : 2}
              displayDataTypes={false}
              shortenTextAfterLength={120}
            />
          )}
        </div>
      )}
      <p className="text-[0.7rem] text-muted-foreground">
        Final validated invoice — post-validation, as persisted. This object is the canonical
        source for the TXT/CSV exports and future ERP integrations.
      </p>
    </div>
  );
}
