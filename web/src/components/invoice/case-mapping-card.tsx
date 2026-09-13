import { Check, Clock, Database, FileText, HelpCircle, Pencil, PackageSearch, TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import type { CaseMappingConfirmation, CaseMappingRow } from "@/api/types";
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
import { cn } from "@/lib/utils";

/**
 * Case → unit mapping review.
 *
 * PDI multiplies units-per-case by its own item retail to get Case
 * Retail, so a wrong value silently corrupts pricing. product_case_mappings
 * is therefore the single authority: a value only ever reaches an EDI
 * after a person confirms it, and it is stored against the UPC so the
 * same product is never asked about again.
 *
 * Unresolved products are grouped by how good the evidence is, because
 * "24, derived from the store's own cost basis" and "24, because the
 * description contains 24/12OZ" and "could be 4 or 24" deserve very
 * different amounts of scrutiny — and presenting them in one
 * undifferentiated list invites confirming the weak ones as fast as the
 * strong ones. That is how a real invoice reached PDI with Units Per
 * Case 1 on every product.
 *
 * Nothing here is ever confirmed automatically, whatever the evidence.
 */

type Band = "reference" | "document" | "ambiguous" | "none";

const BANDS: {
  id: Band;
  label: string;
  blurb: string;
  icon: typeof Database;
  tone: string;
  prefill: boolean;
}[] = [
  {
    id: "reference",
    label: "A · Derived from the store's own cost",
    blurb:
      "Invoice case cost ÷ the store's per-unit cost landed on a whole case pack. The strongest evidence available — still needs confirming.",
    icon: Database,
    tone: "text-success",
    prefill: true,
  },
  {
    id: "document",
    label: "B · Read from the document",
    blurb:
      "Taken from a pack column or the N/M notation in the description. Usually right; check it against the product.",
    icon: FileText,
    tone: "text-foreground",
    prefill: true,
  },
  {
    id: "ambiguous",
    label: "C · Ambiguous packaging",
    blurb:
      "The packaging supports more than one reading, and only the store knows which it sells by. Deliberately left blank.",
    icon: TriangleAlert,
    tone: "text-warning",
    prefill: false,
  },
  {
    id: "none",
    label: "D · No evidence",
    blurb:
      "Neither the document nor the store catalogue can answer this. Enter the value from the product itself.",
    icon: HelpCircle,
    tone: "text-muted-foreground",
    prefill: false,
  },
];

function bandOf(row: CaseMappingRow): Band {
  if (row.suggestion_source === "reference") return "reference";
  if (row.suggestion_source === "description_ambiguous") return "ambiguous";
  if (row.suggested_units_per_case !== null) return "document";
  return "none";
}

/** Where a value came from, in the operator's terms. */
function Evidence({ row }: { row: CaseMappingRow }) {
  if (row.suggestion_source === "database") {
    return <span className="text-muted-foreground">confirmed — reused on every future invoice</span>;
  }
  if (row.suggestion_source === "reference") {
    return (
      <span className="text-muted-foreground">
        store cost {row.reference_avg_cost?.toFixed(4)}/unit
      </span>
    );
  }
  if (row.suggestion_source === "description_ambiguous") {
    return (
      <span className="text-warning">
        could be
        {row.suggestion_candidates.map((n, i) => (
          <span key={n}>
            {i === 0 ? " " : " or "}
            <span className="font-semibold">{n}</span>
          </span>
        ))}
      </span>
    );
  }
  if (row.suggestion_source === "pack_size") {
    return <span className="text-muted-foreground">pack size “{row.pack_size ?? ""}”</span>;
  }
  if (row.suggestion_source === "description") {
    return <span className="text-muted-foreground">read from the description</span>;
  }
  return <span className="text-muted-foreground">nothing on the invoice or in the catalogue</span>;
}

export function CaseMappingCard({
  invoiceId,
  rows,
}: {
  invoiceId: string;
  rows: CaseMappingRow[];
}) {
  // Lines with no usable product code cannot be keyed to a mapping at
  // all (the backend's export gate skips them for the same reason).
  const mappable = useMemo(() => rows.filter((row) => row.item_code !== null), [rows]);
  const confirmed = useMemo(() => mappable.filter((row) => row.mapped), [mappable]);
  const pending = useMemo(() => mappable.filter((row) => !row.mapped), [mappable]);

  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // Correcting an already-confirmed mapping is a separate, one-product
  // action: a saved value is reused on every future invoice, so it must
  // never change as a side effect of confirming something else.
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const confirm = useConfirmCaseMappings(invoiceId);
  const update = useConfirmCaseMappings(invoiceId);

  if (mappable.length === 0) return null;

  const parsed = (value: string) => {
    const units = Number(value);
    return Number.isInteger(units) && units >= 1 && units <= 9999 ? units : null;
  };

  // Weak evidence is never prefilled: a prefilled box turns Confirm into
  // one click that persists a guess against the UPC forever.
  const draftFor = (row: CaseMappingRow) => {
    const band = BANDS.find((b) => b.id === bandOf(row));
    return (
      drafts[row.item_code!] ??
      (band?.prefill ? (row.suggested_units_per_case?.toString() ?? "") : "")
    );
  };

  const readyIn = (group: CaseMappingRow[]): CaseMappingConfirmation[] =>
    group
      .filter((row) => row.pending_value === null)   // already in the queue
      .map((row) => ({ row, units: parsed(draftFor(row)) }))
      .filter(({ units }) => units !== null)
      .map(({ row, units }) => ({
        item_code: row.item_code!,
        units_per_case: units!,
        description: row.description,
      }));

  const save = (group: CaseMappingRow[]) => {
    const ready = readyIn(group);
    if (ready.length === 0) return;
    confirm.mutate(ready, {
      onSuccess: (result) => {
        setDrafts({});
        toast.success(
          `Submitted ${result.saved} value${result.saved === 1 ? "" : "s"} for approval. ` +
            "The export unlocks once the data team approves.",
        );
      },
      onError: (error) => {
        toast.error(error instanceof Error ? error.message : "Failed to save mappings.");
      },
    });
  };

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
          toast.success(`Proposed ${units} units/case — awaiting approval.`);
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Failed to update.");
        },
      },
    );
  };

  const columns = (
    <TableHeader>
      <TableRow className="hover:bg-transparent">
        <TableHead>Product</TableHead>
        <TableHead>UPC</TableHead>
        <TableHead>Store catalogue</TableHead>
        <TableHead className="w-64">Units per case</TableHead>
      </TableRow>
    </TableHeader>
  );

  const productCells = (row: CaseMappingRow) => (
    <>
      <TableCell className="max-w-64 truncate font-medium">{row.description ?? "—"}</TableCell>
      <TableCell className="font-mono text-[0.78rem] tabular-nums">{row.item_code}</TableCell>
      <TableCell className="max-w-56 truncate text-[0.78rem] text-muted-foreground">
        {row.reference_description ?? <span className="opacity-60">no match</span>}
      </TableCell>
    </>
  );

  return (
    <Card className="gap-0 p-0" data-testid="case-mapping-card">
      <CardHeader className="px-5 py-4">
        <CardTitle className="flex items-center gap-2 text-[0.95rem]">
          <PackageSearch className="size-4" /> Case → unit mapping
          {pending.length > 0 ? (
            <span className="text-warning text-[0.75rem] font-medium">
              {pending.length} of {mappable.length} still need confirmation
            </span>
          ) : (
            <span className="text-success text-[0.75rem] font-medium">All products mapped</span>
          )}
        </CardTitle>
        <p className="text-[0.75rem] text-muted-foreground">
          PDI multiplies this by its own item retail, so each value is reviewed once and reused
          on every future invoice. <span className="font-medium">Confirm submits a value for
          data-team approval</span> — nothing reaches an EDI until it is approved.
        </p>
      </CardHeader>

      <CardContent className="space-y-5 px-2 pt-1 pb-3">
        {BANDS.map((band) => {
          const group = pending.filter((row) => bandOf(row) === band.id);
          if (group.length === 0) return null;
          const ready = readyIn(group);
          const Icon = band.icon;

          return (
            <div key={band.id}>
              <div className="px-3 pb-1">
                <div className={cn("flex items-center gap-1.5 text-[0.82rem] font-semibold", band.tone)}>
                  <Icon className="size-3.5" />
                  {band.label}
                  <span className="text-muted-foreground font-normal">({group.length})</span>
                </div>
                <p className="text-[0.72rem] text-muted-foreground">{band.blurb}</p>
              </div>

              <Table>
                {columns}
                <TableBody>
                  {group.map((row) => (
                    <TableRow key={row.item_code}>
                      {productCells(row)}
                      <TableCell>
                        {row.pending_value !== null ? (
                          <div className="flex items-center gap-2 text-[0.82rem]">
                            <Clock className="text-warning size-3.5 shrink-0" />
                            <span className="font-medium">{row.pending_value} units/case</span>
                            <span className="text-[0.7rem] text-muted-foreground">
                              awaiting data-team approval
                            </span>
                          </div>
                        ) : (
                          <div className="flex items-center gap-2">
                            <Input
                              type="number"
                              min={1}
                              max={9999}
                              step={1}
                              className="h-8 w-20 tabular-nums"
                              aria-label={`Units per case for ${row.description ?? row.item_code}`}
                              placeholder={band.prefill ? "" : "enter"}
                              value={draftFor(row)}
                              disabled={confirm.isPending}
                              onChange={(event) =>
                                setDrafts((current) => ({
                                  ...current,
                                  [row.item_code!]: event.target.value,
                                }))
                              }
                            />
                            <span className="text-[0.7rem]">
                              <Evidence row={row} />
                            </span>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              <div className="flex items-center justify-between gap-4 border-t px-3 py-2">
                <p className="text-[0.72rem] text-muted-foreground">
                  {(() => {
                    const queued = group.filter((r) => r.pending_value !== null).length;
                    if (ready.length === 0 && queued === group.length) return "All submitted — awaiting approval.";
                    if (ready.length === 0) return "Enter a value to submit.";
                    return `${ready.length} ready to submit${queued ? `, ${queued} awaiting approval` : ""}.`;
                  })()}
                </p>
                <Button
                  size="sm"
                  variant={band.id === "reference" ? "default" : "outline"}
                  disabled={ready.length === 0 || confirm.isPending}
                  onClick={() => save(group)}
                >
                  {confirm.isPending ? "Submitting…" : `Submit ${ready.length || ""} for approval`.trim()}
                </Button>
              </div>
            </div>
          );
        })}

        {confirmed.length > 0 && (
          <div>
            <div className="px-3 pb-1">
              <div className="text-success flex items-center gap-1.5 text-[0.82rem] font-semibold">
                <Check className="size-3.5" strokeWidth={3} />
                Confirmed
                <span className="text-muted-foreground font-normal">({confirmed.length})</span>
              </div>
              <p className="text-[0.72rem] text-muted-foreground">
                These values reach the EDI. Correct one if it is wrong — it is reused on every
                future invoice.
              </p>
            </div>
            <Table>
              {columns}
              <TableBody>
                {confirmed.map((row) => (
                  <TableRow key={row.item_code}>
                    {productCells(row)}
                    <TableCell>
                      {editing === row.item_code ? (
                        <div className="flex items-center gap-1.5">
                          <Input
                            type="number"
                            min={1}
                            max={9999}
                            step={1}
                            autoFocus
                            className="h-8 w-20 tabular-nums"
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
                      ) : (
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
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
