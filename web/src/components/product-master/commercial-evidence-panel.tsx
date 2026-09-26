import { AlertTriangle } from "lucide-react";
import { Fragment, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { CommercialCandidateRow } from "@/api/types";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  useApproveCommercialCandidate,
  useCommercialCandidate,
  useProposeCommercialCandidate,
  useRejectCommercialCandidate,
} from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import {
  PRODUCT_NAME_UNAVAILABLE,
  ProductNameBasisBadge,
  basisLabel,
  costLabel,
} from "@/pages/product-master-review";

/** How the display name was arrived at, stated plainly in the panel. */
function nameBasisLabel(candidate: CommercialCandidateRow): string {
  switch (candidate.product_name_basis) {
    case "CANONICAL":
      return "Canonical description";
    case "SOURCE":
      return `Source description — not canonical (${candidate.product_name_source ?? "source"})`;
    case "AMBIGUOUS_SOURCE":
      return (
        `${candidate.product_name_variant_count} materially different names in the ` +
        `${candidate.product_name_source ?? "source"} — none is chosen`
      );
    default:
      return "No description on record";
  }
}

interface Props {
  candidate: CommercialCandidateRow;
  onClose: () => void;
}

/**
 * The evidence a reviewer decides on.
 *
 * It answers two questions directly: why this candidate says what it says,
 * and what may never decide it. The inadmissible list is shown rather than
 * implied — a package of 18 does not make the PDI multiplier 18, and the
 * panel says so instead of leaving a reviewer to infer it.
 */
