import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  InventoryReceiptSummary,
  PendingApprovalRow,
  ReceiptPreview,
  InventoryItemRow,
  StoreDirectoryEntry,
  StoreInvoiceRow,
} from "@/api/types";

/**
 * Store inventory: off unless the server says it is on; only the stores the
 * server says this account may use (per-user grants; the server enforces them);
 * no on-hand figure without an audited opening count. A receipt is never posted
 * directly: Retailed submits it; the store's approval queue lists it; the review
 * prefills the proposed units, needs a reason for any difference, shows a stale
 * submission's changes and refuses to approve it, and blocks it while any product
 * line lacks an approved conversion. Rejection needs a reason.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { username: "barj", role: "MANAGER" }, isLoading: false, hasRole: () => true }),
}));

const getInventoryStatus = vi.fn();
const listStores = vi.fn();
const listInventoryStores = vi.fn();
const getInventoryOverview = vi.fn();
const getInventoryAllStores = vi.fn();
const listInventoryItems = vi.fn();
const listStoreInvoices = vi.fn();
const getReceiptPreview = vi.fn();
const listPendingApprovals = vi.fn();
const approveReceipt = vi.fn();
const rejectReceipt = vi.fn();
const reverseReceipt = vi.fn();

vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  getInventoryStatus: (...a: unknown[]) => getInventoryStatus(...a),
  listStores: (...a: unknown[]) => listStores(...a),
  listInventoryStores: (...a: unknown[]) => listInventoryStores(...a),
  getInventoryOverview: (...a: unknown[]) => getInventoryOverview(...a),
  getInventoryAllStores: (...a: unknown[]) => getInventoryAllStores(...a),
  listInventoryItems: (...a: unknown[]) => listInventoryItems(...a),
  listStoreInvoices: (...a: unknown[]) => listStoreInvoices(...a),
  getReceiptPreview: (...a: unknown[]) => getReceiptPreview(...a),
  listPendingApprovals: (...a: unknown[]) => listPendingApprovals(...a),
  approveReceipt: (...a: unknown[]) => approveReceipt(...a),
  rejectReceipt: (...a: unknown[]) => rejectReceipt(...a),
  reverseReceipt: (...a: unknown[]) => reverseReceipt(...a),
}));

const { InventoryPage } = await import("./inventory");

function store(id: string, label: string, kind: "physical" | "source_identity"): StoreDirectoryEntry {
  return {
    id, label, kind, identity_status: kind === "physical" ? "confirmed" : "unresolved",
    display_name: kind === "physical" ? label : null, address: null, source_codes: [], source_identity: null,
    in_store_directory: kind === "physical", customer_name: null, address_line_1: null, address_line_2: null, city: null,
    state: null, postal_code: null, status: "active", notes: null, identifiers: [], invoices: 0, pricing_rows: 0,
    identities: 0, catalogue_rows: 0, case_mappings: 0, pending_proposals: 0,
  };
}
const MIDLER = store("midler", "AF NV Midler", "physical");
const SOURCE = store("src-47708760", "Item Sales · 47708760", "source_identity");

const item = (code: string, description: string, extra: Partial<InventoryItemRow>): InventoryItemRow => ({
  item_code: code, description, status: "OPENING_STOCK_REQUIRED", base_unit: "SELLABLE_UNIT", opening_date: null,
  opening_quantity: null, received: "0", sold: "0", other: "0", on_hand: null, last_movement: "2026-10-09", ...extra,
});

const INVOICE: StoreInvoiceRow = {
  invoice_id: "inv-508655", invoice_number: "508655", invoice_date: "2026-10-01", vendor: "Testani", status: "VALIDATED",
  product_lines: 2, receipt: null, receivable_reasons: [],
};

const LINES: ReceiptPreview["lines"] = [
  { invoice_item_id: "line-1", sort_order: 1, description: "BUD LIGHT B-4/6", item_code: "01820000833", cases: "2",
    units_per_case: 24, expected_units: "48", conversion_path: "PRODUCT_MASTER_GLOBAL", conversion_scope: "Global" },
  { invoice_item_id: "line-2", sort_order: 2, description: "TWISTED TEA", item_code: "08769283102", cases: "3",
    units_per_case: 4, expected_units: "12", conversion_path: "PRODUCT_MASTER_GLOBAL", conversion_scope: "Global" },
];

function receipt(extra: Partial<InventoryReceiptSummary>): InventoryReceiptSummary {
  return { id: "r1", invoice_id: "inv-508655", invoice_number: "508655", status: "SUBMITTED", line_count: 2,
           created_at: "2026-10-09T12:00:00Z", submitted_at: "2026-10-09T12:00:00Z", submitted_by: "vivek",
           submitted_role: "MANAGER", received_on: null, confirmed_by: null, confirmed_role: null,
           discrepancy_count: null, ...extra };
}

const SUBMITTED = receipt({});

const PREVIEW: ReceiptPreview = {
  invoice_id: "inv-508655", invoice_number: "508655", store: MIDLER, eligibility_reasons: [], blockers: [], receipt: null,
  history: [SUBMITTED], can_approve: true, lines: LINES,
  submission: { receipt: SUBMITTED, proposed_lines: LINES, proposed_blockers: [], export_number: 3, stale_reasons: [],
                line_changes: [] },
};

const NOT_SUBMITTED: ReceiptPreview = { ...PREVIEW, submission: null, history: [], can_approve: false };

const QUEUE_ROW: PendingApprovalRow = {
  receipt: SUBMITTED, invoice_id: "inv-508655", invoice_number: "508655", invoice_date: "2026-10-01", vendor: "Testani",
  stale_reasons: [], blockers: 0,
};

function renderPage(path = "/inventory?store=midler") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><InventoryPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getInventoryStatus.mockResolvedValue({ enabled: true });
  listStores.mockResolvedValue([MIDLER, SOURCE]);
  listInventoryStores.mockResolvedValue([MIDLER]);          // the server returns only granted physical stores
  getInventoryOverview.mockResolvedValue({
    store: MIDLER, tracked_products: 2, opening_required: 1, out_of_stock: 0, in_stock: 1,
    last_recorded_at: "2026-10-09T12:00:00Z", recent_receipts: [], pending_approvals: 1,
  });
  // The all-stores list: the stores `listInventoryStores` says are granted, each with the figures
  // `getInventoryOverview` gives (read through its implementation so the call log stays the store view's).
  getInventoryAllStores.mockImplementation(async () => {
    const granted = (await listInventoryStores()) as StoreDirectoryEntry[];
    const stores = await Promise.all(granted.map(async (g) => ({
      store: g, access_granted: true, overview: await getInventoryOverview.getMockImplementation()!(g.id),
    })));
    return { stores, total_physical: stores.length, not_granted: 0 };
  });
  listInventoryItems.mockResolvedValue({
    success: true, total: 2, page: 1, page_size: 50, request_id: null,
    items: [
      item("01820000833", "BUD LIGHT B-4/6", { received: "48" }),
      item("08769283102", "TWISTED TEA", { status: "IN_STOCK", opening_date: "2026-10-01", opening_quantity: "10",
                                          received: "12", on_hand: "22" }),
    ],
  });
  listStoreInvoices.mockResolvedValue({ success: true, total: 1, page: 1, page_size: 25, request_id: null, items: [INVOICE] });
  getReceiptPreview.mockResolvedValue(PREVIEW);
  listPendingApprovals.mockResolvedValue([QUEUE_ROW]);
  approveReceipt.mockResolvedValue({ ...PREVIEW, can_approve: false, submission: null });
  rejectReceipt.mockResolvedValue({ ...PREVIEW, can_approve: false, submission: null });
});

/** Open the store's approval queue and review the one pending submission. */
async function openReview() {
  const user = userEvent.setup();
  renderPage();
  await user.click(await screen.findByRole("tab", { name: /approvals/i }));
  const row = (await screen.findByTestId("approval-row"));
  await user.click(within(row).getByRole("button", { name: "Review" }));
  await screen.findByTestId("receipt-submission");
  return user;
}

