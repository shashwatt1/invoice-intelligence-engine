import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { CommercialCandidateRow } from "@/api/types";

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({
  toast: { success: (...a: unknown[]) => toastSuccess(...a), error: (...a: unknown[]) => toastError(...a) },
}));

let currentRole = "MANAGER";
vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: { username: "barj", role: currentRole } }) }));

const listCommercialCandidates = vi.fn();
const bulkApproveCommercialCandidates = vi.fn();
const listStores = vi.fn();
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listCommercialCandidates: (...a: unknown[]) => listCommercialCandidates(...a),
  bulkApproveCommercialCandidates: (...a: unknown[]) => bulkApproveCommercialCandidates(...a),
  listStores: (...a: unknown[]) => listStores(...a),
  getCommercialCandidate: vi.fn(() => new Promise(() => {})),
}));

const { ProductMasterApprovalsPage } = await import("./product-master-approvals");

function row(id: string, upc: string, extra: Partial<CommercialCandidateRow> = {}): CommercialCandidateRow {
  return {
    id, product_id: `p-${id}`, product_name: `PRODUCT ${id}`, product_name_basis: "CANONICAL",
    product_name_source: null, product_name_reference: null, product_name_variant_count: 0, product_name_variants: [],
    canonical_identifier: upc, pdi_item_code: upc.slice(1), store_id: "s1", store_label: "Item Sales · 47708760",
    store_identity_status: "unresolved", store_kind: "source_identity",
    store_source_identity: { source_system: "item_sales", source_label: "Item Sales", identifier_type: "store_code",
                             identifier_value: "47708760", label: "Item Sales · 47708760" },
    commercial_unit_basis: "UNIT_IS_SELLING_UNIT", units_accounted_for: 4, case_cost: 26.45,
    cost_basis: "DISTRIBUTOR_CASE_PRICE", approval_state: "REVIEW_REQUIRED", evidence_state: "REVIEW_REQUIRED",
    is_conflict: false, requires_resolution: false, review_status: "READY_FOR_REVIEW", legacy_agreement: "AGREES",
    conflict_explanation: null, reviewed_by: null, reviewed_at: null, proposed_units_accounted_for: null,
    proposed_by: null, proposed_note: null, proposed_at: null, review_version: 0, last_decision: null,
    bulk_eligible: true, legacy_mappings: [],
    ...extra,
  } as CommercialCandidateRow;
}

const READY_A = row("a", "018200000001");
const READY_B = row("b", "018200000002", { review_version: 2 });
const CONFLICT = row("c", "018200000003", {
  commercial_unit_basis: "CONFLICT", units_accounted_for: null, is_conflict: true, requires_resolution: true,
  review_status: "CONFLICT", bulk_eligible: false,
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><ProductMasterApprovalsPage /></MemoryRouter></QueryClientProvider>);
}

const rowFor = async (upc: string) => (await screen.findByText(upc)).closest("tr")!;

beforeEach(() => {
  vi.clearAllMocks();
  currentRole = "MANAGER";
  listStores.mockResolvedValue([]);
  listCommercialCandidates.mockResolvedValue({ items: [CONFLICT, READY_A, READY_B], total: 3, page: 1, page_size: 50 });
  bulkApproveCommercialCandidates.mockResolvedValue({ approved: 2, decisions: [] });
});

describe("Product Master approvals", () => {
  it("shows what awaits a decision; conflicts cannot be selected and there is no approve-all", async () => {
    renderPage();
    await rowFor("018200000001");
    expect(listCommercialCandidates).toHaveBeenCalledWith(expect.objectContaining({ review_status: "UNDECIDED", page: 1 }));
    const conflict = await rowFor("018200000003");
    expect(within(conflict).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(conflict).getByText("Individual review required")).toBeInTheDocument();
    expect(within(conflict).getByRole("button", { name: /review individually/i })).toBeInTheDocument();
    expect(within(await rowFor("018200000001")).getByRole("checkbox")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve all/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve Selected (0)" })).toBeDisabled();
  });

  it("select-all selects only the eligible rows", async () => {
    const user = userEvent.setup();
    renderPage();
    await rowFor("018200000001");
    await user.click(screen.getByRole("checkbox", { name: /select every eligible row/i }));
    expect(screen.getByRole("button", { name: "Approve Selected (2)" })).toBeEnabled();
  });

  it("confirms with counts and the reviewer, requires a basis, and sends each mapping with the version seen", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await rowFor("018200000001")).getByRole("checkbox"));
    await user.click(within(await rowFor("018200000002")).getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Approve Selected (2)" }));
    const dialog = await screen.findByRole("alertdialog");
    const counts = within(dialog).getByTestId("bulk-counts");
    expect(counts).toHaveTextContent("Selected2Eligible2Conflicts0Already decided0");
    expect(counts).toHaveTextContent("Reviewerbarj · Manager");
    expect(dialog).toHaveTextContent("becomes the authoritative commercial mapping");
    const approve = within(dialog).getByRole("button", { name: "Approve 2" });
    expect(approve).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/decision basis/i), "Distributor sheet, checked row by row");
    await user.click(approve);
    expect(bulkApproveCommercialCandidates).toHaveBeenCalledWith({
      items: [{ mapping_id: "a", expected_review_version: 0 }, { mapping_id: "b", expected_review_version: 2 }],
      note: "Distributor sheet, checked row by row",
    });
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Approved 2 mappings — each recorded as its own decision."));
  });

  it("tells the reviewer to refresh when the queue changed, and approves nothing", async () => {
    bulkApproveCommercialCandidates.mockRejectedValue(new ApiError(409, { error_code: "ERR_STALE_REVIEW", message: "changed" }));
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await rowFor("018200000001")).getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Approve Selected (1)" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.type(within(dialog).getByLabelText(/decision basis/i), "checked");
    await user.click(within(dialog).getByRole("button", { name: "Approve 1" }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(
      "The queue changed since you selected these rows. Nothing was approved — refresh and select again."));
    expect(await screen.findByRole("button", { name: "Approve Selected (0)" })).toBeDisabled();
  });

  it("is for managers and administrators only", async () => {
    currentRole = "USER";
    renderPage();
    expect(await screen.findByText("Managers only")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve selected/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
