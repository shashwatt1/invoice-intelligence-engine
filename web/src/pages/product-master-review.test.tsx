import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
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
  id: "c1", product_id: "p1",
  product_name: "TEST LAGER 4/6 16OZ", product_name_basis: "CANONICAL",
  product_name_source: null, product_name_reference: "Sheet1 row 4",
  product_name_variant_count: 0, product_name_variants: [],
  canonical_identifier: "018200001154", pdi_item_code: "01820000115",
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
  id: "c2", product_id: "p2",
  // A commercial CONFLICT whose disputed pack size shows in the names too.
  product_name: "Multiple source names", product_name_basis: "AMBIGUOUS_SOURCE",
  product_name_source: "distributor price sheet", product_name_reference: null,
  product_name_variant_count: 2,
  product_name_variants: [
    { description: "TEST GUMBALL C12 19.2OZ", source_class: "distributor price sheet",
      references: ["Monarch Frontline row 14"] },
    { description: "TEST GUMBALL C24 19.2OZ", source_class: "distributor price sheet",
      references: ["Monarch Package row 59"] },
  ],
  canonical_identifier: "018200967214", pdi_item_code: "01820096721",
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

const NAMELESS: CommercialCandidateRow = {
  ...SETTLED,
  id: "c3", product_id: "p3",
  product_name: null, product_name_basis: "UNAVAILABLE",
  product_name_source: null, product_name_reference: null,
  product_name_variant_count: 0, product_name_variants: [],
  canonical_identifier: "070000000017", pdi_item_code: "07000000001",
};

const SOURCE_ONE: CommercialCandidateRow = {
  ...SETTLED,
  id: "c4", product_id: "p4",
  product_name: "TEST CIDER C24 12OZ 6P", product_name_basis: "SOURCE",
  product_name_source: "distributor price sheet", product_name_reference: "Monarch Package row 171",
  product_name_variant_count: 0, product_name_variants: [],
  canonical_identifier: "087000000011", pdi_item_code: "08700000001",
};

const FIVE_FLAVOURS: CommercialCandidateRow = {
  ...SETTLED,
  id: "c5", product_id: "p5",
  product_name: "Multiple source names", product_name_basis: "AMBIGUOUS_SOURCE",
  product_name_source: "distributor price sheet", product_name_reference: null,
  product_name_variant_count: 5,
  product_name_variants: ["APRICOT", "BLUEBERRY", "CHERRY", "DATE", "ELDERFLOWER"].map((flavour, i) => ({
    description: `TEST BREWERY ${flavour} C24 12OZ 6P`, source_class: "distributor price sheet",
    references: [`Monarch Package row ${100 + i}`],
  })),
  canonical_identifier: "083000000019", pdi_item_code: "08300000001",
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
      descriptions: [
        { role: "SOURCE", description: "TEST ULTRA 18PK CAN", source_system: "distributor_price_sheet",
          source_file: "Beer Inventory.xlsx", source_sheet: "Monarch Package", source_row: 12 },
        { role: "SOURCE", description: "Test ultra 18cans", source_system: "item_sales_summary",
          source_file: "Item_Sales_Summary.xlsx", source_sheet: "data", source_row: 88 },
      ],
    },
    history: [],
  });
  approveCommercialCandidate.mockResolvedValue({});
  rejectCommercialCandidate.mockResolvedValue({});
  proposeCommercialCandidate.mockResolvedValue({});
  currentRole = "MANAGER";
});

