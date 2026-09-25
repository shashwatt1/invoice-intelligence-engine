import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CommercialCandidateRow } from "@/api/types";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// The panel asks who is signed in: deciding is MANAGER/ADMIN, proposing is
// open to any authenticated account.
let currentRole = "MANAGER";
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { username: "tester", role: currentRole } }),
}));

const listCommercialCandidates = vi.fn();
const getCommercialSummary = vi.fn();
const getCommercialCandidate = vi.fn();
const approveCommercialCandidate = vi.fn();
const rejectCommercialCandidate = vi.fn();
const proposeCommercialCandidate = vi.fn();

vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listCommercialCandidates: (...a: unknown[]) => listCommercialCandidates(...a),
  getCommercialSummary: (...a: unknown[]) => getCommercialSummary(...a),
  getCommercialCandidate: (...a: unknown[]) => getCommercialCandidate(...a),
  approveCommercialCandidate: (...a: unknown[]) => approveCommercialCandidate(...a),
  rejectCommercialCandidate: (...a: unknown[]) => rejectCommercialCandidate(...a),
  proposeCommercialCandidate: (...a: unknown[]) => proposeCommercialCandidate(...a),
}));

const { ProductMasterReviewPage } = await import("./product-master-review");

const SETTLED: CommercialCandidateRow = {
  id: "c1", product_id: "p1", canonical_identifier: "018200001154", pdi_item_code: "01820000115",
  store_id: "s1", store_label: "Store 47708760", store_identity_status: "unresolved",
  commercial_unit_basis: "UNIT_IS_SELLING_UNIT", units_accounted_for: 4,
  case_cost: 26.45, cost_basis: "DISTRIBUTOR_CASE_PRICE",
  approval_state: "REVIEW_REQUIRED", evidence_state: "REVIEW_REQUIRED",
  is_conflict: false, requires_resolution: false, reviewed_by: null, reviewed_at: null,
  review_status: "READY_FOR_REVIEW", legacy_agreement: "AGREES", conflict_explanation: null,
  proposed_units_accounted_for: null, proposed_by: null, proposed_note: null,
  legacy_mappings: [{ item_code: "01820000115", units_per_case: 4, description: "BUD LT", source: "APPROVED" }],
};

const CONFLICT: CommercialCandidateRow = {
  ...SETTLED,
  id: "c2", canonical_identifier: "018200967214", pdi_item_code: "01820096721",
  commercial_unit_basis: "CONFLICT", units_accounted_for: null,
  case_cost: null, cost_basis: "CONFLICTING_SOURCES",
  is_conflict: true, requires_resolution: true,
  review_status: "CONFLICT", legacy_agreement: "MASTER_HAS_NO_VALUE",
  conflict_explanation:
    "Reference data states 1, but an existing governed mapping holds [1, 18].",
  legacy_mappings: [
    { item_code: "01820096721", units_per_case: 1, description: "MICH ULTRA", source: "APPROVED" },
    { item_code: "01820096721", units_per_case: 18, description: "MICH ULTRA", source: "APPROVED" },
  ],
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><ProductMasterReviewPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listCommercialCandidates.mockResolvedValue({
    items: [CONFLICT, SETTLED], total: 2, page: 1, page_size: 25,
  });
  getCommercialSummary.mockResolvedValue({
    review_required: 2, approved: 0, rejected: 0, conflicts: 1, total: 2,
  });
  getCommercialCandidate.mockResolvedValue({
    candidate: CONFLICT,
    evidence: {
      notes: "Reference data states 1, but an existing governed mapping holds [1, 18].",
      source_statements: [{ units: 1, statement: "Sheet1 items/case column", source_sheet: "Sheet1", source_row: 60 }],
      governed_units_observed: [1, 18],
      supporting_rows: [], source_file: "Beer Inventory.xlsx", source_sheet: "Sheet1", source_row: 60,
      source_snapshot_rows: [{
        source_file: "data/reference/store_47708760/Beer Inventory.xlsx",
        source_sheet: "Sheet1", source_row: 60,
        raw_identifier: "018200967214", raw_description: "MICHELOB ULTRA 18/12 CAN",
        raw_items_case: "1.0", raw_case_cost: "16.7", raw_package: "18/12 CAN",
      }],
      admissible_evidence: ["distributor items/case column"],
      inadmissible_evidence: ["physical pack composition", "package notation", "description text", "frequency of source rows"],
    },
    history: [],
  });
  approveCommercialCandidate.mockResolvedValue({});
  rejectCommercialCandidate.mockResolvedValue({});
  proposeCommercialCandidate.mockResolvedValue({});
  currentRole = "MANAGER";
});

