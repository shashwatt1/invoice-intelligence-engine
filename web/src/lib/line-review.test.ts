import { describe, expect, it, vi } from "vitest";

import type { CaseMappingRow, InvoiceDetail } from "@/api/types";

import { hasUsableDigits, parseUnitsInput, runLineReview, validateLineReview, type LineReviewDeps, type LineReviewInput } from "./line-review";

/**
 * Correct a line's code/description, submit units, and (manager) approve — in
 * that order, through the existing endpoints, stopping at the first refusal.
 */

const BASE: LineReviewInput = {
  invoiceId: "inv-1", sortOrder: 4, storedCode: "018200250064", itemCode: "01820025006",
  storedDescription: "BUD ICE 25OZ", code: "018200250064", description: "BUD ICE 25OZ",
  units: "", approve: false, isManager: true, username: "barj", note: "",
};

function row(extra: Partial<CaseMappingRow> = {}): CaseMappingRow {
  return {
    item_code: "01820025006", description: "BUD ICE 25OZ", pack_size: null, units_per_case: null,
    suggested_units_per_case: null, suggestion_source: null, suggestion_candidates: [], reference_description: null,
    reference_avg_cost: null, pending_proposal_id: null, pending_value: null, mapped: false, sort_order: 4, ...extra,
  };
}

function deps(over: Partial<{ current: CaseMappingRow; correctedItemCode: string | null; proposalId: string | null }> = {},
              fail: Partial<Record<keyof LineReviewDeps, boolean>> = {}) {
  const calls: string[] = [];
  const d: LineReviewDeps = {
    correctLine: vi.fn(async (_i, _s, body) => {
      calls.push(`correct:${JSON.stringify(body)}`);
      if (fail.correctLine) throw new Error("403 forbidden");
      return { item: { sort_order: 4, description: body.description ?? "BUD ICE 25OZ", product_code: body.product_code,
                       item_code: "correctedItemCode" in over ? over.correctedItemCode : "07777700001",
                       quantity: 1, unit_price: 30, line_total: 30, unit_deposit: null, corrected_fields: [] },
               status: "VALIDATED", composite_confidence: 1, failed_checks: 0, review_reasons: [], pdi_export_allowed: false } as never;
    }),
    getInvoice: vi.fn(async () => {
      calls.push("read");
      if (fail.getInvoice) throw new Error("network");
      return { case_mappings: [over.current ?? row()] } as unknown as InvoiceDetail;
    }),
    confirm: vi.fn(async (_i, mappings) => {
      calls.push(`confirm:${mappings[0].item_code}=${mappings[0].units_per_case}`);
      if (fail.confirm) throw new Error("422 invalid");
      return { saved: 1, case_mappings: [row({ item_code: mappings[0].item_code, pending_proposal_id: "proposalId" in over ? over.proposalId : "p-9", pending_value: mappings[0].units_per_case })],
               pdi_export_allowed: false, pdi_export_blocked_reason: "x" };
    }),
    approve: vi.fn(async (id, decision) => {
      calls.push(`approve:${id}:${decision.note}`);
      if (fail.approve) throw new Error("422 already decided");
      return {} as never;
    }),
  };
  return { d, calls };
}

describe("validation before anything is written", () => {
  it("checks codes, units and roles without a network call", () => {
    expect(hasUsableDigits("0-48500-20603-4")).toBe(true);
    expect([hasUsableDigits("RB-ABC"), hasUsableDigits("000000000000")]).toEqual([false, false]);
    expect([parseUnitsInput("24"), parseUnitsInput("0"), parseUnitsInput("2.5"), parseUnitsInput("10000"), parseUnitsInput("x")])
      .toEqual([24, null, null, null, null]);
    expect(validateLineReview({ ...BASE })).toEqual(["Nothing to save — change a field first."]);
    expect(validateLineReview({ ...BASE, code: "078123", isManager: false }).join(" ")).toMatch(/Only a manager or admin can correct/);
    expect(validateLineReview({ ...BASE, code: "ABC" }).join(" ")).toMatch(/no usable digits/);
    expect(validateLineReview({ ...BASE, code: "" }).join(" ")).toMatch(/clearing it is not done here/);
    expect(validateLineReview({ ...BASE, description: "   " }).join(" ")).toMatch(/cannot be empty/);
    expect(validateLineReview({ ...BASE, units: "0" }).join(" ")).toMatch(/whole number from 1 to 9999/);
    expect(validateLineReview({ ...BASE, units: "24", approve: true, isManager: false }).join(" ")).toMatch(/Only a manager or admin can approve/);
    expect(validateLineReview({ ...BASE, approve: true }).join(" ")).toMatch(/Enter units per case to approve/);
    expect(validateLineReview({ ...BASE, units: "24", isManager: false })).toEqual([]);   // a USER may submit units
  });

  it("writes nothing when invalid", async () => {
    const { d, calls } = deps();
    const out = await runLineReview({ ...BASE, code: "ABC", units: "24" }, d);
    expect(out.status).toBe("invalid");
    expect(calls).toEqual([]);
  });
});

