import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CaseMappingRow, StoreRef } from "@/api/types";

/**
 * The in-place line review: correct code/description (manager), enter units,
 * Save (proposal) or Save & approve (manager) — through the existing endpoints,
 * in order, once per click; pending and approved values are never overridden.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const role = { current: "MANAGER" as "USER" | "MANAGER" | "ADMIN" };
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: { username: role.current === "USER" ? "vivek" : "barj", role: role.current },
    isLoading: false,
    hasRole: (minimum: string) => ({ USER: 0, MANAGER: 1, ADMIN: 2 })[role.current] >= ({ USER: 0, MANAGER: 1, ADMIN: 2 } as Record<string, number>)[minimum],
  }),
}));
const api = {
  correctLineItem: vi.fn(),
  getInvoice: vi.fn(),
  confirmCaseMappings: vi.fn(),
  approveProposal: vi.fn(),
};
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  correctLineItem: (...a: unknown[]) => api.correctLineItem(...a),
  getInvoice: (...a: unknown[]) => api.getInvoice(...a),
  confirmCaseMappings: (...a: unknown[]) => api.confirmCaseMappings(...a),
  approveProposal: (...a: unknown[]) => api.approveProposal(...a),
}));

const { LineReviewPanel } = await import("./line-review-panel");
const { CaseMappingCard } = await import("./case-mapping-card");

function row(extra: Partial<CaseMappingRow> = {}): CaseMappingRow {
  return {
    item_code: "01820025006", description: "BUD ICE 25OZ", pack_size: "C-15 25OZ", units_per_case: null,
    suggested_units_per_case: null, suggestion_source: null, suggestion_candidates: [], reference_description: null,
    reference_avg_cost: null, pending_proposal_id: null, pending_value: null, mapped: false, sort_order: 2,
    product_code: "018200250064", quantity: 2, unit_price: 30, line_total: 60, unit_discount: null,
    original_description: null, ...extra,
  };
}

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
}

function renderPanel(r: CaseMappingRow, onOpenChange = vi.fn()) {
  wrap(<LineReviewPanel invoiceId="inv-1" row={r} open onOpenChange={onOpenChange} />);
  return onOpenChange;
}

beforeEach(() => {
  vi.clearAllMocks();
  role.current = "MANAGER";
  api.correctLineItem.mockImplementation(async (_i: string, _s: number, body: { product_code?: string }) => ({
    item: { sort_order: 2, description: "BUD ICE 25OZ", product_code: body.product_code, item_code: "07777700001",
            quantity: 2, unit_price: 30, line_total: 60, unit_deposit: null, corrected_fields: ["product_code"] },
    status: "VALIDATED", composite_confidence: 1, failed_checks: 0, review_reasons: [], pdi_export_allowed: false,
  }));
  api.getInvoice.mockResolvedValue({ case_mappings: [row({ item_code: "07777700001" })] });
  api.confirmCaseMappings.mockImplementation(async (_i: string, m: { item_code: string; units_per_case: number }[]) => ({
    saved: 1, case_mappings: [row({ item_code: m[0].item_code, pending_proposal_id: "p-9", pending_value: m[0].units_per_case })],
    pdi_export_allowed: false, pdi_export_blocked_reason: "x",
  }));
  api.approveProposal.mockResolvedValue({});
});

const field = (name: string) => screen.getByRole("textbox", { name });

describe("line review panel", () => {
  it("shows the line, the invoice's prices and the original description", () => {
    renderPanel(row({ original_description: "BUD ICF 25OZ" }));
    expect(screen.getByTestId("line-review-description")).toHaveTextContent("BUD ICE 25OZ");
    expect(screen.getByTestId("line-review-original")).toHaveTextContent("Originally read as “BUD ICF 25OZ”");
    expect(screen.getByTestId("invoice-price")).toHaveTextContent("qty 2 · $30.00 each · line $60.00");
    expect(screen.getByText(/EDI item code now:/)).toHaveTextContent("01820025006");
  });

  it("manager: corrects the code, submits units and approves, in order, then closes", async () => {
    const user = userEvent.setup();
    const onOpenChange = renderPanel(row());
    await user.clear(field("Product code (UPC)"));
    await user.type(field("Product code (UPC)"), "077777000014");
    await user.type(field("Units per case"), "12");
    await user.click(screen.getByRole("button", { name: "Save & approve" }));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(api.correctLineItem).toHaveBeenCalledWith("inv-1", 2, { corrected_by: "barj", note: null, product_code: "077777000014" });
    expect(api.confirmCaseMappings).toHaveBeenCalledWith("inv-1", [{ item_code: "07777700001", units_per_case: 12, description: "BUD ICE 25OZ" }]);
    expect(api.approveProposal).toHaveBeenCalledWith("p-9", { note: "Approved from the invoice review panel" });
    const order = [api.correctLineItem, api.getInvoice, api.confirmCaseMappings, api.approveProposal]
      .map((fn) => fn.mock.invocationCallOrder[0]);
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it("user: code and description are locked, no approve button, Save submits units only", async () => {
    role.current = "USER";
    const user = userEvent.setup();
    renderPanel(row());
    expect(field("Product code (UPC)")).toBeDisabled();
    expect(field("Description")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save & approve" })).toBeNull();
    await user.type(field("Units per case"), "15");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.confirmCaseMappings).toHaveBeenCalledTimes(1));
    expect(api.correctLineItem).not.toHaveBeenCalled();
    expect(api.approveProposal).not.toHaveBeenCalled();
  });

  it("a value pending review locks the units and links to it", () => {
    renderPanel(row({ pending_value: 24, pending_proposal_id: "p-1" }));
    expect(field("Units per case")).toBeDisabled();
    expect(screen.getByTestId("line-review-pending")).toHaveTextContent("24 per case is already pending review");
    expect(screen.getByRole("link", { name: "Open it in Data Review" })).toHaveAttribute("href", "/data-review/proposals/p-1");
  });

  it("an approved value is not replaced in one step", async () => {
    const user = userEvent.setup();
    renderPanel(row({ mapped: true, units_per_case: 24 }));
    expect(screen.getByTestId("line-review-approved")).toHaveTextContent("Approved for this store: 24 per case.");
    await user.type(field("Units per case"), "12");
    expect(screen.getByText(/Differs from the approved value/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save & approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("submits once however quickly Save is clicked", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    api.confirmCaseMappings.mockImplementationOnce(() => new Promise((resolve) => {
      release = () => resolve({ saved: 1, case_mappings: [row({ pending_proposal_id: "p-9", pending_value: 15 })],
                                pdi_export_allowed: false, pdi_export_blocked_reason: "x" });
    }));
    renderPanel(row());
    await user.type(field("Units per case"), "15");
    const save = screen.getByRole("button", { name: "Save" });
    await user.click(save);
    await user.click(save);
    await waitFor(() => expect(api.confirmCaseMappings).toHaveBeenCalledTimes(1));
    release();
    await waitFor(() => expect(api.getInvoice).toHaveBeenCalledTimes(1));
    expect(api.confirmCaseMappings).toHaveBeenCalledTimes(1);
  });

  it("shows exactly what happened when approval fails", async () => {
    api.approveProposal.mockRejectedValueOnce(new Error("Proposal already reviewed"));
    const user = userEvent.setup();
    const onOpenChange = renderPanel(row());
    await user.type(field("Units per case"), "12");
    await user.click(screen.getByRole("button", { name: "Save & approve" }));
    const alert = await screen.findByTestId("line-review-outcome");
    expect(alert).toHaveTextContent("Done: 12 per case submitted for review.");
    expect(alert).toHaveTextContent("approval failed: Proposal already reviewed. The value remains pending in Data Review.");
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("refuses a code with no usable digits before writing anything", async () => {
    const user = userEvent.setup();
    renderPanel(row());
    await user.clear(field("Product code (UPC)"));
    await user.type(field("Product code (UPC)"), "ABC-DEF");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(api.correctLineItem).not.toHaveBeenCalled();
  });
});

describe("case-mapping card", () => {
  const STORE: StoreRef = { id: "s", label: "AF NV Midler", identity_status: "confirmed", display_name: "AF NV Midler",
                            address: null, source_codes: [] };

  it("opens the review panel for a line and lists lines without a usable code", async () => {
    const user = userEvent.setup();
    wrap(<CaseMappingCard invoiceId="inv-1" store={STORE}
                          rows={[row(), row({ item_code: null, product_code: "N/A", description: "FUEL SURCHARGE?", sort_order: 5 })]} />);
    expect(screen.getByTestId("codeless-lines")).toHaveTextContent("No usable product code (1)");
    expect(screen.getByTestId("codeless-lines")).toHaveTextContent("FUEL SURCHARGE?");
    await user.click(screen.getByRole("button", { name: /Review line 6: FUEL SURCHARGE\?/ }));
    expect(screen.getByTestId("line-review-panel")).toHaveTextContent("Review line 6");
    expect(screen.getByText(/EDI item code now:/)).toHaveTextContent("none (no usable code)");
  });

  it("shows the invoice's prices on each mapping row", () => {
    wrap(<CaseMappingCard invoiceId="inv-1" store={STORE} rows={[row({ mapped: true, units_per_case: 24, suggestion_source: "database" })]} />);
    expect(screen.getByTestId("invoice-price")).toHaveTextContent("From invoice: qty 2 · $30.00 each · line $60.00");
    expect(screen.getByTestId("invoice-price-per-unit")).toHaveTextContent("≈ $1.25 per selling unit at 24/case (calculated, confirmed)");
  });
});
