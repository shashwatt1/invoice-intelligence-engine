import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProposalDetail } from "@/api/types";

/**
 * Correcting units per case on the proposal page: an unchanged value approves
 * the proposal as before; a changed value is saved as a revision and that
 * revision is approved; "save without approving" only revises; a failed
 * approval after a saved revision says so and never claims success.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { username: "barj", role: "MANAGER" }, isLoading: false, hasRole: () => true }),
}));
const api = { approveProposal: vi.fn(), rejectProposal: vi.fn(), reviseProposal: vi.fn() };
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  approveProposal: (...a: unknown[]) => api.approveProposal(...a),
  rejectProposal: (...a: unknown[]) => api.rejectProposal(...a),
  reviseProposal: (...a: unknown[]) => api.reviseProposal(...a),
}));

const { toast } = await import("sonner");
const { DecisionPanel } = await import("./decision-panel");

const STORE = { id: "s1", code: "LG-RCM", name: "LG - RCM" } as unknown as ProposalDetail["store"];
const P: ProposalDetail = {
  id: "p1", store: STORE, entity_type: "product", entity_key: "72518172210", field: "units_per_case",
  proposed_value: 24, current_value: null, source: "invoice_review" as ProposalDetail["source"],
  source_file: null, source_sheet: null, source_row: null, invoice_id: "i1", description: "Lucas Muecas Pepino",
  invoice_deleted: false, proposed_by: "vivek", status: "PENDING" as ProposalDetail["status"],
  reviewed_by: null, reviewed_at: null, review_note: null, created_at: "2026-10-07T15:05:00Z",
  evidence: null, current_master_value: null,
} as unknown as ProposalDetail;

function Where() {
  return <output data-testid="where">{useLocation().pathname}</output>;
}

function wrap(proposal: ProposalDetail = P) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/data-review/proposals/p1"]}>
        <DecisionPanel proposal={proposal} />
        <Routes><Route path="*" element={<Where />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const decided = (id: string, value: number) => ({ proposal: { ...P, id, proposed_value: value, status: "APPROVED" }, applied_to: "mapping" });

beforeEach(() => vi.clearAllMocks());

describe("DecisionPanel — units per case", () => {
  it("approves the proposal unchanged when the value is not edited", async () => {
    const user = userEvent.setup();
    api.approveProposal.mockResolvedValue(decided("p1", 24));
    wrap();
    expect(screen.getByTestId("decision-units")).toHaveValue("24");
    await user.click(screen.getByTestId("decision-approve"));
    await user.click(screen.getByRole("button", { name: "Approve and write mapping" }));
    await waitFor(() => expect(api.approveProposal).toHaveBeenCalledWith("p1", { reviewed_by: "barj", note: null }));
    expect(api.reviseProposal).not.toHaveBeenCalled();
  });

  it("saves a changed value as a revision and approves the revision", async () => {
    const user = userEvent.setup();
    api.reviseProposal.mockResolvedValue({ proposal: { ...P, id: "p2", proposed_value: 12 }, superseded: { ...P, status: "REJECTED" } });
    api.approveProposal.mockResolvedValue(decided("p2", 12));
    wrap();
    await user.clear(screen.getByTestId("decision-units"));
    await user.type(screen.getByTestId("decision-units"), "12");
    expect(screen.getByTestId("decision-units-changed")).toHaveTextContent("Proposed 24 → your value 12");
    expect(screen.getByRole("button", { name: /Reject/ })).toBeDisabled();
    await user.click(screen.getByTestId("decision-approve"));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Your value 12 replaces the proposed 24");
    await user.click(screen.getByRole("button", { name: "Approve and write mapping" }));
    await waitFor(() => expect(api.approveProposal).toHaveBeenCalledWith("p2", { reviewed_by: "barj", note: null }));
    expect(api.reviseProposal).toHaveBeenCalledWith("p1", { proposed_value: 12, proposed_by: "barj", note: null });
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/data-review/proposals/p2"));
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining("Approved 12"));
  });

  it("saves without approving when asked", async () => {
    const user = userEvent.setup();
    api.reviseProposal.mockResolvedValue({ proposal: { ...P, id: "p3", proposed_value: 6 }, superseded: { ...P, status: "REJECTED" } });
    wrap();
    await user.clear(screen.getByTestId("decision-units"));
    await user.type(screen.getByTestId("decision-units"), "6");
    await user.click(screen.getByTestId("decision-save-only"));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/data-review/proposals/p3"));
    expect(api.approveProposal).not.toHaveBeenCalled();
  });

  it("reports a saved revision whose approval failed, without claiming success", async () => {
    const user = userEvent.setup();
    api.reviseProposal.mockResolvedValue({ proposal: { ...P, id: "p4", proposed_value: 10 }, superseded: { ...P, status: "REJECTED" } });
    api.approveProposal.mockRejectedValue(new Error("Product Master governs this item"));
    wrap();
    await user.clear(screen.getByTestId("decision-units"));
    await user.type(screen.getByTestId("decision-units"), "10");
    await user.click(screen.getByTestId("decision-approve"));
    await user.click(screen.getByRole("button", { name: "Approve and write mapping" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("saved as a new pending proposal, but approving it failed")));
    expect(toast.success).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/data-review/proposals/p4"));
  });

  it("refuses a value outside 1–9999", async () => {
    const user = userEvent.setup();
    wrap();
    await user.clear(screen.getByTestId("decision-units"));
    await user.type(screen.getByTestId("decision-units"), "0");
    expect(screen.getByTestId("decision-approve")).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("A whole number from 1 to 9999");
  });

  it("offers no units field for a proposal that is not pending", () => {
    wrap({ ...P, status: "APPROVED" } as ProposalDetail);
    expect(screen.queryByTestId("decision-units")).toBeNull();
  });
});
