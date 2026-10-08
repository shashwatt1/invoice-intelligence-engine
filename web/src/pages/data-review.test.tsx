import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { ProposalDetail, ProposalListParams, ProposalRow, StoreRef } from "@/api/types";

import { DataReviewPage } from "./data-review";

/**
 * The review table's fast path, against a mocked API.
 *
 * What these pin: selection is per row and per page; the toolbar and its
 * count follow the selection; approve / reject / edit go through the bulk
 * and revise endpoints as the signed-in account (never a typed name); a row
 * the Product Master governs needs an individual decision; a refused batch
 * is reported, never claimed; and a filter change drops rows that are no
 * longer on screen from the selection.
 */

vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: { username: "barj", role: "MANAGER" } }) }));
vi.mock("@/api/endpoints", () => ({
  listProposals: vi.fn(),
  listProposalSourceInvoices: vi.fn(async () => []),
  listStores: vi.fn(async () => []),
  bulkApproveProposals: vi.fn(),
  bulkRejectProposals: vi.fn(),
  reviseProposal: vi.fn(),
  getProposal: vi.fn(),
  approveProposal: vi.fn(),
  rejectProposal: vi.fn(),
  getProductHistory: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import * as api from "@/api/endpoints";
import { toast } from "sonner";

const STORE: StoreRef = {
  id: "a07b83b2-ec58-4f9b-8344-be3dc0b8f651",
  label: "86357232 (identity unconfirmed)",
  identity_status: "unresolved",
  display_name: null,
  address: null,
  source_codes: ["86357232"],
};

function row(id: string, entity_key: string, proposed_value: number, extra: Partial<ProposalRow> = {}): ProposalRow {
  return {
    id,
    store: STORE,
    entity_type: "case_mapping",
    entity_key,
    field: "units_per_case",
    proposed_value,
    current_value: null,
    source: "reference_derived",
    source_file: null,
    source_sheet: null,
    source_row: null,
    invoice_id: "9a72e038-d190-46f0-b4d7-24873aeb0240",
    invoice_deleted: false,
    description: null,
    proposed_by: "frontend:review-ui",
    status: "PENDING",
    reviewed_by: null,
    reviewed_at: null,
    review_note: null,
    created_at: "2026-09-17T10:00:00Z",
    ...extra,
  };
}

const A = row("11111111-1111-4111-8111-111111111111", "08200074725", 12);
const B = row("22222222-2222-4222-8222-222222222222", "01820025004", 15);
const C = row("33333333-3333-4333-8333-333333333333", "68474680041", 24);
const DONE = row("44444444-4444-4444-8444-444444444444", "01820096721", 1, {
  status: "APPROVED", reviewed_by: "data-team:shashwat", reviewed_at: "2026-09-17T11:00:00Z",
});

function detail(r: ProposalRow, over: Partial<ProposalDetail> = {}): ProposalDetail {
  return { ...r, evidence: null, reason: null, resulting_mapping: null, current_master_value: null, ...over };
}

const listProposals = vi.mocked(api.listProposals);
const bulkApprove = vi.mocked(api.bulkApproveProposals);
const bulkReject = vi.mocked(api.bulkRejectProposals);
const revise = vi.mocked(api.reviseProposal);

function serve(rows: ProposalRow[]) {
  listProposals.mockImplementation(async (params: ProposalListParams) => {
    let items = rows;
    if (params.status && params.status !== "ALL") items = items.filter((r) => r.status === params.status);
    if (params.item_code) items = items.filter((r) => r.entity_key.includes(params.item_code!));
    return { success: true, request_id: "test", items, total: items.length, page: params.page ?? 1, page_size: params.page_size ?? 25 };
  });
}

function renderPage(path = "/data-review") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/data-review" element={<DataReviewPage />} />
          <Route path="/data-review/proposals/:proposalId" element={<div>detail page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function rowsOnScreen() {
  await screen.findAllByTestId("proposal-row");
  return screen.getAllByTestId("proposal-row");
}

function checkboxIn(tr: HTMLElement) {
  return within(tr).getByRole("checkbox");
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  serve([A, B, C]);
});

describe("selection", () => {
  it("selects and deselects one row, showing the count while anything is selected", async () => {
    const user = userEvent.setup();
    renderPage();
    const [first] = await rowsOnScreen();
    expect(screen.queryByTestId("bulk-toolbar")).not.toBeInTheDocument();

    await user.click(checkboxIn(first));
    expect(screen.getByTestId("selected-count")).toHaveTextContent("1 selected");
    expect(first).toHaveAttribute("data-state", "selected");
    // ticking a box must not open the detail page
    expect(screen.queryByText("detail page")).not.toBeInTheDocument();

    await user.click(checkboxIn(first));
    expect(screen.queryByTestId("bulk-toolbar")).not.toBeInTheDocument();
    expect(first).not.toHaveAttribute("data-state", "selected");
  });

  it("select all takes every pending row on the page, and again clears them", async () => {
    const user = userEvent.setup();
    renderPage();
    await rowsOnScreen();
    const all = screen.getByTestId("select-all");
    expect(all).toHaveAttribute("aria-label", "Select all 3 pending rows on this page");

    await user.click(all);
    expect(screen.getByTestId("selected-count")).toHaveTextContent("3 selected");
    expect(all).toHaveAttribute("aria-checked", "true");

    await user.click(all);
    expect(screen.queryByTestId("bulk-toolbar")).not.toBeInTheDocument();
    expect(all).toHaveAttribute("aria-checked", "false");
  });

  it("is indeterminate when only some rows are ticked", async () => {
    const user = userEvent.setup();
    renderPage();
    const [first] = await rowsOnScreen();
    await user.click(checkboxIn(first));
    expect(screen.getByTestId("select-all")).toHaveAttribute("aria-checked", "mixed");
  });

  it("decided rows cannot be selected and are not counted by select all", async () => {
    const user = userEvent.setup();
    serve([A, DONE]);
    renderPage("/data-review?status=ALL");
    const rows = await rowsOnScreen();
    expect(rows).toHaveLength(2);
    expect(checkboxIn(rows[1])).toBeDisabled();

    await user.click(screen.getByTestId("select-all"));
    expect(screen.getByTestId("selected-count")).toHaveTextContent("1 selected");
  });

  it("drops rows that a filter change removed from the screen", async () => {
    const user = userEvent.setup();
    renderPage();
    await rowsOnScreen();
    await user.click(screen.getByTestId("select-all"));
    expect(screen.getByTestId("selected-count")).toHaveTextContent("3 selected");

    await user.type(screen.getByPlaceholderText("UPC / item code"), "0182");
    await waitFor(() => expect(screen.getAllByTestId("proposal-row")).toHaveLength(1));
    // only B (01820025004) is still visible, so only B is still selected
    await waitFor(() => expect(screen.getByTestId("selected-count")).toHaveTextContent("1 selected"));
  });

  it("clicking the row itself still opens the detailed review", async () => {
    const user = userEvent.setup();
    renderPage();
    const [first] = await rowsOnScreen();
    await user.click(within(first).getByText("08200074725"));
    expect(await screen.findByText("detail page")).toBeInTheDocument();
  });
});

describe("bulk approve", () => {
  it("approves every selected id as the signed-in account, and clears the selection", async () => {
    const user = userEvent.setup();
    bulkApprove.mockResolvedValue({
      reviewed_by: "barj",
      decided: [A, B].map((r) => ({ id: r.id, entity_key: r.entity_key, status: "APPROVED", applied_to: `product_case_mappings:${STORE.id}:${r.entity_key}` })),
    });
    renderPage();
    const [first, second] = await rowsOnScreen();
    await user.click(checkboxIn(first));
    await user.click(checkboxIn(second));
    await user.click(screen.getByRole("button", { name: /approve selected/i }));

    const dialog = await screen.findByTestId("bulk-decision-dialog");
    expect(within(dialog).getByText("Approve 2 records")).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Approve 2" });
    expect(within(dialog).getByTestId("bulk-reviewer")).toHaveTextContent("barj · Manager");
    expect(within(dialog).queryByRole("textbox", { name: /approved by/i })).not.toBeInTheDocument();
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() => expect(bulkApprove).toHaveBeenCalledTimes(1));
    expect(bulkApprove).toHaveBeenCalledWith({
      proposal_ids: [A.id, B.id], reviewed_by: "barj", note: null,
    });
    await waitFor(() => expect(screen.queryByTestId("bulk-decision-dialog")).not.toBeInTheDocument());
    expect(screen.queryByTestId("bulk-toolbar")).not.toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith("Approved 2 — wrote 2 mappings.");
  });

  it("reports a refused batch row by row and claims nothing", async () => {
    const user = userEvent.setup();
    bulkApprove.mockRejectedValue(new ApiError(422, {
      error_code: "ERR_VALIDATION_FAILED",
      message: "1 proposal(s) already decided; nothing was changed.",
      detail: { failures: { [B.id]: "already APPROVED" } },
    }));
    renderPage();
    await rowsOnScreen();
    await user.click(screen.getByTestId("select-all"));
    await user.click(screen.getByRole("button", { name: /approve selected/i }));
    const dialog = await screen.findByTestId("bulk-decision-dialog");
    await user.click(within(dialog).getByRole("button", { name: "Approve 3" }));

    const failures = await within(dialog).findByTestId("bulk-failures");
    expect(failures).toHaveTextContent("Nothing was changed");
    expect(failures).toHaveTextContent("01820025004 — already APPROVED");
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("Nothing was approved — 1 of 3 could not be decided.");
    // the dialog stays open and the selection is intact for a retry
    expect(screen.getByTestId("selected-count")).toHaveTextContent("3 selected");
  });
});

describe("bulk reject", () => {
  it("rejects every selected id with the optional note", async () => {
    const user = userEvent.setup();
    bulkReject.mockResolvedValue({
      reviewed_by: "barj",
      decided: [{ id: C.id, entity_key: C.entity_key, status: "REJECTED", applied_to: null }],
    });
    renderPage();
    const [, , third] = await rowsOnScreen();
    await user.click(checkboxIn(third));
    await user.click(screen.getByRole("button", { name: /reject selected/i }));

    const dialog = await screen.findByTestId("bulk-decision-dialog");
    expect(within(dialog).getByText("Reject 1 record")).toBeInTheDocument();
    expect(within(dialog).getByTestId("bulk-reviewer")).toHaveTextContent("barj · Manager");
    await user.type(within(dialog).getByLabelText(/note/i), "test run, invoice deleted");
    await user.click(within(dialog).getByRole("button", { name: "Reject 1" }));

    await waitFor(() => expect(bulkReject).toHaveBeenCalledWith({
      proposal_ids: [C.id], reviewed_by: "barj", note: "test run, invoice deleted",
    }));
    expect(bulkApprove).not.toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith("Rejected 1 — master data untouched.");
  });
});

describe("editing a proposed value", () => {
  it("inline: saves a corrected value as a revision under the signed-in account", async () => {
    const user = userEvent.setup();
    const revised = row("55555555-5555-4555-8555-555555555555", B.entity_key, 12, { source: "operator_entered" });
    revise.mockResolvedValue({ proposal: detail(revised), superseded: detail({ ...B, status: "REJECTED" }) });
    renderPage();
    const [, second] = await rowsOnScreen();

    await user.click(within(second).getByRole("button", { name: `Edit proposed value for ${B.entity_key}` }));
    const input = within(second).getByRole("textbox", { name: `New units per case for ${B.entity_key}` });
    expect(input).toHaveValue("15");
    const save = within(second).getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();                          // unchanged value cannot be "revised"

    await user.clear(input);
    await user.type(input, "12");
    await user.click(save);

    await waitFor(() => expect(revise).toHaveBeenCalledWith(B.id, {
      proposed_value: 12, proposed_by: "barj",
    }));
    expect(toast.success).toHaveBeenCalledWith("01820025004: 15 → 12. New proposal pending approval.");
    // nothing was approved by editing
    expect(bulkApprove).not.toHaveBeenCalled();
  });

  it("inline: refuses a non-numeric or out-of-range value", async () => {
    const user = userEvent.setup();
    renderPage();
    const [first] = await rowsOnScreen();
    await user.click(within(first).getByRole("button", { name: `Edit proposed value for ${A.entity_key}` }));
    const input = within(first).getByRole("textbox", { name: `New units per case for ${A.entity_key}` });
    for (const bad of ["0", "abc", "1.5"]) {
      await user.clear(input);
      await user.type(input, bad);
      expect(within(first).getByRole("button", { name: "Save" })).toBeDisabled();
    }
    expect(revise).not.toHaveBeenCalled();
  });

  it("edit selected: each row keeps its own value and only changed rows are revised", async () => {
    const user = userEvent.setup();
    revise.mockImplementation(async (proposalId, revision) => {
      const original = [A, B, C].find((r) => r.id === proposalId)!;
      const next = row(`9999-${proposalId}`, original.entity_key, revision.proposed_value, { source: "operator_entered" });
      return { proposal: detail(next), superseded: detail({ ...original, status: "REJECTED" }) };
    });
    renderPage();
    await rowsOnScreen();
    await user.click(screen.getByTestId("select-all"));
    await user.click(screen.getByRole("button", { name: /edit selected/i }));

    const dialog = await screen.findByTestId("bulk-edit-dialog");
    const inputs = within(dialog).getAllByRole("textbox", { name: /units per case for/i });
    expect(inputs.map((i) => (i as HTMLInputElement).value)).toEqual(["12", "15", "24"]);
    // there is no "apply to all"
    expect(within(dialog).queryByText(/apply/i)).not.toBeInTheDocument();

    const saveButton = within(dialog).getByRole("button", { name: /save|fix/i });
    expect(saveButton).toBeDisabled();                    // nothing changed yet

    await user.clear(inputs[1]);
    await user.type(inputs[1], "12");
    expect(within(dialog).getByTestId("edit-reviewer")).toHaveTextContent("barj");
    await user.type(within(dialog).getByLabelText(/note/i), "printed package says 12");
    expect(within(dialog).getByRole("button", { name: "Save 1 change" })).toBeEnabled();
    await user.click(within(dialog).getByRole("button", { name: "Save 1 change" }));

    await waitFor(() => expect(revise).toHaveBeenCalledTimes(1));
    expect(revise).toHaveBeenCalledWith(B.id, {
      proposed_value: 12, proposed_by: "barj", note: "printed package says 12",
    });
    await waitFor(() => expect(screen.queryByTestId("bulk-edit-dialog")).not.toBeInTheDocument());
    expect(toast.success).toHaveBeenCalledWith("Revised 1 value — still pending approval.");
  });

  it("edit selected: a failed revision is marked on its row and the rest still save", async () => {
    const user = userEvent.setup();
    revise.mockImplementation(async (proposalId, revision) => {
      if (proposalId === A.id) throw new ApiError(422, { error_code: "ERR_VALIDATION_FAILED", message: "Proposal is APPROVED and cannot be revised." });
      const original = [A, B, C].find((r) => r.id === proposalId)!;
      return {
        proposal: detail(row(`9999-${proposalId}`, original.entity_key, revision.proposed_value)),
        superseded: detail({ ...original, status: "REJECTED" }),
      };
    });
    renderPage();
    await rowsOnScreen();
    await user.click(screen.getByTestId("select-all"));
    await user.click(screen.getByRole("button", { name: /edit selected/i }));
    const dialog = await screen.findByTestId("bulk-edit-dialog");
    const inputs = within(dialog).getAllByRole("textbox", { name: /units per case for/i });
    await user.clear(inputs[0]);
    await user.type(inputs[0], "6");
    await user.clear(inputs[2]);
    await user.type(inputs[2], "12");
    await user.click(within(dialog).getByRole("button", { name: "Save 2 changes" }));

    await waitFor(() => expect(revise).toHaveBeenCalledTimes(2));
    expect(within(dialog).getByText(/cannot be revised/)).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith("1 revised, 1 failed. The failed rows are marked below.");
    expect(screen.getByTestId("bulk-edit-dialog")).toBeInTheDocument();   // stays open to show it
  });
});

describe("provenance and Product Master governance", () => {
  it("a row the Product Master governs has no checkbox, says why, and is left out of select all", async () => {
    const user = userEvent.setup();
    const blocked = row("66666666-6666-4666-8666-666666666666", "08769200057", 18, {
      product_master_block: "The Product Master mapping for this item is REVIEW_REQUIRED (CONFLICT) — decide it in Product Master Approvals first; a store value approved here would bypass that decision.",
    });
    serve([A, blocked]);
    renderPage();
    const rows = await screen.findAllByTestId("proposal-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[1]).queryByTestId("row-checkbox")).not.toBeInTheDocument();
    expect(within(rows[1]).getByTestId("individual-review")).toHaveAttribute("title", blocked.product_master_block!);
    expect(within(rows[1]).getByTestId("product-master-block")).toHaveTextContent("Product Master decision required");
    await user.click(screen.getByTestId("select-all"));
    expect(screen.getByTestId("selected-count")).toHaveTextContent("1 selected");
  });

  it("names the source invoice by number and date, and finds proposals by invoice number", async () => {
    const user = userEvent.setup();
    serve([row(A.id, A.entity_key, 12, { invoice_number: "101497", invoice_date: "2026-09-10" })]);
    renderPage();
    const [first] = await screen.findAllByTestId("proposal-row");
    expect(within(first).getByTestId("related-invoice")).toHaveTextContent(/Invoice #101497 · /);
    await user.type(screen.getByRole("textbox", { name: "Invoice number or ID" }), "101497");
    await waitFor(() => expect(listProposals).toHaveBeenLastCalledWith(
      expect.objectContaining({ invoice_number: "101497", invoice_id: undefined })));
  });
});