describe("Product Master product names", () => {
  it("shows the product name as its own column, ahead of the identifiers", async () => {
    renderPage();
    expect(await screen.findByText("TEST LAGER 4/6 16OZ")).toBeInTheDocument();
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers.slice(0, 2)).toEqual(["Product name", "UPC / PDI item"]);
    // The identifiers stay visible beside the name — the name is not the identity.
    const row = screen.getByText("TEST LAGER 4/6 16OZ").closest("tr")!;
    expect(within(row).getByText("018200001154")).toBeInTheDocument();
    expect(within(row).getByText("01820000115")).toBeInTheDocument();
  });

  it("marks a canonical name as canonical", async () => {
    renderPage();
    const row = (await screen.findByText("TEST LAGER 4/6 16OZ")).closest("tr")!;
    expect(within(row).getByText("Canonical")).toBeInTheDocument();
    expect(within(row).queryByText(/source-derived|multiple source names/i)).not.toBeInTheDocument();
  });

  it("marks a single source wording as source-derived", async () => {
    listCommercialCandidates.mockResolvedValue({ items: [SOURCE_ONE], total: 1, page: 1, page_size: 25 });
    renderPage();
    const row = (await screen.findByText("TEST CIDER C24 12OZ 6P")).closest("tr")!;
    expect(within(row).getByText("Source-derived")).toBeInTheDocument();
  });

  it("shows materially different source names as themselves and picks none", async () => {
    renderPage();
    const row = (await screen.findByText("TEST GUMBALL C12 19.2OZ")).closest("tr")!;
    expect(within(row).getByText("Multiple source names — review")).toBeInTheDocument();
    // Both disputed pack sizes are on the row; neither is presented as the name.
    expect(within(row).getByText("TEST GUMBALL C24 19.2OZ")).toBeInTheDocument();
    expect(row).toHaveAttribute("data-conflict");
    expect(within(row).getByText("Conflicting evidence")).toBeInTheDocument();
  });

  it("lists several flavours in the queue and points to the rest", async () => {
    listCommercialCandidates.mockResolvedValue({ items: [FIVE_FLAVOURS], total: 1, page: 1, page_size: 25 });
    renderPage();
    const row = (await screen.findByText("TEST BREWERY APRICOT C24 12OZ 6P")).closest("tr")!;
    expect(within(row).getByText("TEST BREWERY BLUEBERRY C24 12OZ 6P")).toBeInTheDocument();
    expect(within(row).getByText("TEST BREWERY CHERRY C24 12OZ 6P")).toBeInTheDocument();
    expect(within(row).getByText("and 2 more — open Review to see all 5")).toBeInTheDocument();
  });

  it("says plainly when no name exists rather than inventing one", async () => {
    listCommercialCandidates.mockResolvedValue({ items: [NAMELESS], total: 1, page: 1, page_size: 25 });
    renderPage();
    const row = (await screen.findByText("Product name unavailable")).closest("tr")!;
    expect(within(row).getByText("No name on record")).toBeInTheDocument();
    expect(within(row).getByText("070000000017")).toBeInTheDocument();
  });

  it("searches by name as well as UPC", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("TEST LAGER 4/6 16OZ");
    await user.type(screen.getByPlaceholderText(/search upc, item code or name/i), "lager");
    await vi.waitFor(() => {
      expect(listCommercialCandidates).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: "lager" }),
      );
    });
  });

  it("lists every source name with its provenance in the evidence panel", async () => {
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("TEST GUMBALL C12 19.2OZ")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("heading", { name: "Multiple source names" })).toBeInTheDocument();
    // The UPC identifies the product: in the header, the Product section and the source row.
    expect(within(dialog).getAllByText("018200967214").length).toBeGreaterThanOrEqual(2);
    expect(within(dialog).getByText("Source names for this UPC")).toBeInTheDocument();
    expect(within(dialog).getByText("Monarch Frontline row 14")).toBeInTheDocument();
    expect(within(dialog).getByText("Monarch Package row 59")).toBeInTheDocument();
    expect(within(dialog).getByText(/2 materially different names .* none is chosen/)).toBeInTheDocument();
    expect(within(dialog).getByText(/no single name is shown/i)).toBeInTheDocument();
    // Every description on record, canonical and source kept apart.
    expect(await within(dialog).findByText("Descriptions on record")).toBeInTheDocument();
    expect(within(dialog).getByText("Test ultra 18cans")).toBeInTheDocument();
    expect(within(dialog).getByText(/identified by its UPC/)).toBeInTheDocument();
    // SOURCE → DERIVED evidence is still there.
    expect(within(dialog).getByText(/Source values/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/Derived, not printed in the workbook/i)).toBeInTheDocument();
  });

  it("shows a USER the same names and evidence, with no approve or reject", async () => {
    currentRole = "USER";
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("TEST GUMBALL C12 19.2OZ")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));

    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Source names for this UPC")).toBeInTheDocument();
    expect(await within(dialog).findByText("Descriptions on record")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /approve candidate/i })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /submit proposal/i })).toBeInTheDocument();
  });
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
    await user.type(screen.getByLabelText(/decision basis/i), "Checked the distributor sheet");
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
    await user.type(screen.getByLabelText(/decision basis/i), "Items/case column states 1");
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

    await user.type(await screen.findByLabelText(/decision basis/i), "Source sheet is stale");
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

