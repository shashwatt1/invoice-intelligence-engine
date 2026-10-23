import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { InventoryItemRow, ReceiptPreview, StoreDirectoryEntry, StoreInvoiceRow } from "@/api/types";

/**
 * Store inventory: off unless the server says it is on; only the stores the
 * server says this account may use (per-user grants; the server enforces them);
 * no on-hand figure without an audited opening count; a receipt prefilled
 * with expected units, a reason required for any difference, and a receipt
 * blocked outright while any product line lacks an approved conversion.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const getInventoryStatus = vi.fn();
const listStores = vi.fn();
const listInventoryStores = vi.fn();
const getInventoryOverview = vi.fn();
const listInventoryItems = vi.fn();
const listStoreInvoices = vi.fn();
const getReceiptPreview = vi.fn();
const postReceipt = vi.fn();
const reverseReceipt = vi.fn();

vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  getInventoryStatus: (...a: unknown[]) => getInventoryStatus(...a),
  listStores: (...a: unknown[]) => listStores(...a),
  listInventoryStores: (...a: unknown[]) => listInventoryStores(...a),
  getInventoryOverview: (...a: unknown[]) => getInventoryOverview(...a),
  listInventoryItems: (...a: unknown[]) => listInventoryItems(...a),
  listStoreInvoices: (...a: unknown[]) => listStoreInvoices(...a),
  getReceiptPreview: (...a: unknown[]) => getReceiptPreview(...a),
  postReceipt: (...a: unknown[]) => postReceipt(...a),
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

const PREVIEW: ReceiptPreview = {
  invoice_id: "inv-508655", invoice_number: "508655", store: MIDLER, eligibility_reasons: [], blockers: [], receipt: null,
  history: [], can_post: true,
  lines: [
    { invoice_item_id: "line-1", sort_order: 1, description: "BUD LIGHT B-4/6", item_code: "01820000833", cases: "2",
      units_per_case: 24, expected_units: "48", conversion_path: "PRODUCT_MASTER_GLOBAL", conversion_scope: "Global" },
    { invoice_item_id: "line-2", sort_order: 2, description: "TWISTED TEA", item_code: "08769283102", cases: "3",
      units_per_case: 4, expected_units: "12", conversion_path: "PRODUCT_MASTER_GLOBAL", conversion_scope: "Global" },
  ],
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
    last_recorded_at: "2026-10-09T12:00:00Z", recent_receipts: [],
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
  postReceipt.mockResolvedValue({ ...PREVIEW, can_post: false });
});

async function openReceipt() {
  const user = userEvent.setup();
  renderPage();
  await user.click(await screen.findByRole("tab", { name: /invoices/i }));
  const row = (await screen.findByText("#508655")).closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Receive" }));
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
  });

  it("offers only the stores the server says this account may use, never the whole directory", async () => {
    const other = store("other", "AF Other Street", "physical");
    listStores.mockResolvedValue([MIDLER, other, SOURCE]);
    const user = userEvent.setup();
    renderPage("/inventory");
    await user.click(await screen.findByRole("combobox", { name: "Store" }));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(options).toEqual(["AF NV Midler"]);
    expect(listInventoryStores).toHaveBeenCalled();
    expect(listStores).not.toHaveBeenCalled();
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
    expect(await screen.findByText("Choose a store")).toBeInTheDocument();
    expect(getInventoryOverview).not.toHaveBeenCalled();
  });

  it("never treats a source identity as a store, even when the URL names one", async () => {
    renderPage("/inventory?store=src-47708760");
    expect(await screen.findByText("Choose a store")).toBeInTheDocument();
    expect(getInventoryOverview).not.toHaveBeenCalled();
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

  it("prefills expected units and requires a reason for any line that differs", async () => {
    const user = await openReceipt();
    const bud = await screen.findByRole("spinbutton", { name: "Accepted units for BUD LIGHT B-4/6" });
    expect(bud).toHaveValue(48);
    expect(screen.getByRole("spinbutton", { name: "Accepted units for TWISTED TEA" })).toHaveValue(12);
    expect(screen.getByTestId("confirm-receipt")).toBeEnabled();

    await user.clear(bud);
    await user.type(bud, "46");
    expect(screen.getByText("Differs from expected: give a reason")).toBeInTheDocument();
    expect(screen.getByTestId("confirm-receipt")).toBeDisabled();

    await user.type(screen.getByRole("textbox", { name: "Reason for BUD LIGHT B-4/6" }), "2 cans crushed");
    await user.click(screen.getByTestId("confirm-receipt"));
    await waitFor(() => expect(postReceipt).toHaveBeenCalledTimes(1));
    const [invoiceId, body] = postReceipt.mock.calls[0];
    expect(invoiceId).toBe("inv-508655");
    expect(body.lines).toEqual([
      { invoice_item_id: "line-1", accepted_units: 46, reason: "2 cans crushed" },
      { invoice_item_id: "line-2", accepted_units: 12, reason: null },
    ]);
    expect(body.received_on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("refuses a fractional or negative quantity before it is sent", async () => {
    const user = await openReceipt();
    const tea = await screen.findByRole("spinbutton", { name: "Accepted units for TWISTED TEA" });
    await user.clear(tea);
    await user.type(tea, "11.5");
    expect(screen.getByText("Whole sellable units only")).toBeInTheDocument();
    expect(screen.getByTestId("confirm-receipt")).toBeDisabled();
  });

  it("blocks the whole receipt and names every product without an approved conversion", async () => {
    getReceiptPreview.mockResolvedValue({
      ...PREVIEW, can_post: false, lines: [PREVIEW.lines[0]],
      blockers: [{ sort_order: 2, description: "FIREBALL 50ML", item_code: "08800404783", reason: "no approved units-per-case mapping" }],
    });
    await openReceipt();
    const blocked = await screen.findByTestId("receipt-blocked");
    expect(within(blocked).getByText("FIREBALL 50ML")).toBeInTheDocument();
    expect(within(blocked).getByText("08800404783")).toBeInTheDocument();
    expect(screen.queryByTestId("confirm-receipt")).not.toBeInTheDocument();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  it("shows an invoice that was already received, without offering to receive it again", async () => {
    getReceiptPreview.mockResolvedValue({
      ...PREVIEW, can_post: false,
      receipt: { id: "r1", invoice_id: "inv-508655", invoice_number: "508655", status: "POSTED", received_on: "2026-10-09",
                 confirmed_by: "barj", confirmed_role: "MANAGER", line_count: 2, discrepancy_count: 1,
                 created_at: "2026-10-09T12:00:00Z" },
    });
    await openReceipt();
    expect(await screen.findByTestId("receipt-posted")).toHaveTextContent("Confirmed by barj");
    expect(screen.queryByTestId("confirm-receipt")).not.toBeInTheDocument();
  });

  it("reverses a posted receipt only with a reason, naming the receipt it reverses", async () => {
    const posted = { id: "r1", invoice_id: "inv-508655", invoice_number: "508655", status: "POSTED" as const,
                     received_on: "2026-10-09", confirmed_by: "barj", confirmed_role: "MANAGER", line_count: 2,
                     discrepancy_count: 0, created_at: "2026-10-09T12:00:00Z" };
    getReceiptPreview.mockResolvedValue({ ...PREVIEW, can_post: false, receipt: posted, history: [posted] });
    reverseReceipt.mockResolvedValue({ receipt: { ...posted, status: "REVERSED" }, movements_reversed: 2, already_reversed: false });
    listStoreInvoices.mockResolvedValue({ success: true, total: 1, page: 1, page_size: 25, request_id: null,
                                          items: [{ ...INVOICE, receipt: posted }] });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("tab", { name: /invoices/i }));
    const row = (await screen.findByText("#508655")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "View" }));
    await user.click(await screen.findByRole("button", { name: "Reverse receipt…" }));
    expect(screen.getByTestId("confirm-reverse")).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "Why the receipt is being reversed" }), "lager was 2 cases");
    await user.click(screen.getByTestId("confirm-reverse"));
    await waitFor(() => expect(reverseReceipt).toHaveBeenCalledWith("inv-508655", { receipt_id: "r1", reason: "lager was 2 cases" }));
  });

  it("shows a reversed receipt as history and offers to receive the invoice again", async () => {
    const reversed = { id: "r1", invoice_id: "inv-508655", invoice_number: "508655", status: "REVERSED" as const,
                       received_on: "2026-10-09", confirmed_by: "barj", confirmed_role: "MANAGER", line_count: 2,
                       discrepancy_count: 0, created_at: "2026-10-09T12:00:00Z", reversed_at: "2026-10-09T13:00:00Z",
                       reversed_by: "prabh", reversal_reason: "lager was 2 cases" };
    listStoreInvoices.mockResolvedValue({ success: true, total: 1, page: 1, page_size: 25, request_id: null,
                                          items: [{ ...INVOICE, receipt: reversed }] });
    getReceiptPreview.mockResolvedValue({ ...PREVIEW, history: [reversed] });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("tab", { name: /invoices/i }));
    const row = (await screen.findByText("#508655")).closest("tr")!;
    expect(within(row).getByText("Receipt reversed")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Receive" }));
    expect(await screen.findByTestId("reversed-receipts")).toHaveTextContent("reversed by prabh");
    expect(screen.getByTestId("confirm-receipt")).toBeEnabled();
  });
});