describe("the flow", () => {
  it("corrects the code, re-reads, submits and approves — in that order", async () => {
    const { d, calls } = deps({ current: row({ item_code: "07777700001" }) });
    const out = await runLineReview({ ...BASE, code: "077777000014", units: "12", approve: true, note: "checked the photo" }, d);
    expect(out).toMatchObject({ status: "done", itemCode: "07777700001", proposalId: "p-9" });
    expect(calls).toEqual([
      `correct:${JSON.stringify({ corrected_by: "barj", note: "checked the photo", product_code: "077777000014" })}`,
      "read", "confirm:07777700001=12", "approve:p-9:checked the photo",
    ]);
    expect(out.steps).toEqual(["product code corrected to 077777000014", "12 per case submitted for review", "approved"]);
  });

  it("Save submits a pending proposal and does not approve", async () => {
    const { d, calls } = deps();
    const out = await runLineReview({ ...BASE, units: "15" }, d);
    expect(out).toMatchObject({ status: "done", proposalId: "p-9" });
    expect(out.message).toMatch(/awaiting approval/);
    expect(calls).toEqual(["read", "confirm:01820025006=15"]);
  });

  it("a USER can Save units (no correction, no approval)", async () => {
    const { d, calls } = deps();
    const out = await runLineReview({ ...BASE, units: "15", isManager: false }, d);
    expect(out.status).toBe("done");
    expect(calls).toEqual(["read", "confirm:01820025006=15"]);
  });

  it("a description-only correction keeps the original in history and submits nothing else", async () => {
    const { d, calls } = deps();
    const out = await runLineReview({ ...BASE, description: "BUD ICE 25 OZ CAN" }, d);
    expect(out.status).toBe("done");
    expect(calls).toEqual([`correct:${JSON.stringify({ corrected_by: "barj", note: null, description: "BUD ICE 25 OZ CAN" })}`]);
  });

  it("stops after the correction when the server finds no usable code", async () => {
    const { d, calls } = deps({ correctedItemCode: null });
    const out = await runLineReview({ ...BASE, code: "0001-0000-0", units: "12", approve: true }, d);
    expect(out.status).toBe("stopped");
    expect(out.message).toMatch(/no mapping was submitted/);
    expect(calls.map((c) => c.split(":")[0])).toEqual(["correct"]);
  });

  it("never duplicates a value already pending review", async () => {
    const { d, calls } = deps({ current: row({ pending_value: 24, pending_proposal_id: "p-1" }) });
    const out = await runLineReview({ ...BASE, units: "12", approve: true }, d);
    expect(out).toMatchObject({ status: "stopped", proposalId: "p-1" });
    expect(out.message).toMatch(/already pending review/);
    expect(calls).toEqual(["read"]);
  });

  it("never replaces an approved value in one step, but Save may propose the change", async () => {
    const approved = row({ mapped: true, units_per_case: 24 });
    const one = deps({ current: approved });
    const stopped = await runLineReview({ ...BASE, units: "12", approve: true }, one.d);
    expect(stopped.status).toBe("stopped");
    expect(stopped.message).toMatch(/already approved at 24/);
    expect(one.calls).toEqual(["read"]);

    const two = deps({ current: approved });
    const proposed = await runLineReview({ ...BASE, units: "12" }, two.d);
    expect(proposed.status).toBe("done");
    expect(two.calls).toEqual(["read", "confirm:01820025006=12"]);
  });

  it("does nothing when the value is already approved as entered", async () => {
    const { d, calls } = deps({ current: row({ mapped: true, units_per_case: 15 }) });
    const out = await runLineReview({ ...BASE, units: "15", approve: true }, d);
    expect(out.status).toBe("done");
    expect(out.message).toMatch(/already approved at 15/);
    expect(calls).toEqual(["read"]);
  });

  it("reports partial progress honestly when approval fails", async () => {
    const { d } = deps({}, { approve: true });
    const out = await runLineReview({ ...BASE, units: "12", approve: true }, d);
    expect(out).toMatchObject({ status: "failed", proposalId: "p-9" });
    expect(out.steps).toEqual(["12 per case submitted for review"]);
    expect(out.message).toMatch(/approval failed: 422 already decided\. The value remains pending/);
  });

  it("stops before submitting when the correction or the re-read fails", async () => {
    const a = deps({}, { correctLine: true });
    expect((await runLineReview({ ...BASE, code: "077777000014", units: "12" }, a.d)).status).toBe("failed");
    expect(a.calls.map((c) => c.split(":")[0])).toEqual(["correct"]);
    const b = deps({}, { getInvoice: true });
    const out = await runLineReview({ ...BASE, units: "12" }, b.d);
    expect(out.status).toBe("failed");
    expect(out.message).toMatch(/Nothing was submitted/);
    expect(b.calls).toEqual(["read"]);
  });

  it("does not approve when the submitted proposal cannot be found", async () => {
    const { d, calls } = deps({ proposalId: null });
    const out = await runLineReview({ ...BASE, units: "12", approve: true }, d);
    expect(out.status).toBe("failed");
    expect(calls.some((c) => c.startsWith("approve"))).toBe(false);
  });
});