describe("rows per page", () => {
  type Asked = { page: number; page_size: number; review_status?: string; search?: string };
  const asked = () => listCommercialCandidates.mock.calls.map(([params]) => params as Asked);
  const lastAsked = () => asked()[asked().length - 1];

  /** The server's paging, over a queue of `total` ready-for-review rows. */
  function serverQueue(total: number) {
    listCommercialCandidates.mockImplementation(async ({ page, page_size }: Asked) => {
      const start = (page - 1) * page_size;
      const count = Math.max(0, Math.min(page_size, total - start));
      return {
        items: Array.from({ length: count }, (_, i) => ({ ...SETTLED, id: `row-${start + i}` })),
        total, page, page_size,
      };
    });
  }

  const rowsShown = () => screen.getAllByRole("button", { name: "Review" }).length;

  async function choosePageSize(user: ReturnType<typeof userEvent.setup>, size: number) {
    await user.click(await screen.findByRole("combobox", { name: "Rows per page" }));
    await user.click(await screen.findByRole("option", { name: String(size) }));
  }

  async function goToPage2(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole("button", { name: "Next page" }));
    await screen.findByText("26–50 of 412");
    expect(lastAsked()).toMatchObject({ page: 2, page_size: 25 });
  }

  beforeEach(() => serverQueue(412));

  it("defaults to 25 rows and offers exactly 25, 50, 100, 250 and 500", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText("1–25 of 412")).toBeInTheDocument();
    expect(asked()[0]).toMatchObject({ page: 1, page_size: 25, review_status: "READY_FOR_REVIEW" });
    expect(rowsShown()).toBe(25);
    expect(screen.getByRole("combobox", { name: "Rows per page" })).toHaveTextContent("25");
    await user.click(screen.getByRole("combobox", { name: "Rows per page" }));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(options).toEqual(["25", "50", "100", "250", "500"]);
  });

  it.each([
    [50, "1–50 of 412"],
    [100, "1–100 of 412"],
    [250, "1–250 of 412"],
  ])("asks the server for %i rows and shows %s", async (size, range) => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("1–25 of 412");
    await choosePageSize(user, size);
    expect(await screen.findByText(range)).toBeInTheDocument();
    expect(lastAsked()).toMatchObject({ page: 1, page_size: size, review_status: "READY_FOR_REVIEW" });
    expect(rowsShown()).toBe(size);
    expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled();
  });

  it("shows all 412 ready-for-review rows on one page at 500, and can go back to fewer", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("1–25 of 412");
    await choosePageSize(user, 500);
    expect(await screen.findByText("1–412 of 412")).toBeInTheDocument();
    expect(lastAsked()).toMatchObject({ page: 1, page_size: 500 });
    expect(rowsShown()).toBe(412);
    // One page: no paging buttons, but the count and the choice stay.
    expect(screen.queryByRole("button", { name: "Next page" })).not.toBeInTheDocument();
    await choosePageSize(user, 25);
    expect(await screen.findByText("1–25 of 412")).toBeInTheDocument();
  });

  it("returns to page 1 when the page size changes, with one request for the new size", async () => {
    const user = userEvent.setup();
    renderPage();
    await goToPage2(user);
    await choosePageSize(user, 100);
    expect(await screen.findByText("1–100 of 412")).toBeInTheDocument();
    expect(lastAsked()).toMatchObject({ page: 1, page_size: 100 });
    expect(asked().filter((a) => a.page_size === 100)).toHaveLength(1);
  });

  it("keeps the chosen size while paging", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("1–25 of 412");
    await choosePageSize(user, 100);
    await screen.findByText("1–100 of 412");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("101–200 of 412")).toBeInTheDocument();
    expect(lastAsked()).toMatchObject({ page: 2, page_size: 100 });
  });

  it("returns to page 1 when the search changes", async () => {
    const user = userEvent.setup();
    renderPage();
    await goToPage2(user);
    await user.type(screen.getByPlaceholderText("Search UPC, item code or name"), "LAGER");
    await waitFor(() => expect(lastAsked()).toMatchObject({ page: 1, page_size: 25, search: "LAGER" }));
  });

  it.each([
    ["review status", 0, "Conflict"],
    ["commercial unit", 1, "Case is the selling unit"],
    ["cost status", 2, "Cost established"],
  ])("returns to page 1 when the %s filter changes", async (_name, index, option) => {
    const user = userEvent.setup();
    renderPage();
    await goToPage2(user);
    await user.click(screen.getAllByRole("combobox")[index]);
    await user.click(await screen.findByRole("option", { name: option }));
    await waitFor(() => expect(lastAsked()).toMatchObject({ page: 1, page_size: 25 }));
  });
});

