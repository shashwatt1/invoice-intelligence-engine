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

  it("on a timeout tells the reviewer to check the queue, does not resend, and clears the selection", async () => {
    bulkApproveCommercialCandidates.mockRejectedValue(new ApiError(0, {
      error_code: "ERR_TIMEOUT",
      message: "The approval is still processing or may already have completed. Check the approval queue before trying again.",
    }));
    const user = userEvent.setup();
    renderPage();
    await user.click(within(await rowFor("018200000001")).getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Approve Selected (1)" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.type(within(dialog).getByLabelText(/decision basis/i), "checked");
    await user.click(within(dialog).getByRole("button", { name: "Approve 1" }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(
      "The approval is still processing or may already have completed. Check the approval queue before trying again."));
    expect(bulkApproveCommercialCandidates).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Approve Selected (0)" })).toBeDisabled();
    await waitFor(() => expect(listCommercialCandidates.mock.calls.length).toBeGreaterThan(1));   // the queue is re-read
  });

  it("is for managers and administrators only", async () => {
    currentRole = "USER";
    renderPage();
    expect(await screen.findByText("Managers only")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve selected/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

describe("selection is independent of search, filters and paging", () => {
  const READY_D = row("d", "087692000570");
  const READY_E = row("e", "018200967214", { review_version: 1 });
  const QUEUE = [CONFLICT, READY_A, READY_B, READY_D, READY_E];

  // Answers like the server: search, store and paging narrow what is returned.
  function serveQueue(items: CommercialCandidateRow[]) {
    listCommercialCandidates.mockImplementation(async (p: { search?: string; store_id?: string; page: number; page_size: number }) => {
      const matching = items.filter((r) => (!p.search || (r.canonical_identifier ?? "").includes(p.search))
                                         && (!p.store_id || r.store_id === p.store_id));
      const start = (p.page - 1) * p.page_size;
      return { items: matching.slice(start, start + p.page_size), total: matching.length, page: p.page, page_size: p.page_size };
    });
  }

  const approveButton = (n: number) => screen.findByRole("button", { name: `Approve Selected (${n})` });
  const search = async (user: ReturnType<typeof userEvent.setup>, text: string) => {
    const box = screen.getByPlaceholderText(/search upc/i);
    await user.clear(box);
    if (text) await user.type(box, text);
  };
  const checkbox = async (upc: string) => within(await rowFor(upc)).getByRole("checkbox");

  beforeEach(() => serveQueue(QUEUE));

  it("keeps the selection through a search, removes only the row unchecked, and approves exactly what is selected", async () => {
    const user = userEvent.setup();
    renderPage();
    await rowFor("018200000001");
    await user.click(screen.getByRole("checkbox", { name: /select every eligible row/i }));
    await approveButton(4);

    await search(user, "018200967214");
    await waitFor(() => expect(screen.queryByText("018200000001")).not.toBeInTheDocument());
    expect(await checkbox("018200967214")).toBeChecked();
    expect(await approveButton(4)).toBeEnabled();
    expect(screen.getByTestId("selection-summary")).toHaveTextContent("4 selected · 3 not in this view");
    await user.click(await checkbox("018200967214"));
    await approveButton(3);

    await search(user, "087692000570");
    expect(await checkbox("087692000570")).toBeChecked();
    await user.click(await checkbox("087692000570"));
    await approveButton(2);

    await search(user, "");
    expect(await checkbox("018200000001")).toBeChecked();
    expect(await checkbox("018200000002")).toBeChecked();
    expect(await checkbox("018200967214")).not.toBeChecked();
    expect(await checkbox("087692000570")).not.toBeChecked();

    await user.click(await approveButton(2));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByTestId("bulk-counts")).toHaveTextContent("Selected2Eligible2Conflicts0Already decided0");
    await user.type(within(dialog).getByLabelText(/decision basis/i), "Reviewed sheet");
    await user.click(within(dialog).getByRole("button", { name: "Approve 2" }));
    expect(bulkApproveCommercialCandidates).toHaveBeenCalledWith({
      items: [{ mapping_id: "a", expected_review_version: 0 }, { mapping_id: "b", expected_review_version: 2 }],
      note: "Reviewed sheet",
    });
  });

  it("approves selected rows that are not in the current view, each with the version it was selected at", async () => {
    const user = userEvent.setup();
    renderPage();
    await rowFor("018200000001");
    await user.click(screen.getByRole("checkbox", { name: /select every eligible row/i }));
    await search(user, "087692000570");
    await waitFor(() => expect(screen.queryByText("018200000001")).not.toBeInTheDocument());
    await user.click(await approveButton(4));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByTestId("bulk-counts")).toHaveTextContent("Selected4Eligible4");
    await user.type(within(dialog).getByLabelText(/decision basis/i), "Reviewed sheet");
    await user.click(within(dialog).getByRole("button", { name: "Approve 4" }));
    expect(bulkApproveCommercialCandidates).toHaveBeenCalledWith({
      items: [
        { mapping_id: "a", expected_review_version: 0 }, { mapping_id: "b", expected_review_version: 2 },
        { mapping_id: "d", expected_review_version: 0 }, { mapping_id: "e", expected_review_version: 1 },
      ],
      note: "Reviewed sheet",
    });
  });

  it("keeps the selection when a search matches nothing, and a conflict found by search stays unselectable", async () => {
    const user = userEvent.setup();
    renderPage();
    await rowFor("018200000001");
    await user.click(screen.getByRole("checkbox", { name: /select every eligible row/i }));
    await search(user, "999999999999");
    expect(await screen.findByText("Nothing awaiting approval")).toBeInTheDocument();
    expect(await approveButton(4)).toBeEnabled();
    await search(user, "018200000003");
    expect(within(await rowFor("018200000003")).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /select every eligible row/i })).toBeDisabled();
    expect(await approveButton(4)).toBeEnabled();
  });

  it("keeps the selection across pages and page sizes; select-all adds a page without dropping others", async () => {
    const many = Array.from({ length: 30 }, (_, i) => row(`m${i}`, `0300000000${String(i).padStart(2, "0")}`));
    serveQueue([CONFLICT, ...many]);
    const user = userEvent.setup();
    renderPage();
    await rowFor("030000000000");
    await user.click(screen.getByRole("combobox", { name: "Rows per page" }));
    await user.click(await screen.findByRole("option", { name: "25" }));
    await waitFor(() => expect(screen.queryByText("030000000024")).not.toBeInTheDocument());
    await user.click(screen.getByRole("checkbox", { name: /select every eligible row/i }));
    await approveButton(24);   // the conflict is on this page and is not selected

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await checkbox("030000000024")).not.toBeChecked();
    expect(await approveButton(24)).toBeEnabled();
    await user.click(screen.getByRole("checkbox", { name: /select every eligible row/i }));
    await approveButton(30);

    await user.click(screen.getByRole("button", { name: "Previous page" }));
    expect(await checkbox("030000000000")).toBeChecked();
    await user.click(screen.getByRole("combobox", { name: "Rows per page" }));
    await user.click(await screen.findByRole("option", { name: "100" }));
    expect(await checkbox("030000000029")).toBeChecked();
    expect(await approveButton(30)).toBeEnabled();
  });

  it("keeps the selection when the store filter changes, and clears it only on request", async () => {
    listStores.mockResolvedValue([{ id: "s2", label: "PB Wolf", display_name: "PB Wolf", identity_status: "confirmed",
                                    address: null, source_codes: [], kind: "physical", source_identity: null }]);
    const user = userEvent.setup();
    renderPage();
    await rowFor("018200000001");
    await user.click(await checkbox("018200000001"));
    await user.click(await checkbox("087692000570"));
    await approveButton(2);
    await user.click(screen.getByRole("combobox", { name: "Store" }));
    await user.click(await screen.findByRole("option", { name: /PB Wolf/ }));
    expect(await screen.findByText("No Product Master mappings held by this store")).toBeInTheDocument();
    expect(await approveButton(2)).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(await approveButton(0)).toBeDisabled();
  });
});

