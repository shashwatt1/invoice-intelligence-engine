/**
 * Review one invoice line in place: correct its product code and/or
 * description (manager+, the existing line-correction endpoint, history kept),
 * submit units per case (the existing governed proposal), and — for a manager
 * who chooses "Save & approve" — approve that proposal (the existing approval
 * endpoint). Nothing new is written anywhere: each step is an existing call,
 * in order, and the first refusal stops the rest.
 *
 * Deliberately conservative:
 *  - a code with no usable digits is refused before anything is written;
 *  - after a code change the invoice is re-read, so the decision is made on
 *    the corrected code's real state, never on the old one;
 *  - a value already pending review is never duplicated or overridden;
 *  - an approved value is never replaced in one step ("Save & approve" stops;
 *    "Save" proposes the change for Data Review);
 *  - if approval fails after the proposal was created, the proposal simply
 *    stays pending — the outcome says exactly which steps happened.
 */
import type {
  CaseMappingConfirmation,
  CaseMappingResult,
  CaseMappingRow,
  InvoiceDetail,
  LineItemCorrection,
  LineItemCorrectionResult,
  ProposalDecision,
  ProposalDecisionResult,
} from "@/api/types";

export const MIN_UNITS = 1;
export const MAX_UNITS = 9999;
export const MAX_DESCRIPTION = 500;

export interface LineReviewInput {
  invoiceId: string;
  sortOrder: number;
  /** The line as shown: stored code, its normalized form, description. */
  storedCode: string | null;
  itemCode: string | null;
  storedDescription: string | null;
  /** What the reviewer left in the fields (trimmed here). */
  code: string;
  description: string;
  /** Units per case as typed; empty means "do not submit units". */
  units: string;
  approve: boolean;
  /** MANAGER or ADMIN: may correct the line and approve. */
  isManager: boolean;
  username: string;
  note: string;
}

export interface LineReviewDeps {
  correctLine: (invoiceId: string, sortOrder: number, body: LineItemCorrection) => Promise<LineItemCorrectionResult>;
  getInvoice: (invoiceId: string) => Promise<InvoiceDetail>;
  confirm: (invoiceId: string, mappings: CaseMappingConfirmation[]) => Promise<CaseMappingResult>;
  approve: (proposalId: string, decision: ProposalDecision) => Promise<ProposalDecisionResult>;
}

export type LineReviewStatus = "invalid" | "done" | "stopped" | "failed";

export interface LineReviewOutcome {
  status: LineReviewStatus;
  /** Plain-language steps that actually happened, in order. */
  steps: string[];
  message: string;
  proposalId?: string | null;
  itemCode?: string | null;
}

/** Same test the backend applies: at least one non-zero digit. The backend stays the authority. */
export function hasUsableDigits(code: string): boolean {
  return /[1-9]/.test(code.replace(/\D/g, ""));
}

export function parseUnitsInput(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const units = Number(value.trim());
  return units >= MIN_UNITS && units <= MAX_UNITS ? units : null;
}

/** Validation that needs no network. Returns the problems, or none. */
export function validateLineReview(input: LineReviewInput): string[] {
  const problems: string[] = [];
  const code = input.code.trim();
  const description = input.description.trim();
  const codeChanged = code !== (input.storedCode ?? "").trim();
  const descriptionChanged = description !== (input.storedDescription ?? "").trim();
  const wantsUnits = input.units.trim() !== "";
  if ((codeChanged || descriptionChanged) && !input.isManager) {
    problems.push("Only a manager or admin can correct the product code or description.");
  }
  if (codeChanged && !code) problems.push("Enter a product code — clearing it is not done here.");
  if (codeChanged && code && !hasUsableDigits(code)) {
    problems.push("That product code has no usable digits, so it could not identify the product on the EDI line.");
  }
  if (descriptionChanged && !description) problems.push("The description cannot be empty.");
  if (description.length > MAX_DESCRIPTION) problems.push(`The description is limited to ${MAX_DESCRIPTION} characters.`);
  if (wantsUnits && parseUnitsInput(input.units) === null) {
    problems.push(`Units per case must be a whole number from ${MIN_UNITS} to ${MAX_UNITS}.`);
  }
  if (input.approve && !input.isManager) problems.push("Only a manager or admin can approve.");
  if (input.approve && !wantsUnits) problems.push("Enter units per case to approve.");
  if (!codeChanged && !descriptionChanged && !wantsUnits) problems.push("Nothing to save — change a field first.");
  return problems;
}

