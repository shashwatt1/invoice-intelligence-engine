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
import { ReviewStatusBadge } from "@/components/product-master/review-status-badge";
import {
  useApproveCommercialCandidate,
  useCommercialCandidate,
  useProposeCommercialCandidate,
  useRejectCommercialCandidate,
  useReopenCommercialCandidate,
} from "@/hooks/use-api";
import { useAuth } from "@/hooks/use-auth";
import { roleLabel, sellingUnitCost } from "@/lib/commercial";
import { formatDateTime } from "@/lib/format";
import { sourceIdentityView } from "@/lib/stores";
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

const DECISION_LABEL: Record<string, string> = {
  PROPOSE: "Proposed", APPROVE: "Approved", REJECT: "Rejected", REOPEN: "Reopened for reconsideration",
};
const STATE_LABEL: Record<string, string> = {
  REVIEW_REQUIRED: "review required", PENDING: "proposal pending", APPROVED: "approved", REJECTED: "rejected",
};

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

  const reopen = useReopenCommercialCandidate();
  const [basis, setBasis] = useState<string>("");
  const [units, setUnits] = useState<string>("");
  const [note, setNote] = useState("");
  // A final decision is a second, explicit step — never a single click.
  const [confirming, setConfirming] = useState<"approve" | "reject" | "reopen" | null>(null);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [stale, setStale] = useState(false);

  const needsResolution = candidate.requires_resolution;
  // Decided rows are closed; a PENDING proposal is still open for a MANAGER/ADMIN decision.
  const settled = candidate.approval_state === "APPROVED" || candidate.approval_state === "REJECTED";
  const evidence = detail.data?.evidence;
  const current = detail.data?.candidate ?? candidate;
  // The version this person is deciding against; a decision made after anyone
  // else acted on the candidate is refused by the server (409).
  const reviewVersion = current.review_version ?? 0;
  const source = sourceIdentityView(candidate.store_kind, candidate.store_source_identity);
  const basisGiven = note.trim().length > 0;
  const dirty = basisGiven || basis !== "" || units !== "";
  // The multiplier the decision would record: the reviewer's choice, else what was derived.
  const typedUnits = Number(units);
  const multiplier = basis === "CASE_IS_SELLING_UNIT" ? 1
    : basis === "UNIT_IS_SELLING_UNIT" ? (Number.isInteger(typedUnits) && typedUnits >= 2 ? typedUnits : null)
      : needsResolution ? null : candidate.units_accounted_for;
  const unitCost = sellingUnitCost(candidate.case_cost, multiplier);
  const proposalReady = basisGiven && Number.isInteger(typedUnits) && typedUnits >= 1;
  const busy = approve.isPending || reject.isPending || reopen.isPending || propose.isPending;

  // Leaving with unsaved notes asks first; nothing typed is lost silently.
  const attemptClose = () => {
    if (dirty && !confirmLeave) {
      setConfirmLeave(true);
      return;
    }
    onClose();
  };

  const onError = (error: unknown) => {
    if (error instanceof ApiError && error.statusCode === 409) {
      setStale(true);
      setConfirming(null);
      toast.error("This mapping changed since you opened it. Nothing was saved — reopen it to see the latest state.");
      return;
    }
    toast.error(error instanceof ApiError ? error.userMessage : "The decision was not saved.");
  };
  const done = (message: string) => () => {
    toast.success(message);
    onClose();
  };

  const submitApprove = () => approve.mutate(
    {
      id: candidate.id,
      body: {
        note: note.trim(),
        commercial_unit_basis: basis || null,
        units_accounted_for: units ? Number(units) : null,
        expected_review_version: reviewVersion,
      },
    },
    { onSuccess: done("Candidate approved — master data only, EDI unchanged."), onError },
  );
  const submitReject = () => reject.mutate(
    { id: candidate.id, body: { note: note.trim(), expected_review_version: reviewVersion } },
    { onSuccess: done("Candidate rejected and kept for audit."), onError },
  );
  const submitReopen = () => reopen.mutate(
    { id: candidate.id, body: { reason: note.trim(), expected_review_version: reviewVersion } },
    { onSuccess: done("Reopened for reconsideration — the earlier decision stays in the history."), onError },
  );
  const submitPropose = () => propose.mutate(
    { id: candidate.id, body: { units_accounted_for: typedUnits, note: note.trim() } },
    { onSuccess: done("Proposal submitted for review."), onError },
  );
  const confirmAction = { approve: submitApprove, reject: submitReject, reopen: submitReopen };
  const confirmText = {
    approve: (
      `Approve this mapping? It becomes the authoritative commercial mapping for ` +
      `${source ? source.name : candidate.store_label} — master data only; EDI output does not change.`
    ),
    reject: "Reject this mapping? It stays on record for audit and no longer awaits review.",
    reopen: (
      "Reopen this decided mapping for reconsideration? The decision stays in the history, and the " +
      "mapping returns to review with the interpretation the evidence gave before it."
    ),
  };
  const confirmLabel = { approve: "Confirm approval", reject: "Confirm rejection", reopen: "Confirm reopen" };

  return (
    <AlertDialog open onOpenChange={(next) => !next && attemptClose()}>
      <AlertDialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-base">
            {candidate.product_name ?? PRODUCT_NAME_UNAVAILABLE}
          </AlertDialogTitle>
          <ReviewStatusBadge status={current.review_status} lastDecision={current.last_decision} />
          <AlertDialogDescription>
            <span className="font-mono text-xs">{candidate.canonical_identifier ?? "—"}</span>
            {" · "}
            {source ? (
              <>{source.name} · source identity · physical store not identified</>
            ) : (
              <>
                {candidate.store_label}
                {candidate.store_identity_status !== "confirmed" && " · location not confirmed"}
              </>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-6">
          <section className="space-y-2">
            <h3 className="text-sm font-medium">Product identity</h3>
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

          <section className="space-y-4 border-t pt-4">
            <h3 className="text-sm font-medium">Source evidence</h3>

            {source && (
              <div className="space-y-1" data-testid="store-context">
                <h4 className="text-xs font-medium text-muted-foreground">Source identity</h4>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-sm">
                  <dt className="text-muted-foreground">Source system</dt>
                  <dd>{source.system}</dd>
                  <dt className="text-muted-foreground">{source.identifierLabel}</dt>
                  <dd className="font-mono text-xs">{source.identifierValue ?? "—"}</dd>
                  <dt className="text-muted-foreground">Physical store</dt>
                  <dd>Not identified</dd>
                </dl>
                <p className="text-xs text-muted-foreground">
                  These candidates come from this source system&apos;s records. Which physical store it
                  corresponds to is a data-team decision; reviewing a candidate does not decide it.
                </p>
              </div>
            )}

            <div className="space-y-2">
              <h4 className="text-xs font-medium text-muted-foreground">Source values (as the workbook holds them)</h4>
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
            </div>

            <div className="space-y-2">
              <h4 className="text-xs font-medium text-muted-foreground">Derivation detail</h4>
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
            </div>

            {candidate.legacy_mappings.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-xs font-medium text-muted-foreground">Legacy mapping — current EDI authority</h4>
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
              </div>
            )}

            {candidate.proposed_units_accounted_for !== null && (
              <div className="space-y-1">
                <h4 className="text-xs font-medium text-muted-foreground">Pending proposal</h4>
                <p className="text-sm text-muted-foreground">
                  {candidate.proposed_by} proposed{" "}
                  <span className="tabular-nums text-foreground">
                    {candidate.proposed_units_accounted_for}
                  </span>
                  {candidate.proposed_note ? ` · ${candidate.proposed_note}` : ""}
                </p>
              </div>
            )}
          </section>

          <section className="space-y-3 border-t pt-4">
            <h3 className="text-sm font-medium">Commercial interpretation</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Derived basis</dt>
              <dd>{basisLabel(candidate.commercial_unit_basis)}</dd>
              <dt className="text-muted-foreground">Derived multiplier</dt>
              <dd className="tabular-nums">{candidate.units_accounted_for ?? "not established"}</dd>
            </dl>
            <p className="text-sm text-muted-foreground">
              {basisLabel(candidate.commercial_unit_basis)}
              {candidate.units_accounted_for !== null
                ? ` → multiplier ${candidate.units_accounted_for}`
                : " → no multiplier"}
              . Derived, not printed in the workbook.
            </p>
            {(candidate.conflict_explanation || evidence?.notes) && (
              <p className="text-sm text-muted-foreground">
                {candidate.conflict_explanation ?? evidence?.notes}
              </p>
            )}

            {!settled && (
              <>
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
              </>
            )}

            <div className="rounded-md bg-muted/40 p-3" data-testid="commercial-impact">
              <h4 className="text-xs font-medium text-muted-foreground">Operational consequence</h4>
              <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                <dt className="text-muted-foreground">Case cost</dt>
                <dd className="tabular-nums">
                  {candidate.case_cost === null ? "—" : `$${candidate.case_cost.toFixed(2)}`}
                  <span className="text-muted-foreground"> · {costLabel(candidate.cost_basis)}</span>
                </dd>
                <dt className="text-muted-foreground">Multiplier</dt>
                <dd className="tabular-nums">{multiplier ?? "—"}</dd>
                <dt className="text-muted-foreground">Selling-unit cost</dt>
                <dd className="tabular-nums">
                  {unitCost ? `$${unitCost.value}${unitCost.exact ? "" : " (rounded to the cent)"}` : "—"}
                </dd>
              </dl>
              <p className="mt-1 text-xs text-muted-foreground">
                {unitCost
                  ? "Case cost ÷ multiplier, computed exactly. PDI multiplies Item Retail by the multiplier."
                  : candidate.case_cost === null
                    ? "No case cost is established, so no selling-unit cost is shown."
                    : "Choose an interpretation to see the selling-unit cost."}
              </p>
            </div>
          </section>

          {detail.data?.history.length ? (
            <section className="space-y-2 border-t pt-4">
              <h3 className="text-sm font-medium">Decision history</h3>
              <ol className="space-y-2 text-sm" data-testid="review-history">
                {detail.data.history.map((entry, index) => {
                  const reinterpreted = entry.previous_commercial_unit_basis !== entry.new_commercial_unit_basis
                    || entry.previous_units_accounted_for !== entry.new_units_accounted_for;
                  return (
                    <li key={index} className="rounded-md border px-3 py-2">
                      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                        <span className="font-medium">{DECISION_LABEL[entry.decision] ?? entry.decision}</span>
                        <span className="text-xs text-muted-foreground tabular-nums">{formatDateTime(entry.decided_at)}</span>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        by {entry.reviewer}{entry.reviewer_role ? ` · ${roleLabel(entry.reviewer_role)}` : ""} ·{" "}
                        {STATE_LABEL[entry.previous_approval_state] ?? entry.previous_approval_state} →{" "}
                        {STATE_LABEL[entry.new_approval_state] ?? entry.new_approval_state}
                      </div>
                      {reinterpreted ? (
                        <div className="text-xs text-muted-foreground">
                          {basisLabel(entry.previous_commercial_unit_basis)} ×{entry.previous_units_accounted_for ?? "—"} →{" "}
                          {basisLabel(entry.new_commercial_unit_basis)} ×{entry.new_units_accounted_for ?? "—"}
                        </div>
                      ) : null}
                      {entry.note ? <div className="mt-1">{entry.note}</div> : null}
                    </li>
                  );
                })}
              </ol>
            </section>
          ) : null}

          {(!settled || canDecide) && (
            <section className="space-y-3 border-t pt-4">
              <h3 className="text-sm font-medium">{settled ? "Reconsider" : "Decision"}</h3>
              <div className="space-y-2">
                <label className="text-sm font-medium" htmlFor="note">
                  {settled ? "Reason for reconsideration" : canDecide ? "Decision basis" : "Proposal basis"}
                </label>
                <Input
                  id="note" value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder={settled ? "Why this decision is reconsidered"
                    : canDecide ? "What this decision rests on" : "What your proposal rests on"}
                  aria-required
                  disabled={busy || stale}
                />
                <p className="text-xs text-muted-foreground">
                  {settled ? "Required. Recorded with the reopening; the decision itself stays in the history."
                    : canDecide ? "Required. Recorded with an approval, or as the reason for a rejection."
                      : "Required. Recorded with your proposal for the reviewing manager."}
                </p>
              </div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm" data-testid="decision-maker">
                <dt className="text-muted-foreground">{canDecide ? "Decision by" : "Proposal by"}</dt>
                <dd>
                  <span className="font-medium">{user?.username ?? "—"}</span>
                  <span className="text-muted-foreground"> · {roleLabel(user?.role)}</span>
                  <div className="text-xs text-muted-foreground">Recorded from your signed-in account.</div>
                </dd>
              </dl>
            </section>
          )}
          {settled && !canDecide && (
            <p className="border-t pt-4 text-sm text-muted-foreground">
              This mapping has been decided. Only a manager can reopen it for reconsideration.
            </p>
          )}

          <div className="sticky -bottom-5 -mx-5 -mb-5 space-y-2 border-t bg-popover px-5 pt-3 pb-5">
            {stale ? (
              <p className="text-sm text-danger" role="alert">
                This mapping changed since you opened it — someone decided, proposed or reopened it.
                Nothing was saved. Close it and open it again to see the latest state.
              </p>
            ) : null}
            {confirmLeave ? (
              <div className="space-y-2" role="alert" data-testid="leave-warning">
                <p className="text-sm">You have unsaved notes. Leave without saving them?</p>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => setConfirmLeave(false)}>Stay</Button>
                  <Button variant="ghost" onClick={onClose}>Leave</Button>
                </div>
              </div>
            ) : confirming ? (
              <div className="space-y-2" role="alert" data-testid="decision-confirmation">
                <p className="text-sm">{confirmText[confirming]}</p>
                <p className="text-xs text-muted-foreground">
                  Decision by {user?.username ?? "—"} · {roleLabel(user?.role)}. Basis: “{note.trim()}”
                </p>
                <div className="flex gap-2">
                  <Button onClick={confirmAction[confirming]} disabled={busy}>{confirmLabel[confirming]}</Button>
                  <Button variant="outline" onClick={() => setConfirming(null)} disabled={busy}>Go back</Button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap gap-2">
                  {!settled && canDecide ? (
                    <>
                      <Button onClick={() => setConfirming("approve")} disabled={busy || stale || !basisGiven}>
                        Approve candidate
                      </Button>
                      <Button variant="outline" onClick={() => setConfirming("reject")} disabled={busy || stale || !basisGiven}>
                        Reject
                      </Button>
                    </>
                  ) : null}
                  {!settled && !canDecide ? (
                    <Button onClick={submitPropose} disabled={busy || stale || !proposalReady}>
                      Submit proposal
                    </Button>
                  ) : null}
                  {settled && canDecide ? (
                    <Button variant="outline" onClick={() => setConfirming("reopen")} disabled={busy || stale || !basisGiven}>
                      Reopen for reconsideration
                    </Button>
                  ) : null}
                  <Button variant="ghost" className="ml-auto" onClick={attemptClose}>Back</Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {settled ? "A decided mapping changes only through a recorded reconsideration."
                    : canDecide ? "Approval records a master-data decision. It does not change EDI output."
                      : "Your proposal is reviewed by a manager before it becomes authoritative."}
                </p>
              </>
            )}
          </div>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}
