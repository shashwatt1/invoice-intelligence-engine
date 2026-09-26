import { AlertTriangle, Check, FileSpreadsheet, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { CommercialCandidateRow } from "@/api/types";
import { PageHeader } from "@/components/layout/page-header";
import { CommercialEvidencePanel } from "@/components/product-master/commercial-evidence-panel";
import { FilterBar } from "@/components/shared/filter-bar";
import { MetricStrip } from "@/components/shared/metric-strip";
import { Pagination } from "@/components/shared/pagination";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/shared/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCommercialCandidates, useCommercialSummary } from "@/hooks/use-api";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;

/** How many sellable units PDI multiplies Item Retail by — not what is in the box. */
export function basisLabel(basis: string): string {
  switch (basis) {
    case "CASE_IS_SELLING_UNIT":
      return "Case is the selling unit";
    case "UNIT_IS_SELLING_UNIT":
      return "Contained unit is the selling unit";
    case "CONFLICT":
      return "Conflicting evidence";
    case "UNKNOWN":
      return "No evidence";
    default:
      return basis;
  }
}

export const PRODUCT_NAME_UNAVAILABLE = "Product name unavailable";
export const MULTIPLE_SOURCE_NAMES = "Multiple source names";

/** How many materially different source wordings are listed in the queue itself. */
const VARIANTS_SHOWN_INLINE = 3;

/**
 * The kind of name, stated on every row. The name is a label chosen by the
 * description policy — never the identity, which stays the UPC beside it.
 */
export function ProductNameBasisBadge({ basis }: { basis: CommercialCandidateRow["product_name_basis"] }) {
  switch (basis) {
    case "CANONICAL":
      return <Badge variant="outline">Canonical</Badge>;
    case "SOURCE":
      return <Badge variant="secondary">Source-derived</Badge>;
    case "AMBIGUOUS_SOURCE":
      return (
        <Badge className="border-amber-300 bg-amber-100 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          <AlertTriangle aria-hidden />
          Multiple source names — review
        </Badge>
      );
    default:
      return <Badge variant="outline">No name on record</Badge>;
  }
}