describe("mapping scope versus where a proposal came from", () => {
  it("labels globally held rows Product Master · Global and points to where invoice proposals are reviewed", async () => {
    renderPage();
    const first = await rowFor("018200000001");
    expect(within(first).getByTestId("mapping-scope")).toHaveTextContent("Product Master · Global");
    expect(screen.getByTestId("invoice-proposals-pointer")).toHaveTextContent("Values submitted from an invoice are reviewed in Master Data Review");
  });

  it("filtering by a physical store explains where that store's invoice proposals are", async () => {
    listStores.mockResolvedValue([{ id: "pb-wolf", label: "PB Wolf", display_name: "PB Wolf", identity_status: "confirmed",
                                    address: null, source_codes: [], kind: "physical", source_identity: null }]);
    listCommercialCandidates.mockImplementation(async (p: { store_id?: string }) =>
      p.store_id ? { items: [], total: 0, page: 1, page_size: 50 } : { items: [READY_A], total: 1, page: 1, page_size: 50 });
    const user = userEvent.setup();
    renderPage();
    await rowFor("018200000001");
    await user.click(screen.getByRole("combobox", { name: "Store" }));
    await user.click(await screen.findByRole("option", { name: /PB Wolf/ }));
    expect(await screen.findByText("No Product Master mappings held by this store")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open this store's invoice proposals" })).toHaveAttribute("href", "/data-review?store=pb-wolf");
  });
});