function errorText(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "the request failed";
}

export async function runLineReview(input: LineReviewInput, deps: LineReviewDeps): Promise<LineReviewOutcome> {
  const problems = validateLineReview(input);
  if (problems.length) return { status: "invalid", steps: [], message: problems.join(" ") };

  const steps: string[] = [];
  const code = input.code.trim();
  const description = input.description.trim();
  const codeChanged = code !== (input.storedCode ?? "").trim();
  const descriptionChanged = description !== (input.storedDescription ?? "").trim();
  const units = input.units.trim() === "" ? null : parseUnitsInput(input.units);
  const note = input.note.trim() || null;
  let itemCode = input.itemCode;

  // 1 — correct the line (existing endpoint; history records old → new, who, when, why).
  if (codeChanged || descriptionChanged) {
    const body: LineItemCorrection = { corrected_by: input.username, note };
    if (codeChanged) body.product_code = code;
    if (descriptionChanged) body.description = description;
    try {
      const result = await deps.correctLine(input.invoiceId, input.sortOrder, body);
      if (codeChanged) itemCode = result.item.item_code ?? null;
      steps.push(
        [codeChanged ? `product code corrected to ${code}` : null, descriptionChanged ? "description corrected" : null]
          .filter(Boolean).join(" and "),
      );
    } catch (error) {
      return { status: "failed", steps, message: `The correction was not saved: ${errorText(error)}.` };
    }
    if (codeChanged && !itemCode) {
      return {
        status: "stopped", steps, itemCode: null,
        message: "The corrected code has no usable digits for the EDI line, so no mapping was submitted.",
      };
    }
  }

  if (units === null) {
    return { status: "done", steps, itemCode, message: "Saved." };
  }
  if (!itemCode) {
    return { status: "stopped", steps, message: "This line has no usable product code, so no mapping was submitted." };
  }

  // 2 — decide on the code's real, current state (re-read after any correction).
  let current: CaseMappingRow | undefined;
  try {
    const fresh = await deps.getInvoice(input.invoiceId);
    current = fresh.case_mappings.find((row) => row.sort_order === input.sortOrder)
      ?? fresh.case_mappings.find((row) => row.item_code === itemCode);
  } catch (error) {
    return { status: "failed", steps, itemCode, message: `Could not re-read the invoice: ${errorText(error)}. Nothing was submitted.` };
  }
  if (current?.pending_value != null) {
    return {
      status: "stopped", steps, itemCode, proposalId: current.pending_proposal_id,
      message: `A value of ${current.pending_value} is already pending review for ${itemCode}. Decide it in Data Review instead of submitting another.`,
    };
  }
  if (current?.mapped && current.units_per_case !== null && current.units_per_case === units) {
    return { status: "done", steps, itemCode, message: `${itemCode} is already approved at ${units} per case — nothing to submit.` };
  }
  if (input.approve && current?.mapped && current.units_per_case !== null) {
    return {
      status: "stopped", steps, itemCode,
      message: `${itemCode} is already approved at ${current.units_per_case} per case. Changing an approved value is not done in one step — use Save to propose ${units} for Data Review.`,
    };
  }

  // 3 — the governed proposal (existing endpoint).
  let proposalId: string | null = null;
  try {
    const result = await deps.confirm(input.invoiceId, [
      { item_code: itemCode, units_per_case: units, description: description || input.storedDescription },
    ]);
    proposalId = result.case_mappings.find((row) => row.item_code === itemCode)?.pending_proposal_id ?? null;
    steps.push(`${units} per case submitted for review`);
  } catch (error) {
    return { status: "failed", steps, itemCode, message: `The mapping was not submitted: ${errorText(error)}.` };
  }
  if (!input.approve) {
    return { status: "done", steps, itemCode, proposalId, message: "Saved — awaiting approval in Data Review." };
  }
  if (!proposalId) {
    return { status: "failed", steps, itemCode, message: "Submitted, but the pending proposal could not be found to approve. It remains pending in Data Review." };
  }

  // 4 — approve that proposal (existing endpoint; manager only, decided once).
  try {
    await deps.approve(proposalId, { note: note ?? "Approved from the invoice review panel" });
    steps.push("approved");
  } catch (error) {
    return {
      status: "failed", steps, itemCode, proposalId,
      message: `Submitted, but approval failed: ${errorText(error)}. The value remains pending in Data Review.`,
    };
  }
  return { status: "done", steps, itemCode, proposalId, message: `Approved — ${itemCode} is ${units} per case for this store.` };
}
