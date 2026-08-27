import { Check, Pencil, PackageSearch, TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import type { CaseMappingConfirmation, CaseMappingRow, SuggestionSource } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useConfirmCaseMappings } from "@/hooks/use-api";

/**
 * Says where a prefilled number came from, so the operator can weigh it.
 *
 * A value scraped out of a description is weaker evidence than one from a
 * dedicated pack column, and a form like "4/6/16OZ" is weaker still: four
 * six-packs could be 4 units per case or 24, and only the store knows
 * which it sells. This previously rendered the pack column unconditionally,
 * which showed an empty pair of quotes on every vendor that prints no such
 * column — telling the operator nothing at all.
 */
function SuggestionNote({ row }: { row: CaseMappingRow }) {
  // A confirmed mapping is the value actually used, whatever the document
  // says — so it reports the database even when nothing was printed.
  if (row.suggestion_source === "database") {
    return (
      <span className="text-[0.7rem] text-muted-foreground">
        confirmed — reused on every future invoice
      </span>
    );
  }
  if (row.suggested_units_per_case === null) {
    return (
      <span className="text-[0.7rem] text-muted-foreground">
        no pack information printed — confirm from the product
      </span>
    );
  }
  if (row.suggestion_source === "description_ambiguous") {
    return (
      <span className="text-warning text-[0.7rem]">
        “{row.pack_size ?? row.description}” could be
        {row.suggestion_candidates.map((n, i) => (
          <span key={n}>
            {i === 0 ? " " : " or "}
            <span className="font-semibold">{n}</span>
          </span>
        ))}
        {" "}— enter the value this store sells by
      </span>
    );
  }
  const source: Partial<Record<SuggestionSource, string>> = {
    database: "from the mapping database",
    pack_size: `from pack size “${row.pack_size ?? ""}”`,
    description: `read from the description`,
  };
  return (
    <span className="text-[0.7rem] text-muted-foreground">
      {(row.suggestion_source && source[row.suggestion_source]) ?? "suggested"}
    </span>
  );
}

/**
 * Case → unit mapping review.
 *
 * PDI reads units-per-case out of the EDI and multiplies it by its own
 * item retail to get Case Retail, so a wrong value silently corrupts
 * pricing in PDI. The mapping table is therefore the single source of
 * truth: a value only ever reaches the EDI after a person confirms it,
 * and it is stored against the UPC so the same product is never asked
 * about again on any later invoice.
 *
 * Everything the document offers — a pack column, or the N/M notation in
 * a description — is a prefilled suggestion and nothing more. Nothing is
 * ever confirmed automatically, and an unknown product is never silently
 * defaulted to 1.
 */