/** The product's display name. Ambiguity is shown as the names themselves, not a count. */
export function ProductName({ row, className }: { row: CommercialCandidateRow; className?: string }) {
  if (row.product_name_basis === "AMBIGUOUS_SOURCE") {
    const shown = row.product_name_variants.slice(0, VARIANTS_SHOWN_INLINE);
    const more = row.product_name_variants.length - shown.length;
    return (
      <div className={cn("space-y-1", className)}>
        <ProductNameBasisBadge basis={row.product_name_basis} />
        <ul className="space-y-0.5 text-xs leading-snug" aria-label="Source names">
          {shown.map((variant) => (
            <li key={variant.description}>{variant.description}</li>
          ))}
        </ul>
        {more > 0 && (
          <div className="text-xs text-muted-foreground">
            and {more} more — open Review to see all {row.product_name_variant_count}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className={cn("space-y-1", className)}>
      <div
        className={cn(
          "font-medium leading-snug",
          row.product_name_basis === "UNAVAILABLE" && "font-normal italic text-muted-foreground",
        )}
      >
        {row.product_name ?? PRODUCT_NAME_UNAVAILABLE}
      </div>
      <ProductNameBasisBadge basis={row.product_name_basis} />
    </div>
  );
}

export function costLabel(basis: string | null): string {
  switch (basis) {
    case "DISTRIBUTOR_CASE_PRICE":
      return "Distributor case price";
    case "CONFLICTING_SOURCES":
      return "Sources disagree";
    case "UNRESOLVED":
    case null:
      return "Not established";
    default:
      return basis;
  }
}

/**
 * Product Master commercial review.
 *
 * Every row here is a candidate, never an authority: `product_case_mappings`
 * still drives EDI and approving a candidate changes no export. The header
 * says so, and each row shows the legacy value beside the candidate so a
 * reviewer can see what the live system currently does.
 *
 * Conflict rows carry no multiplier on purpose — the evidence produced
 * none — so approving one requires choosing the interpretation explicitly.
 */
export function ProductMasterReviewPage() {
  const [state, setState] = useState("READY_FOR_REVIEW");
  const [basis, setBasis] = useState("ALL");
  const [cost, setCost] = useState("ALL");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<CommercialCandidateRow | null>(null);

  useEffect(() => setPage(1), [state, basis, cost, search]);

  const params = useMemo(() => ({
    review_status: state === "ALL" ? undefined : state,
    commercial_unit_basis: basis === "ALL" || basis === "CONFLICT" ? undefined : basis,
    conflicts_only: basis === "CONFLICT" || undefined,
    cost_basis: cost === "ALL" ? undefined : cost,
    search: search.trim() || undefined,
    page,
    page_size: PAGE_SIZE,
  }), [state, basis, cost, search, page]);

  const query = useCommercialCandidates(params);
  const summary = useCommercialSummary();

  return (
    <>
      <PageHeader
        title="Product Master — Commercial Review"
        description={
          "Reference-data candidates for how each store accounts for a product in PDI. " +
          "Approving a candidate records a master-data decision; it does not change EDI, " +
          "which still reads the legacy case mappings shown beside each row."
        }
      />

      <MetricStrip
        items={[
          { key: "review", label: "Awaiting review", value: summary.data?.review_required ?? "—" },
          { key: "conflicts", label: "Conflicts", value: summary.data?.conflicts ?? "—" },
          { key: "pending", label: "Pending proposals", value: summary.data?.pending ?? "—" },
          { key: "approved", label: "Approved", value: summary.data?.approved ?? "—" },
          { key: "rejected", label: "Rejected", value: summary.data?.rejected ?? "—" },
        ]}
      />

      <FilterBar>
        <Input
          placeholder="Search UPC, item code or name"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="max-w-[260px]"
        />
        <Select value={state} onValueChange={setState}>
          <SelectTrigger className="w-[180px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="READY_FOR_REVIEW">Ready for review</SelectItem>
            <SelectItem value="PENDING">Pending proposal</SelectItem>
            <SelectItem value="CONFLICT">Conflict</SelectItem>
            <SelectItem value="NO_MULTIPLIER">No multiplier</SelectItem>
            <SelectItem value="APPROVED">Approved</SelectItem>
            <SelectItem value="REJECTED">Rejected</SelectItem>
            <SelectItem value="ALL">All states</SelectItem>
          </SelectContent>
        </Select>
        <Select value={basis} onValueChange={setBasis}>
          <SelectTrigger className="w-[230px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">All commercial units</SelectItem>
            <SelectItem value="CONFLICT">Conflicts only</SelectItem>
            <SelectItem value="CASE_IS_SELLING_UNIT">Case is the selling unit</SelectItem>
            <SelectItem value="UNIT_IS_SELLING_UNIT">Contained unit is the selling unit</SelectItem>
          </SelectContent>
        </Select>
        <Select value={cost} onValueChange={setCost}>
          <SelectTrigger className="w-[190px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">Any cost status</SelectItem>
            <SelectItem value="DISTRIBUTOR_CASE_PRICE">Cost established</SelectItem>
            <SelectItem value="CONFLICTING_SOURCES">Cost sources disagree</SelectItem>
          </SelectContent>
        </Select>
      </FilterBar>

      {query.isLoading ? (
        <TableSkeleton rows={8} />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (query.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          icon={FileSpreadsheet}
          title="Nothing to review"
          description="No commercial candidates match these filters."
        />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product name</TableHead>
                <TableHead>UPC / PDI item</TableHead>
                <TableHead>Store</TableHead>
                <TableHead>Commercial unit</TableHead>
                <TableHead className="text-right">Units/case</TableHead>
                <TableHead className="text-right">Case cost</TableHead>
                <TableHead>Legacy (EDI)</TableHead>
                <TableHead>State</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data!.items.map((row) => (
                <TableRow
                  key={row.id}
                  className={cn(row.is_conflict && "bg-muted/40")}
                  data-conflict={row.is_conflict || undefined}
                >
                  <TableCell className="min-w-[220px] max-w-[320px] text-sm">
                    <ProductName row={row} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {row.canonical_identifier ?? "—"}
                    <div className="text-muted-foreground">{row.pdi_item_code ?? ""}</div>
                  </TableCell>
                  <TableCell className="text-sm">
                    {row.store_label}
                    {row.store_identity_status !== "confirmed" && (
                      <div className="text-xs text-muted-foreground">location not confirmed</div>
                    )}
                  </TableCell>
                  <TableCell className="text-sm">
                    <span className="inline-flex items-center gap-1.5">
                      {row.is_conflict && <AlertTriangle className="size-3.5 shrink-0" aria-hidden />}
                      {basisLabel(row.commercial_unit_basis)}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.units_accounted_for ?? <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.case_cost === null ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      row.case_cost.toFixed(2)
                    )}
                    <div className="text-xs text-muted-foreground">{costLabel(row.cost_basis)}</div>
                  </TableCell>
                  <TableCell className="text-sm">
                    {row.legacy_mappings.length === 0 ? (
                      <span className="text-muted-foreground">none</span>
                    ) : (
                      <>
                        <span className="tabular-nums">
                          {row.legacy_mappings.map((m) => m.units_per_case).join(", ")}
                        </span>
                        <div className="text-xs text-muted-foreground">
                          {row.legacy_agreement === "AGREES"
                            ? "agrees"
                            : row.legacy_agreement === "DISSENTS"
                              ? "dissents"
                              : ""}
                        </div>
                      </>
                    )}
                  </TableCell>
                  <TableCell className="text-sm">
                    <span className="inline-flex items-center gap-1.5">
                      {row.approval_state === "APPROVED" && <Check className="size-3.5" aria-hidden />}
                      {row.approval_state === "REJECTED" && <X className="size-3.5" aria-hidden />}
                      {row.review_status.replaceAll("_", " ").toLowerCase()}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="outline" size="sm" onClick={() => setSelected(row)}>
                      Review
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={query.data!.total}
            onPageChange={setPage}
          />
        </>
      )}

      {selected && (
        <CommercialEvidencePanel
          candidate={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