describe("Product Master commercial review", () => {
  it("shows candidates with their commercial meaning and the legacy EDI value beside them", async () => {
    renderPage();
    expect(await screen.findByText("018200001154")).toBeInTheDocument();
    expect(screen.getByText("Contained unit is the selling unit")).toBeInTheDocument();
    // The legacy column exists so a reviewer can see what EDI does today.
    expect(screen.getByRole("columnheader", { name: /legacy \(edi\)/i })).toBeInTheDocument();
  });

  it("states that approving does not change EDI", async () => {
    renderPage();
    expect(await screen.findByText(/does not change EDI/i)).toBeInTheDocument();
  });

  it("shows a conflict without offering a preselected multiplier", async () => {
    renderPage();
    const row = (await screen.findByText("018200967214")).closest("tr")!;
    expect(within(row).getByText("Conflicting evidence")).toBeInTheDocument();
    // No number is implied for the reviewer to accept.
    expect(within(row).queryByText("18")).not.toBeInTheDocument();
    expect(row).toHaveAttribute("data-conflict");
  });

  it("splits the queue by derived review status", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("018200967214");

    const selects = screen.getAllByRole("combobox");
    await user.click(selects[0]);
    await user.click(await screen.findByRole("option", { name: /^no multiplier$/i }));

    expect(listCommercialCandidates).toHaveBeenLastCalledWith(
      expect.objectContaining({ review_status: "NO_MULTIPLIER" }),
    );
  });

  it("shows whether the legacy mapping agrees or dissents", async () => {
    renderPage();
    const row = (await screen.findByText("018200001154")).closest("tr")!;
    expect(within(row).getByText("agrees")).toBeInTheDocument();
  });

  it("keeps cost status separate from the commercial decision", async () => {
    renderPage();
    const row = (await screen.findByText("018200967214")).closest("tr")!;
    expect(within(row).getByText("Sources disagree")).toBeInTheDocument();
  });

  it("opens the evidence panel and names what may never decide the multiplier", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("018200967214")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));

    expect(await screen.findByText(/Sheet1 row 60/)).toBeInTheDocument();
    // Appears both in the derivation note and as its own line; either satisfies the reviewer.
    expect(screen.getAllByText(/governed mapping holds/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/physical pack composition/)).toBeInTheDocument();
    expect(screen.getByText(/A package of 18 does not make the multiplier 18/)).toBeInTheDocument();
  });

  it("requires an explicit interpretation before approving a conflict", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("018200967214")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));

    expect(await screen.findByText(/did not settle this candidate/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /approve candidate/i }));

    // The UI sends no basis, so the backend refuses — nothing is guessed here.
    expect(approveCommercialCandidate).toHaveBeenCalledWith(
      "c2", expect.objectContaining({ commercial_unit_basis: null, units_accounted_for: null }),
    );
  });

  it("sends the reviewer's chosen interpretation when resolving a conflict", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("018200967214")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));

    await user.click(await screen.findByRole("combobox", { name: /commercial interpretation/i }));
    await user.click(await screen.findByRole("option", { name: /case is the selling unit/i }));
    await user.click(screen.getByRole("button", { name: /approve candidate/i }));

    expect(approveCommercialCandidate).toHaveBeenCalledWith(
      "c2", expect.objectContaining({ commercial_unit_basis: "CASE_IS_SELLING_UNIT" }),
    );
  });

  it("rejects a candidate with a note", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("018200967214")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));

    await user.type(await screen.findByLabelText(/note/i), "Source sheet is stale");
    await user.click(screen.getByRole("button", { name: /^reject$/i }));

    expect(rejectCommercialCandidate).toHaveBeenCalledWith(
      "c2", expect.objectContaining({ note: "Source sheet is stale" }),
    );
  });

  it("shows the raw source values a reviewer needs, not just the derived number", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("018200967214")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));

    // SOURCE layer — as the workbook holds it.
    expect(await screen.findByText(/Source values/i)).toBeInTheDocument();
    expect(screen.getByText("MICHELOB ULTRA 18/12 CAN")).toBeInTheDocument();
    expect(screen.getByText("18/12 CAN")).toBeInTheDocument();
    expect(screen.getByText("1.0")).toBeInTheDocument();
    // DERIVED layer — explicitly labelled as derived.
    expect(screen.getByText(/Derived, not printed in the workbook/i)).toBeInTheDocument();
  });

  it("offers a USER proposal instead of approve/reject", async () => {
    currentRole = "USER";
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("018200967214")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));

    expect(await screen.findByRole("button", { name: /submit proposal/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve candidate/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^reject$/i })).not.toBeInTheDocument();
    expect(screen.getByText(/reviewed by a manager/i)).toBeInTheDocument();
  });

  it("submits a USER proposal with the entered units", async () => {
    currentRole = "USER";
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("018200967214")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));

    await user.click(await screen.findByRole("combobox", { name: /commercial interpretation/i }));
    await user.click(await screen.findByRole("option", { name: /contained unit/i }));
    await user.type(screen.getByLabelText(/sellable units per case/i), "12");
    await user.click(screen.getByRole("button", { name: /submit proposal/i }));

    expect(proposeCommercialCandidate).toHaveBeenCalledWith(
      "c2", expect.objectContaining({ units_accounted_for: 12 }),
    );
  });

  it("filters to conflicts only", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("018200967214");

    const selects = screen.getAllByRole("combobox");
    await user.click(selects[1]);
    await user.click(await screen.findByRole("option", { name: /conflicts only/i }));

    expect(listCommercialCandidates).toHaveBeenLastCalledWith(
      expect.objectContaining({ conflicts_only: true }),
    );
  });

  it("searches by UPC", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("018200967214");
    await user.type(screen.getByPlaceholderText(/search upc/i), "018200967214");

    await vi.waitFor(() => {
      expect(listCommercialCandidates).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "018200967214" }),
      );
    });
  });
});