export function CaseMappingCard({
  invoiceId,
  rows,
}: {
  invoiceId: string;
  rows: CaseMappingRow[];
}) {
  // Lines with no usable product code cannot be keyed to a mapping at
  // all (the backend's export gate skips them for the same reason), so
  // showing them here would ask for something that cannot be saved.
  const mappable = useMemo(() => rows.filter((row) => row.item_code !== null), [rows]);
  const pending = useMemo(() => mappable.filter((row) => !row.mapped), [mappable]);

  // Keyed by item_code so a product appearing on several lines is one entry.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // Correcting an already-confirmed mapping is a separate, one-product
  // action: a saved value is reused on every future invoice, so it must
  // never be changed as a side effect of the bulk confirm below.
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const confirm = useConfirmCaseMappings(invoiceId);
  const update = useConfirmCaseMappings(invoiceId);

  if (mappable.length === 0) return null;

  // An ambiguous description is deliberately NOT prefilled. Prefilling
  // makes "Confirm & Save" a single click that can persist a wrong value
  // against the UPC forever — which is exactly how a real invoice ended
  // up in PDI with "Units Per Case 1" on every product.
  const draftFor = (row: CaseMappingRow) =>
    drafts[row.item_code!] ??
    (row.suggestion_source === "description_ambiguous"
      ? ""
      : (row.suggested_units_per_case?.toString() ?? ""));

  const parsed = (value: string) => {
    const units = Number(value);
    return Number.isInteger(units) && units >= 1 && units <= 9999 ? units : null;
  };

  const ready: CaseMappingConfirmation[] = pending
    .map((row) => ({ row, units: parsed(draftFor(row)) }))
    .filter(({ units }) => units !== null)
    .map(({ row, units }) => ({
      item_code: row.item_code!,
      units_per_case: units!,
      description: row.description,
    }));

  const startEditing = (row: CaseMappingRow) => {
    setEditing(row.item_code);
    setEditDraft(row.units_per_case?.toString() ?? "");
  };

  const saveEdit = (row: CaseMappingRow) => {
    const units = parsed(editDraft);
    if (units === null) return;
    update.mutate(
      [{ item_code: row.item_code!, units_per_case: units, description: row.description }],
      {
        onSuccess: () => {
          setEditing(null);
          toast.success(
            `Updated to ${units} units/case. Future invoices with this UPC will use it.`,
          );
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Failed to update the mapping.");
        },
      },
    );
  };

  const save = () => {
    confirm.mutate(ready, {
      onSuccess: (result) => {
        setDrafts({});
        toast.success(
          `Saved ${result.saved} case mapping${result.saved === 1 ? "" : "s"}.` +
            (result.pdi_export_allowed ? " PDI export is now available." : ""),
        );
      },
      onError: (error) => {
        toast.error(error instanceof Error ? error.message : "Failed to save case mappings.");
      },
    });
  };

  return (
    <Card className="gap-0 p-0" data-testid="case-mapping-card">
      <CardHeader className="px-5 py-4">
        <CardTitle className="flex items-center gap-2 text-[0.95rem]">
          <PackageSearch className="size-4" /> Case → unit mapping
          {pending.length > 0 ? (
            <span className="text-warning text-[0.75rem] font-medium">
              {pending.length} product{pending.length === 1 ? "" : "s"} need
              {pending.length === 1 ? "s" : ""} confirmation
            </span>
          ) : (
            <span className="text-success text-[0.75rem] font-medium">
              All products mapped
            </span>
          )}
        </CardTitle>
        <p className="text-[0.75rem] text-muted-foreground">
          {pending.length > 0
            ? "PDI multiplies units per case by its own item retail, so this is confirmed once per product and reused on every future invoice."
            : "Confirmed previously — these values come from the mapping database, not from the document."}
        </p>
      </CardHeader>
      <CardContent className="px-2 pb-2">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>Product</TableHead>
              <TableHead>UPC / item code</TableHead>
              <TableHead>On document</TableHead>
              <TableHead className="w-52">Units per case</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {mappable.map((row) => (
              <TableRow key={row.item_code}>
                <TableCell className="max-w-72 truncate font-medium">
                  {row.description ?? "—"}
                </TableCell>
                <TableCell className="font-mono text-[0.78rem] tabular-nums">
                  {row.item_code}
                </TableCell>
                <TableCell className="text-[0.78rem] text-muted-foreground">
                  {row.pack_size ?? "—"}
                </TableCell>
                <TableCell>
                  {row.mapped && editing === row.item_code ? (
                    <div className="flex items-center gap-1.5">
                      <Input
                        type="number"
                        min={1}
                        max={9999}
                        step={1}
                        autoFocus
                        className="h-8 w-24 tabular-nums"
                        aria-label={`Correct units per case for ${row.description ?? row.item_code}`}
                        value={editDraft}
                        disabled={update.isPending}
                        onChange={(event) => setEditDraft(event.target.value)}
                      />
                      <Button
                        size="sm"
                        className="h-8"
                        disabled={parsed(editDraft) === null || update.isPending}
                        onClick={() => saveEdit(row)}
                      >
                        {update.isPending ? "Saving…" : "Update"}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-8 px-2"
                        disabled={update.isPending}
                        onClick={() => setEditing(null)}
                        aria-label="Cancel"
                      >
                        <X className="size-3.5" />
                      </Button>
                    </div>
                  ) : row.mapped ? (
                    <div className="flex items-center gap-2">
                      <span className="text-success flex items-center gap-1.5 text-[0.82rem] font-medium">
                        <Check className="size-3.5" strokeWidth={3} />
                        {row.units_per_case} units/case
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-1.5 text-muted-foreground"
                        onClick={() => startEditing(row)}
                        aria-label={`Correct the mapping for ${row.description ?? row.item_code}`}
                      >
                        <Pencil className="size-3" />
                      </Button>
                      <SuggestionNote row={row} />
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <TriangleAlert className="text-warning size-3.5 shrink-0" />
                      <Input
                        type="number"
                        min={1}
                        max={9999}
                        step={1}
                        className="h-8 w-24 tabular-nums"
                        aria-label={`Units per case for ${row.description ?? row.item_code}`}
                        placeholder="e.g. 24"
                        value={draftFor(row)}
                        disabled={confirm.isPending}
                        onChange={(event) =>
                          setDrafts((current) => ({
                            ...current,
                            [row.item_code!]: event.target.value,
                          }))
                        }
                      />
                      <SuggestionNote row={row} />
                    </div>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {pending.length > 0 && (
          <div className="flex items-center justify-between gap-4 border-t px-3 py-3">
            <p className="text-[0.75rem] text-muted-foreground">
              Saved against the UPC — this product will never need confirming again.
            </p>
            <Button
              size="sm"
              disabled={ready.length === 0 || confirm.isPending}
              onClick={save}
            >
              {confirm.isPending
                ? "Saving…"
                : `Confirm & Save${ready.length > 1 ? ` (${ready.length})` : ""}`}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