describe("Product Master review — accountable decisions", () => {
  async function openCandidate(identifier: string) {
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText(identifier)).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: /review/i }));
    return { user, dialog: await screen.findByRole("alertdialog") };
  }

  it("orders the panel identity → evidence → interpretation → decision", async () => {
    const { dialog } = await openCandidate("018200967214");
    const headings = within(dialog).getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["Product identity", "Source evidence", "Commercial interpretation", "Decision"]);
  });

  it("keeps Approve and Reject unavailable until the decision basis is written", async () => {
    const { user, dialog } = await openCandidate("018200001154");
    const approve = within(dialog).getByRole("button", { name: /approve candidate/i });
    const reject = within(dialog).getByRole("button", { name: /^reject$/i });
    expect(approve).toBeDisabled();
    expect(reject).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/decision basis/i), "   ");
    expect(approve).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/decision basis/i), "Sheet1 items/case states 4");
    expect(approve).toBeEnabled();
    expect(reject).toBeEnabled();
    await user.click(approve);
    expect(approveCommercialCandidate).toHaveBeenCalledWith(
      "c1", expect.objectContaining({ note: "Sheet1 items/case states 4" }),
    );
  });

  it("shows who decides from the signed-in account, with nothing to type", async () => {
    currentRole = "ADMIN";
    const { dialog } = await openCandidate("018200001154");
    const maker = within(dialog).getByTestId("decision-maker");
    expect(maker).toHaveTextContent("Decision by");
    expect(maker).toHaveTextContent("tester · Administrator");
    expect(maker).toHaveTextContent("Recorded from your signed-in account.");
    expect(within(dialog).queryByLabelText(/name|reviewer|approved by/i)).not.toBeInTheDocument();
  });

  it("states the operational consequence as exact arithmetic", async () => {
    // SETTLED: case cost 26.45, derived multiplier 4 → 6.6125 → $6.61, rounded.
    const { user, dialog } = await openCandidate("018200001154");
    const impact = within(dialog).getByTestId("commercial-impact");
    expect(impact).toHaveTextContent("$26.45");
    expect(impact).toHaveTextContent("Selling-unit cost$6.61 (rounded to the cent)");
    // Choosing "case is the selling unit" makes the multiplier 1 and the unit cost the case cost.
    await user.click(within(dialog).getByRole("combobox", { name: /commercial interpretation/i }));
    await user.click(await screen.findByRole("option", { name: /case is the selling unit/i }));
    expect(impact).toHaveTextContent("Multiplier1");
    expect(impact).toHaveTextContent("Selling-unit cost$26.45");
  });

  it("shows no unit cost for a conflict until an interpretation is chosen, and never guesses one", async () => {
    const { dialog } = await openCandidate("018200967214");
    const impact = within(dialog).getByTestId("commercial-impact");
    expect(impact).toHaveTextContent("Selling-unit cost—");
    expect(impact).toHaveTextContent("No case cost is established");
  });

  it("lets a MANAGER decide a candidate that has a pending proposal", async () => {
    listCommercialCandidates.mockResolvedValue({
      items: [{ ...SETTLED, approval_state: "PENDING", review_status: "PENDING",
                proposed_units_accounted_for: 12, proposed_by: "vivek", proposed_note: "from the sheet" }],
      total: 1, page: 1, page_size: 25,
    });
    const { dialog } = await openCandidate("018200001154");
    expect(within(dialog).getByText(/vivek proposed/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /approve candidate/i })).toBeInTheDocument();
  });

  it("shows the reviewer's role in the decision history", async () => {
    getCommercialCandidate.mockResolvedValue({
      candidate: SETTLED,
      evidence: { notes: null, source_statements: [], governed_units_observed: [], supporting_rows: [],
                  source_file: null, source_sheet: null, source_row: null, source_snapshot_rows: [],
                  admissible_evidence: [], inadmissible_evidence: [], descriptions: [] },
      history: [{ decision: "PROPOSE", previous_approval_state: "REVIEW_REQUIRED", new_approval_state: "PENDING",
                  previous_commercial_unit_basis: "UNIT_IS_SELLING_UNIT", new_commercial_unit_basis: "UNIT_IS_SELLING_UNIT",
                  previous_units_accounted_for: 4, new_units_accounted_for: 12, reviewer: "vivek",
                  reviewer_role: "USER", note: "from the sheet", decided_at: "2026-09-30T10:00:00Z" }],
    });
    const { dialog } = await openCandidate("018200001154");
    expect(await within(dialog).findByText(/propose by vivek \(User\)/)).toBeInTheDocument();
  });
});