export function CommercialEvidencePanel({ candidate, onClose }: Props) {
  const detail = useCommercialCandidate(candidate.id);
  const approve = useApproveCommercialCandidate();
  const reject = useRejectCommercialCandidate();
  const propose = useProposeCommercialCandidate();
  const { user } = useAuth();
  // Deciding is MANAGER/ADMIN. A USER reviews the same evidence and
  // proposes a value; the backend enforces this independently.
  const canDecide = user?.role === "MANAGER" || user?.role === "ADMIN";

  const [basis, setBasis] = useState<string>("");
  const [units, setUnits] = useState<string>("");
  const [note, setNote] = useState("");

  const needsResolution = candidate.requires_resolution;
  const settled = candidate.approval_state !== "REVIEW_REQUIRED";
  const evidence = detail.data?.evidence;

  const onError = (error: unknown) => {
    toast.error(error instanceof ApiError ? error.userMessage : "The decision was not saved.");
  };

  const submitApprove = () => {
    approve.mutate(
      {
        id: candidate.id,
        body: {
          note: note.trim() || null,
          commercial_unit_basis: basis || null,
          units_accounted_for: units ? Number(units) : null,
        },
      },
      {
        onSuccess: () => { toast.success("Candidate approved — master data only, EDI unchanged."); onClose(); },
        onError,
      },
    );
  };

  const submitPropose = () => {
    const value = Number(units);
    if (!value) {
      toast.error("Enter the units per case you are proposing.");
      return;
    }
    propose.mutate(
      { id: candidate.id, body: { units_accounted_for: value, note: note.trim() || null } },
      {
        onSuccess: () => {
          toast.success("Proposal submitted for review.");
          onClose();
        },
        onError,
      },
    );
  };

  const submitReject = () => {
    reject.mutate(
      { id: candidate.id, body: { note: note.trim() || null } },
      {
        onSuccess: () => { toast.success("Candidate rejected and kept for audit."); onClose(); },
        onError,
      },
    );
  };

  return (
    <AlertDialog open onOpenChange={(next) => !next && onClose()}>
      <AlertDialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">
            {candidate.product_name ?? PRODUCT_NAME_UNAVAILABLE}
          </AlertDialogTitle>
          <AlertDialogDescription>
            <span className="font-mono text-xs">{candidate.canonical_identifier ?? "—"}</span>
            {" · "}
            {candidate.store_label}
            {candidate.store_identity_status !== "confirmed" && " · location not confirmed"}
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-6">
          <section className="space-y-2">
            <h3 className="text-sm font-medium">Product</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">UPC</dt>
              <dd className="font-mono text-xs">{candidate.canonical_identifier ?? "—"}</dd>
              <dt className="text-muted-foreground">PDI item code</dt>
              <dd className="font-mono text-xs">{candidate.pdi_item_code ?? "—"}</dd>
              <dt className="text-muted-foreground">Product name</dt>
              <dd className="space-y-1">
                <div>{candidate.product_name ?? PRODUCT_NAME_UNAVAILABLE}</div>
                <ProductNameBasisBadge basis={candidate.product_name_basis} />
                <div className="text-xs text-muted-foreground">
                  {nameBasisLabel(candidate)}
                  {candidate.product_name_reference ? ` · ${candidate.product_name_reference}` : ""}
                </div>
              </dd>
            </dl>
            {candidate.product_name_basis === "AMBIGUOUS_SOURCE" && (
              <div className="space-y-1 rounded-md border border-amber-300 p-3 dark:border-amber-800">
                <h4 className="text-xs font-medium">Source names for this UPC</h4>
                <ul className="space-y-1 text-sm">
                  {candidate.product_name_variants.map((variant) => (
                    <li key={variant.description} className="flex flex-wrap justify-between gap-x-4">
                      <span>{variant.description}</span>
                      <span className="font-mono text-xs text-muted-foreground">
                        {variant.references.join("; ")}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-muted-foreground">
                  These differ in wording that can mean a different flavour, size, pack or
                  variety pack, so no single name is shown. Confirm which product this UPC is
                  before relying on any of them.
                </p>
              </div>
            )}
            {evidence?.descriptions?.length ? (
              <div className="space-y-1">
                <h4 className="text-xs font-medium text-muted-foreground">
                  Descriptions on record
                </h4>
                <ul className="space-y-0.5 text-sm">
                  {evidence.descriptions.map((description, index) => (
                    <li key={index} className="flex flex-wrap justify-between gap-x-4">
                      <span>
                        <span className="text-xs uppercase text-muted-foreground">
                          {description.role === "CANONICAL" ? "Canonical" : "Source"}
                        </span>{" "}
                        {description.description}
                      </span>
                      <span className="font-mono text-xs text-muted-foreground">
                        {description.source_sheet ?? description.source_system}
                        {description.source_row !== null ? ` row ${description.source_row}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <p className="text-xs text-muted-foreground">
              The name is a label for reading. The product is identified by its UPC.
            </p>
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-medium">Commercial unit</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Basis</dt>
              <dd>{basisLabel(candidate.commercial_unit_basis)}</dd>
              <dt className="text-muted-foreground">Multiplier</dt>
              <dd className="tabular-nums">{candidate.units_accounted_for ?? "not established"}</dd>
              <dt className="text-muted-foreground">Case cost</dt>
              <dd className="tabular-nums">
                {candidate.case_cost === null ? "—" : candidate.case_cost.toFixed(2)}
                <span className="text-muted-foreground"> · {costLabel(candidate.cost_basis)}</span>
              </dd>
            </dl>
            {(candidate.conflict_explanation || evidence?.notes) && (
              <p className="text-sm text-muted-foreground">
                {candidate.conflict_explanation ?? evidence?.notes}
              </p>
            )}
          </section>

          {candidate.legacy_mappings.length > 0 && (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Legacy mapping — current EDI authority</h3>
              <ul className="space-y-1 text-sm">
                {candidate.legacy_mappings.map((legacy, index) => (
                  <li key={index} className="flex justify-between gap-4">
                    <span className="font-mono text-xs">{legacy.item_code}</span>
                    <span className="tabular-nums">{legacy.units_per_case} units/case</span>
                    <span className="text-muted-foreground">{legacy.source}</span>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-muted-foreground">
                This is what EDI uses today. Approving the candidate does not change it.
              </p>
            </section>
          )}

          <section className="space-y-2">
            <h3 className="text-sm font-medium">Source values (as the workbook holds them)</h3>
            {evidence?.source_snapshot_rows?.length ? (
              <div className="space-y-2">
                {evidence.source_snapshot_rows.map((row, index) => (
                  <dl key={index} className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-sm">
                    {[
                      ["Workbook", row.source_file],
                      ["Sheet", row.source_sheet],
                      ["Row", row.source_row],
                      ["Source identifier", row.raw_identifier],
                      ["Source description", row.raw_description],
                      ["Items/Case as printed", row.raw_items_case],
                      ["Package / format", row.raw_package],
                      ["Case cost", row.raw_case_cost],
                      ["Unit cost", row.raw_unit_cost],
                      ["Divisor evidence", row.raw_divisor_evidence],
                    ]
                      .filter(([, value]) => value !== undefined && value !== null)
                      .map(([label, value]) => (
                        <Fragment key={String(label)}>
                          <dt className="text-muted-foreground">{String(label)}</dt>
                          <dd className="break-all">{String(value)}</dd>
                        </Fragment>
                      ))}
                  </dl>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                No source snapshot was captured for this candidate.
              </p>
            )}
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-medium">Derived from the source</h3>
            <p className="text-sm text-muted-foreground">
              {basisLabel(candidate.commercial_unit_basis)}
              {candidate.units_accounted_for !== null
                ? ` → multiplier ${candidate.units_accounted_for}`
                : " → no multiplier"}
              . Derived, not printed in the workbook.
            </p>
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-medium">Derivation detail</h3>
            {evidence?.source_statements?.length ? (
              <ul className="space-y-1 text-sm">
                {evidence.source_statements.map((statement, index) => (
                  <li key={index} className="text-muted-foreground">
                    <span className="font-mono text-xs">
                      {String(statement.source_sheet ?? "")} row {String(statement.source_row ?? "")}
                    </span>
                    {" — states "}
                    <span className="tabular-nums text-foreground">{String(statement.units ?? "?")}</span>
                    {statement.statement ? ` (${String(statement.statement)})` : ""}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No distributor statement was found.</p>
            )}
            {evidence?.governed_units_observed?.length ? (
              <p className="text-sm text-muted-foreground">
                An existing governed mapping holds{" "}
                <span className="tabular-nums">{evidence.governed_units_observed.join(", ")}</span>.
              </p>
            ) : null}
            {evidence?.inadmissible_evidence?.length ? (
              <p className="text-xs text-muted-foreground">
                Never determines the multiplier: {evidence.inadmissible_evidence.join(", ")}.
                A package of 18 does not make the multiplier 18.
              </p>
            ) : null}
          </section>

          {candidate.proposed_units_accounted_for !== null && (
            <section className="space-y-1">
              <h3 className="text-sm font-medium">Pending proposal</h3>
              <p className="text-sm text-muted-foreground">
                {candidate.proposed_by} proposed{" "}
                <span className="tabular-nums text-foreground">
                  {candidate.proposed_units_accounted_for}
                </span>
                {candidate.proposed_note ? ` · ${candidate.proposed_note}` : ""}
              </p>
            </section>
          )}

          {detail.data?.history.length ? (
            <section className="space-y-2">
              <h3 className="text-sm font-medium">Decision history</h3>
              <ul className="space-y-1 text-sm text-muted-foreground">
                {detail.data.history.map((entry, index) => (
                  <li key={index}>
                    {entry.decision.toLowerCase()} by {entry.reviewer} ·{" "}
                    {entry.previous_commercial_unit_basis} → {entry.new_commercial_unit_basis}
                    {entry.note ? ` · ${entry.note}` : ""}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {!settled && (
            <section className="space-y-3 border-t pt-4">
              {needsResolution && (
                <p className="flex items-start gap-2 text-sm">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                  <span>
                    The evidence did not settle this candidate. Choose the interpretation it
                    supports, or reject it.
                  </span>
                </p>
              )}

              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="basis">Commercial interpretation</label>
                <Select value={basis} onValueChange={setBasis}>
                  <SelectTrigger id="basis">
                    <SelectValue placeholder={needsResolution ? "Choose one" : "Keep as derived"} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="CASE_IS_SELLING_UNIT">
                      Case is the selling unit (multiplier 1)
                    </SelectItem>
                    <SelectItem value="UNIT_IS_SELLING_UNIT">
                      Contained unit is the selling unit
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {basis === "UNIT_IS_SELLING_UNIT" && (
                <div className="space-y-2">
                  <label className="text-sm font-medium" htmlFor="units">Sellable units per case</label>
                  <Input
                    id="units" type="number" min={2} max={9999} value={units}
                    onChange={(event) => setUnits(event.target.value)}
                  />
                </div>
              )}

              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="note">Note</label>
                <Input
                  id="note" value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="What the decision rests on"
                />
              </div>

              <div className="flex gap-2">
                {canDecide ? (
                  <>
                    <Button onClick={submitApprove} disabled={approve.isPending}>
                      Approve candidate
                    </Button>
                    <Button variant="outline" onClick={submitReject} disabled={reject.isPending}>
                      Reject
                    </Button>
                  </>
                ) : (
                  <Button onClick={submitPropose} disabled={propose.isPending}>
                    Submit proposal
                  </Button>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                {canDecide
                  ? "Approval records a master-data decision. It does not change EDI output."
                  : "Your proposal is reviewed by a manager before it becomes authoritative."}
              </p>
            </section>
          )}
          {settled && (
            <div className="flex justify-end border-t pt-4">
              <Button variant="outline" onClick={onClose}>Close</Button>
            </div>
          )}
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
