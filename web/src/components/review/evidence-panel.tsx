import { ChevronDown } from "lucide-react";
import { useState } from "react";

import type { ProposalDetail } from "@/api/types";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { formatMoney, formatPercent, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The evidence behind one proposal, rendered from the fields the backend
 * actually recorded. Shapes differ by source:
 *
 *   propose_from_reference  → { invoice_description, invoice_case_cost,
 *                                best{kind, units_per_case, …}, agreeing_sources, dissenting[] }
 *   review-UI confirmation  → { invoice_description, pack_size, suggested_units_per_case,
 *                                suggestion_source, suggestion_candidates,
 *                                reference_description, reference_avg_cost }
 *   legacy migration        → { description, original_mapping_source }
 *
 * Known keys get a labelled row; everything is also available verbatim
 * under "Raw evidence", so nothing the backend stored is hidden.
 */

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const num = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const str = (value: unknown): string | null =>
  typeof value === "string" && value.length > 0 ? value : null;

function Row({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="grid grid-cols-[minmax(9rem,1fr)_2fr] gap-x-4 gap-y-0.5 border-b py-2 text-[0.8rem] last:border-b-0">
      <div className="text-muted-foreground">
        {label}
        {hint ? <div className="text-[0.68rem] opacity-80">{hint}</div> : null}
      </div>
      <div className="min-w-0 break-words tabular-nums">{children}</div>
    </div>
  );
}

function Mono({ children }: { children: React.ReactNode }) {
  return <span className="font-mono text-[0.78rem]">{children}</span>;
}

function Origin({ file, sheet, row }: { file: string | null; sheet: string | null; row: number | null }) {
  if (!file && !sheet && row === null) return <span className="text-muted-foreground">—</span>;
  return (
    <span>
      {file ? <Mono>{file}</Mono> : null}
      {sheet ? <> · sheet <Mono>{sheet}</Mono></> : null}
      {row !== null ? <> · row <Mono>{row}</Mono></> : null}
    </span>
  );
}

/** The `best` evidence block written by propose_from_reference. */
function BestEvidence({ best, invoiceCaseCost }: { best: Json; invoiceCaseCost: number | null }) {
  const kind = str(best.kind);
  const units = num(best.units_per_case);
  const caseCost = num(best.case_cost);
  const unitCost = num(best.unit_cost);
  const unitRetail = num(best.unit_retail);
  const margin = num(best.margin);
  const band = Array.isArray(best.band) ? (best.band as unknown[]).map(num) : null;
  const marginByUnits = isObject(best.margin_by_units) ? best.margin_by_units : null;
  const matches = typeof best.case_cost_matches_invoice === "boolean" ? best.case_cost_matches_invoice : null;

  return (
    <>
      {kind ? <Row label="Evidence kind"><Mono>{kind}</Mono></Row> : null}
      {units !== null ? <Row label="Implies units/case"><span className="font-semibold">{units}</span></Row> : null}
      {str(best.package) ? <Row label="Package as written"><Mono>{str(best.package)}</Mono></Row> : null}
      {str(best.derivation) ? <Row label="Derivation">{titleCase(str(best.derivation)!)}</Row> : null}
      {str(best.reference_description) ? (
        <Row label="Store description">{str(best.reference_description)}</Row>
      ) : null}
      {str(best.distributor) || str(best.pricing_basis) || str(best.effective_from) ? (
        <Row label="Pricing source">
          {[str(best.distributor), str(best.pricing_basis) && titleCase(str(best.pricing_basis)!), str(best.effective_from) && `from ${str(best.effective_from)}`]
            .filter(Boolean)
            .join(" · ")}
        </Row>
      ) : null}
      {caseCost !== null || unitCost !== null ? (
        <Row label="Reference cost" hint="case ÷ unit as the source states them">
          {caseCost !== null ? <>case {formatMoney(caseCost)}</> : null}
          {caseCost !== null && unitCost !== null ? " · " : null}
          {unitCost !== null ? <>unit {formatMoney(unitCost)}</> : null}
          {caseCost !== null && unitCost !== null && unitCost > 0 ? (
            <span className="text-muted-foreground"> → {(caseCost / unitCost).toFixed(2)}</span>
          ) : null}
        </Row>
      ) : null}
      {invoiceCaseCost !== null && caseCost !== null ? (
        <Row label="Cost comparison" hint="invoice case cost vs reference case cost">
          invoice {formatMoney(invoiceCaseCost)} · reference {formatMoney(caseCost)}
          {matches !== null ? (
            <span className={cn("ml-2 text-[0.72rem] font-medium", matches ? "text-success" : "text-warning")}>
              {matches ? "match" : "differ"}
            </span>
          ) : null}
        </Row>
      ) : null}
      {unitRetail !== null ? (
        <Row label="Store unit retail" hint="what the store sells one unit for">
          {formatMoney(unitRetail)}
        </Row>
      ) : null}
      {margin !== null ? (
        <Row label="Implied margin" hint={band ? `calibrated band ${formatPercent(band[0])}–${formatPercent(band[1])}` : undefined}>
          {formatPercent(margin)}
          {str(best.strength) ? (
            <span className={cn("ml-2 rounded px-1.5 py-0.5 text-[0.7rem] font-medium",
              best.strength === "strong" ? "bg-success-soft text-success" : "bg-warning-soft text-warning")}>
              {str(best.strength)}
            </span>
          ) : null}
        </Row>
      ) : null}
      {str(best.note) ? <Row label="Note">{str(best.note)}</Row> : null}
      {marginByUnits ? (
        <Row label="Margin by pack reading" hint="every plausible pack, the margin it would imply">
          <div className="flex flex-wrap gap-1">
            {Object.entries(marginByUnits).map(([pack, value]) => {
              const m = num(value);
              const chosen = Number(pack) === units;
              return (
                <span
                  key={pack}
                  className={cn(
                    "rounded border px-1.5 py-0.5 text-[0.7rem]",
                    chosen ? "border-success bg-success-soft text-success font-semibold" : "text-muted-foreground",
                  )}
                >
                  {pack} → {m === null ? "?" : formatPercent(m, 1)}
                </span>
              );
            })}
          </div>
        </Row>
      ) : null}
    </>
  );
}

export function EvidencePanel({ proposal }: { proposal: ProposalDetail }) {
  const [rawOpen, setRawOpen] = useState(false);
  const e = proposal.evidence ?? {};
  const best = isObject(e.best) ? e.best : null;
  const dissenting = Array.isArray(e.dissenting) ? (e.dissenting as unknown[]).filter(isObject) : [];
  const agreeing = num(e.agreeing_sources);
  const invoiceCaseCost = num(e.invoice_case_cost);
  const candidates = Array.isArray(e.suggestion_candidates)
    ? (e.suggestion_candidates as unknown[]).map(num).filter((v): v is number => v !== null)
    : [];
  const hasStructured =
    best !== null ||
    str(e.invoice_description) !== null ||
    str(e.description) !== null ||
    str(e.pack_size) !== null ||
    num(e.suggested_units_per_case) !== null;

  return (
    <div className="space-y-4">
      <div>
        <Row label="Recorded from">
          <Origin file={proposal.source_file} sheet={proposal.source_sheet} row={proposal.source_row} />
        </Row>
        {proposal.reason ? <Row label="Reason">{proposal.reason}</Row> : null}
        {str(e.invoice_description) ? (
          <Row label="Invoice description"><Mono>{str(e.invoice_description)}</Mono></Row>
        ) : null}
        {str(e.description) ? <Row label="Description"><Mono>{str(e.description)}</Mono></Row> : null}
        {invoiceCaseCost !== null ? <Row label="Invoice case cost">{formatMoney(invoiceCaseCost)}</Row> : null}
        {str(e.pack_size) ? <Row label="Printed pack size"><Mono>{str(e.pack_size)}</Mono></Row> : null}
        {num(e.suggested_units_per_case) !== null || str(e.suggestion_source) ? (
          <Row label="What the invoice suggested">
            {num(e.suggested_units_per_case) ?? "—"}
            {str(e.suggestion_source) ? (
              <span className="text-muted-foreground"> via {str(e.suggestion_source)}</span>
            ) : null}
          </Row>
        ) : null}
        {candidates.length > 0 ? (
          <Row label="Readings the notation allowed">{candidates.join(" or ")}</Row>
        ) : null}
        {str(e.reference_description) ? (
          <Row label="Store description">{str(e.reference_description)}</Row>
        ) : null}
        {num(e.reference_avg_cost) !== null ? (
          <Row label="Store avg cost / unit">{formatMoney(num(e.reference_avg_cost))}</Row>
        ) : null}
        {str(e.original_mapping_source) ? (
          <Row label="Original mapping source"><Mono>{str(e.original_mapping_source)}</Mono></Row>
        ) : null}
      </div>

      {best ? (
        <div>
          <div className="mb-1 text-[0.72rem] font-semibold tracking-wide text-muted-foreground uppercase">
            Best evidence
          </div>
          <BestEvidence best={best} invoiceCaseCost={invoiceCaseCost} />
        </div>
      ) : null}

      {agreeing !== null || dissenting.length > 0 ? (
        <div>
          <div className="mb-1 text-[0.72rem] font-semibold tracking-wide text-muted-foreground uppercase">
            Agreement
          </div>
          {agreeing !== null ? (
            <Row label="Sources agreeing">{agreeing}</Row>
          ) : null}
          <Row label="Dissenting">
            {dissenting.length === 0 ? (
              <span className="text-success">none</span>
            ) : (
              <ul className="space-y-0.5">
                {dissenting.map((d, index) => (
                  <li key={index} className="text-warning">
                    {num(d.units_per_case) ?? "?"} units/case
                    <span className="text-muted-foreground">
                      {" "}— {str(d.kind) ?? "?"}
                      {str(d.source_sheet) ? ` · ${str(d.source_sheet)}` : ""}
                      {num(d.source_row) !== null ? ` row ${num(d.source_row)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Row>
        </div>
      ) : null}

      {!hasStructured && Object.keys(e).length === 0 ? (
        <p className="text-[0.8rem] text-muted-foreground">No evidence was recorded with this proposal.</p>
      ) : null}

      {Object.keys(e).length > 0 ? (
        <Collapsible open={rawOpen} onOpenChange={setRawOpen}>
          <CollapsibleTrigger className="flex items-center gap-1 text-[0.75rem] font-medium text-muted-foreground hover:text-foreground">
            <ChevronDown className={cn("size-3.5 transition-transform", rawOpen && "rotate-180")} />
            Raw evidence (as stored)
          </CollapsibleTrigger>
          <CollapsibleContent>
            <pre className="mt-2 max-h-80 overflow-auto rounded-md bg-muted p-3 font-mono text-[0.72rem] leading-relaxed">
              {JSON.stringify(e, null, 2)}
            </pre>
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </div>
  );
}