describe("Inventory page", () => {
  it("says inventory is off, and asks for nothing else, while the server has it switched off", async () => {
    getInventoryStatus.mockResolvedValue({ enabled: false });
    renderPage();
    expect(await screen.findByText("Inventory is not switched on")).toBeInTheDocument();
    expect(getInventoryOverview).not.toHaveBeenCalled();
    expect(listInventoryItems).not.toHaveBeenCalled();
    expect(listInventoryStores).not.toHaveBeenCalled();
    expect(getInventoryAllStores).not.toHaveBeenCalled();
  });

  it("shows a card for each store the server says this account may use, never the whole directory", async () => {
    const other = store("other", "AF Other Street", "physical");
    listStores.mockResolvedValue([MIDLER, other, SOURCE]);
    renderPage("/inventory");
    const cards = await screen.findAllByTestId("store-card");
    expect(cards.map((c) => within(c).getByText(/AF/).textContent)).toEqual(["AF NV Midler"]);
    expect(await within(cards[0]).findByText("1 awaiting approval")).toBeInTheDocument();
    expect(within(cards[0]).getByText("Need opening count")).toBeInTheDocument();
    expect(getInventoryAllStores).toHaveBeenCalled();
    expect(listStores).not.toHaveBeenCalled();
    expect(getInventoryOverview).not.toHaveBeenCalled();     // the grid is one request, not one per store
  });

  it("opens a store from its card, and goes back to all stores", async () => {
    const user = userEvent.setup();
    renderPage("/inventory");
    await user.click(await screen.findByRole("button", { name: "Open AF NV Midler inventory" }));
    expect(await screen.findAllByTestId("inventory-row")).toHaveLength(2);
    expect(within(screen.getByTestId("store-header")).getByText("AF NV Midler")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /All stores/ }));
    expect(await screen.findByTestId("store-grid")).toBeInTheDocument();
  });

  it("filters the store cards by search and by what needs attention", async () => {
    const quiet = store("quiet", "PB Wolf", "physical");
    listInventoryStores.mockResolvedValue([MIDLER, quiet]);
    getInventoryOverview.mockImplementation(async (id: string) => ({
      store: id === "quiet" ? quiet : MIDLER, tracked_products: id === "quiet" ? 0 : 2, opening_required: id === "quiet" ? 0 : 1,
      out_of_stock: 0, in_stock: id === "quiet" ? 0 : 1, last_recorded_at: null, recent_receipts: [],
      pending_approvals: id === "quiet" ? 0 : 1,
    }));
    const user = userEvent.setup();
    renderPage("/inventory");
    expect(await screen.findAllByTestId("store-card")).toHaveLength(2);
    await user.click(screen.getByRole("combobox", { name: "Show stores" }));
    await user.click(await screen.findByRole("option", { name: "Awaiting approval" }));
    await waitFor(() => expect(screen.getAllByTestId("store-card")).toHaveLength(1));
    expect(screen.getByText("AF NV Midler")).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "Show stores" }));
    await user.click(await screen.findByRole("option", { name: "No activity yet" }));
    await waitFor(() => expect(screen.getByText("PB Wolf")).toBeInTheDocument());
    expect(screen.getAllByTestId("store-card")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    await user.type(screen.getByRole("textbox", { name: "Search store name or address" }), "midler");
    await waitFor(() => expect(screen.getAllByTestId("store-card")).toHaveLength(1));
    expect(screen.getByText("AF NV Midler")).toBeInTheDocument();
  });

  it("says no stores are assigned when the account has no grants, and loads no inventory", async () => {
    listInventoryStores.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("No stores assigned to you")).toBeInTheDocument();
    expect(screen.getByText(/Ask an administrator to grant you access/)).toBeInTheDocument();
    expect(getInventoryOverview).not.toHaveBeenCalled();
  });

  it("does not open a store the account cannot use, even when the URL names it", async () => {
    renderPage("/inventory?store=other");
    expect(await screen.findByTestId("store-grid")).toBeInTheDocument();
    expect(screen.queryByTestId("store-header")).not.toBeInTheDocument();
    expect(getInventoryOverview).not.toHaveBeenCalled();     // no store view opened, and the grid is one request
    expect(listInventoryItems).not.toHaveBeenCalled();
  });

  it("never treats a source identity as a store, even when the URL names one", async () => {
    renderPage("/inventory?store=src-47708760");
    expect(await screen.findByTestId("store-grid")).toBeInTheDocument();
    expect(getInventoryOverview).not.toHaveBeenCalled();     // no store view opened, and the grid is one request
    expect(listInventoryItems).not.toHaveBeenCalled();
  });

  it("sorts products on the server, and filters invoices by search and receipt state", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findAllByTestId("inventory-row");
    await user.click(screen.getByRole("combobox", { name: "Sort products" }));
    await user.click(await screen.findByRole("option", { name: "Lowest on hand" }));
    await waitFor(() => expect(listInventoryItems).toHaveBeenLastCalledWith("midler", expect.objectContaining({ sort: "on_hand_asc", page: 1 })));

    await user.click(screen.getByRole("tab", { name: /invoices/i }));
    await screen.findByTestId("store-invoice-row");
    await user.click(screen.getByRole("combobox", { name: "Receipt state" }));
    await user.click(await screen.findByRole("option", { name: "Awaiting approval" }));
    await waitFor(() => expect(listStoreInvoices).toHaveBeenLastCalledWith("midler", expect.objectContaining({ receipt: "awaiting" })));
    await user.type(screen.getByRole("textbox", { name: "Search invoice number or vendor" }), "5086");
    await waitFor(() => expect(listStoreInvoices).toHaveBeenLastCalledWith(
      "midler", expect.objectContaining({ search: "5086", receipt: "awaiting", page: 1 })));
  });

  it("shows no on-hand figure until an opening count exists, and marks on-hand provisional", async () => {
    renderPage();
    const rows = await screen.findAllByTestId("inventory-row");
    expect(getInventoryOverview).toHaveBeenCalledWith("midler");
    expect(within(rows[0]).getAllByText("Opening stock required").length).toBeGreaterThan(0);
    expect(within(rows[0]).queryByText("prov.")).not.toBeInTheDocument();
    expect(within(rows[1]).getByText("22")).toBeInTheDocument();
    expect(within(rows[1]).getByText("prov.")).toBeInTheDocument();
    expect(screen.getByTestId("provisional-note")).toHaveTextContent("Sales are not imported yet");
  });

  it("shows the store's zero start and labels products that start from it", async () => {
    getInventoryOverview.mockResolvedValue({
      store: MIDLER, tracked_products: 2, opening_required: 0, out_of_stock: 0, in_stock: 2,
      last_recorded_at: "2026-10-09T12:00:00Z", recent_receipts: [], pending_approvals: 0,
      baseline: { baseline_date: "2026-10-09", basis: "EARLIEST_POSTED_RECEIPT", initialization_date: "2026-10-20",
                  initialized_by: "admin", reason: "go-live" },
    });
    listInventoryItems.mockResolvedValue({
      success: true, total: 2, page: 1, page_size: 50, request_id: null,
      items: [
        item("01820000833", "BUD LIGHT B-4/6", { status: "IN_STOCK", opening_date: "2026-10-09", opening_quantity: "0",
                                                received: "30", on_hand: "30", opening_source: "STORE_BASELINE" }),
        item("08769283102", "TWISTED TEA", { status: "IN_STOCK", opening_date: "2026-10-01", opening_quantity: "10",
                                            received: "12", on_hand: "22", opening_source: "OPENING_COUNT" }),
      ],
    });
    renderPage();
    const rows = await screen.findAllByTestId("inventory-row");
    expect(within(rows[0]).getByText(/store start 09 Oct 2026/)).toBeInTheDocument();
    expect(within(rows[0]).getAllByText("30")).toHaveLength(2);                 // received 30, on hand 30
    expect(within(rows[1]).queryByText(/store start/)).not.toBeInTheDocument();
    expect(screen.getByTestId("baseline-note")).toHaveTextContent("Zero start (declared, not counted) from 09 Oct 2026");
    expect(screen.getByTestId("baseline-note")).toHaveTextContent("first received invoice");
  });

  it("shows no store-start note when the store has no baseline", async () => {
    renderPage();
    await screen.findAllByTestId("inventory-row");
    expect(screen.queryByTestId("baseline-note")).not.toBeInTheDocument();
  });

  it("lists the store's pending submissions with who submitted them, from the server's queue", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByRole("tab", { name: "Approvals (1)" })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /approvals/i }));
    const row = await screen.findByTestId("approval-row");
    expect(listPendingApprovals).toHaveBeenCalledWith("midler");
    expect(row).toHaveTextContent("#508655");
    expect(row).toHaveTextContent("vivek");
    expect(row).toHaveTextContent("Ready to review");
  });

  it("opens the approval queue directly from a link", async () => {
    renderPage("/inventory?store=midler&tab=approvals");
    expect(await screen.findByTestId("approval-row")).toBeInTheDocument();
  });

  it("shows a stale queue entry as out of date", async () => {
    listPendingApprovals.mockResolvedValue([{ ...QUEUE_ROW, stale_reasons: ["invoice_changed_since_retailed"] }]);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("tab", { name: /approvals/i }));
    expect(await screen.findByTestId("approval-row")).toHaveTextContent("Out of date");
  });

  it("prefills the proposed units, requires a reason for any difference, and approves that submission", async () => {
    const user = await openReview();
    expect(screen.getByTestId("receipt-submission")).toHaveTextContent("Submitted by vivek");
    expect(screen.getByTestId("receipt-submission")).toHaveTextContent("PDI export #3");
    const bud = screen.getByRole("spinbutton", { name: "Accepted units for BUD LIGHT B-4/6" });
    expect(bud).toHaveValue(48);
    expect(screen.getByRole("spinbutton", { name: "Accepted units for TWISTED TEA" })).toHaveValue(12);
    expect(screen.getByTestId("confirm-approve")).toBeEnabled();

    await user.clear(bud);
    await user.type(bud, "46");
    expect(screen.getByText("Differs from expected: give a reason")).toBeInTheDocument();
    expect(screen.getByTestId("confirm-approve")).toBeDisabled();

    await user.type(screen.getByRole("textbox", { name: "Reason for BUD LIGHT B-4/6" }), "2 cans crushed");
    await user.click(screen.getByTestId("confirm-approve"));
    await waitFor(() => expect(approveReceipt).toHaveBeenCalledTimes(1));
    const [invoiceId, body] = approveReceipt.mock.calls[0];
    expect(invoiceId).toBe("inv-508655");
    expect(body.receipt_id).toBe("r1");
    expect(body.lines).toEqual([
      { invoice_item_id: "line-1", accepted_units: 46, reason: "2 cans crushed" },
      { invoice_item_id: "line-2", accepted_units: 12, reason: null },
    ]);
    expect(body.received_on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("refuses a fractional or negative quantity before it is sent", async () => {
    const user = await openReview();
    const tea = screen.getByRole("spinbutton", { name: "Accepted units for TWISTED TEA" });
    await user.clear(tea);
    await user.type(tea, "11.5");
    expect(screen.getByText("Whole sellable units only")).toBeInTheDocument();
    expect(screen.getByTestId("confirm-approve")).toBeDisabled();
    await user.clear(tea);
    await user.type(tea, "-1");
    expect(screen.getByText("Cannot be negative")).toBeInTheDocument();
    expect(approveReceipt).not.toHaveBeenCalled();
  });

  it("says when you are approving your own submission", async () => {
    const own = receipt({ submitted_by: "barj" });
    getReceiptPreview.mockResolvedValue({ ...PREVIEW, submission: { ...PREVIEW.submission!, receipt: own } });
    await openReview();
    expect(screen.getByText(/approving it yourself is recorded as self-approval/)).toBeInTheDocument();
  });

  it("shows a stale submission's reasons and changes, and offers no approval", async () => {
    getReceiptPreview.mockResolvedValue({
      ...PREVIEW, can_approve: false,
      submission: { ...PREVIEW.submission!, stale_reasons: ["invoice_changed_since_retailed", "lines_changed_since_submission"],
                    line_changes: [{ invoice_item_id: "line-1", item_code: "01820000833", description: "BUD LIGHT B-4/6",
                                     change: "changed", proposed_units: "48", current_units: "24",
                                     proposed_units_per_case: 24, current_units_per_case: 24 }] },
    });
    await openReview();
    const stale = screen.getByTestId("submission-stale");
    expect(stale).toHaveTextContent("cannot be approved");
    expect(stale).toHaveTextContent("changed after it was marked Retailed");
    expect(within(screen.getByTestId("submission-changes")).getByText("BUD LIGHT B-4/6")).toBeInTheDocument();
    expect(screen.queryByTestId("confirm-approve")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject…" })).toBeInTheDocument();
  });

  it("rejects only with a reason", async () => {
    const user = await openReview();
    await user.click(screen.getByRole("button", { name: "Reject…" }));
    expect(screen.getByTestId("confirm-reject")).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "Why the submission is rejected" }), "wrong delivery");
    await user.click(screen.getByTestId("confirm-reject"));
    await waitFor(() => expect(rejectReceipt).toHaveBeenCalledWith("inv-508655", { receipt_id: "r1", reason: "wrong delivery" }));
    expect(approveReceipt).not.toHaveBeenCalled();
  });

  it("blocks approval and names every product without an approved conversion", async () => {
    getReceiptPreview.mockResolvedValue({
      ...PREVIEW, can_approve: false, lines: [LINES[0]],
      blockers: [{ sort_order: 2, description: "FIREBALL 50ML", item_code: "08800404783", reason: "no approved units-per-case mapping" }],
    });
    await openReview();
    const blocked = screen.getByTestId("receipt-blocked");
    expect(within(blocked).getByText("FIREBALL 50ML")).toBeInTheDocument();
    expect(within(blocked).getByText("08800404783")).toBeInTheDocument();
    expect(screen.queryByTestId("confirm-approve")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  it("never offers to receive an invoice that has not been submitted", async () => {
    getReceiptPreview.mockResolvedValue(NOT_SUBMITTED);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("tab", { name: /invoices/i }));
    const row = (await screen.findByText("#508655")).closest("tr")!;
    expect(within(row).getByText("Not submitted")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Details" }));
    expect(await screen.findByTestId("receipt-not-submitted")).toHaveTextContent("marked Retailed");
    expect(screen.queryByTestId("confirm-approve")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  it("shows a posted receipt with its submitter and approver, without offering to receive it again", async () => {
    const posted = receipt({ status: "POSTED", received_on: "2026-10-09", confirmed_by: "barj", confirmed_role: "MANAGER",
                             discrepancy_count: 1 });
    getReceiptPreview.mockResolvedValue({ ...PREVIEW, can_approve: false, submission: null, receipt: posted, history: [posted] });
    listStoreInvoices.mockResolvedValue({ success: true, total: 1, page: 1, page_size: 25, request_id: null,
                                          items: [{ ...INVOICE, receipt: posted }] });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("tab", { name: /invoices/i }));
    const row = (await screen.findByText("#508655")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "View" }));
    const shown = await screen.findByTestId("receipt-posted");
    expect(shown).toHaveTextContent("Submitted by vivek");
    expect(shown).toHaveTextContent("Approved by barj");
    expect(screen.queryByTestId("confirm-approve")).not.toBeInTheDocument();
  });

  it("reverses a posted receipt only with a reason, naming the receipt it reverses", async () => {
    const posted = receipt({ status: "POSTED", received_on: "2026-10-09", confirmed_by: "barj", confirmed_role: "MANAGER",
                             discrepancy_count: 0 });
    getReceiptPreview.mockResolvedValue({ ...PREVIEW, can_approve: false, submission: null, receipt: posted, history: [posted] });
    reverseReceipt.mockResolvedValue({ receipt: { ...posted, status: "REVERSED" }, movements_reversed: 2, already_reversed: false });
    listStoreInvoices.mockResolvedValue({ success: true, total: 1, page: 1, page_size: 25, request_id: null,
                                          items: [{ ...INVOICE, receipt: posted }] });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("tab", { name: /invoices/i }));
    const row = (await screen.findByText("#508655")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "View" }));
    await user.click(await screen.findByRole("button", { name: "Reverse receipt…" }));
    expect(screen.getByTestId("reverse-form")).toHaveTextContent("Retailed mark is not changed");
    expect(screen.getByTestId("confirm-reverse")).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "Why the receipt is being reversed" }), "lager was 2 cases");
    await user.click(screen.getByTestId("confirm-reverse"));
    await waitFor(() => expect(reverseReceipt).toHaveBeenCalledWith("inv-508655", { receipt_id: "r1", reason: "lager was 2 cases" }));
  });

  it("keeps rejected and reversed receipts as history beside a new submission", async () => {
    const rejected = receipt({ id: "r0", status: "REJECTED", rejected_by: "prabh", rejection_reason: "short-shipped" });
    const reversed = receipt({ id: "rx", status: "REVERSED", received_on: "2026-10-08", confirmed_by: "barj",
                               confirmed_role: "MANAGER", discrepancy_count: 0, reversed_by: "prabh",
                               reversed_at: "2026-10-09T13:00:00Z", reversal_reason: "lager was 2 cases" });
    getReceiptPreview.mockResolvedValue({ ...PREVIEW, history: [rejected, reversed, SUBMITTED] });
    await openReview();
    const history = screen.getByTestId("receipt-history");
    expect(history).toHaveTextContent("rejected by prabh: short-shipped");
    expect(history).toHaveTextContent("reversed by prabh");
  });
});
