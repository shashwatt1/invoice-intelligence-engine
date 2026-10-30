import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  InventoryAllStores, InventoryOverview, OpeningState, ResolvedValue, StoreInventoryEntry, StoreRef, ZeroStartPreview,
} from "@/api/types";

/**
 * The Inventory page lists every physical store the server returns — with or without invoices, receipts or
 * movements — says plainly when a store's opening count was never entered (unknown, not zero), shows an
 * administrator every store as openable (no grant is needed) while a store the server marks unusable stays
 * locked, and offers "Start at zero" to administrators only. The zero-start dialog shows what it will do (the
 * start date, the products given an explicit zero, the available units after), never calls a declaration a
 * count, and for a store with receipts needs an explicit acknowledgement.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let role: "ADMIN" | "MANAGER" = "MANAGER";
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { username: "tester", role }, isLoading: false, hasRole: () => true }),
}));

const getInventoryStatus = vi.fn();
const getInventoryAllStores = vi.fn();
const getInventoryOverview = vi.fn();
const listInventoryItems = vi.fn();
const startStoreAtZero = vi.fn();
const getZeroStartPreview = vi.fn();

vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  getInventoryStatus: (...a: unknown[]) => getInventoryStatus(...a),
  getInventoryAllStores: (...a: unknown[]) => getInventoryAllStores(...a),
  getInventoryOverview: (...a: unknown[]) => getInventoryOverview(...a),
  listInventoryItems: (...a: unknown[]) => listInventoryItems(...a),
  startStoreAtZero: (...a: unknown[]) => startStoreAtZero(...a),
  getZeroStartPreview: (...a: unknown[]) => getZeroStartPreview(...a),
  listStoreInvoices: async () => ({ success: true, total: 0, page: 1, page_size: 25, request_id: null, items: [] }),
  listPendingApprovals: async () => [],
}));

const { InventoryPage } = await import("./inventory");

const ref = (id: string, label: string): StoreRef => ({ id, label, address: null } as StoreRef);

function overview(store: StoreRef, state: OpeningState, extra: Partial<InventoryOverview> = {}): InventoryOverview {
  return {
    store, tracked_products: 0, opening_required: 0, out_of_stock: 0, in_stock: 0, last_recorded_at: null,
    recent_receipts: [], pending_approvals: 0, opening_state: state, opening_counted_products: 0,
    on_hand_units: null, ...extra,
  };
}

const EMPTY = ref("empty", "AF Empty Store");
const ZERO = ref("zero", "AF Zero Store");
const COUNTED = ref("counted", "AF Counted Store");
const LOCKED = ref("locked", "AF Locked Store");

function entries(): StoreInventoryEntry[] {
  return [
    { store: COUNTED, access_granted: true,
      overview: overview(COUNTED, "COUNTED", { tracked_products: 3, in_stock: 3, on_hand_units: 42, opening_counted_products: 3 }) },
    { store: EMPTY, access_granted: true, overview: overview(EMPTY, "NOT_SET") },
    { store: LOCKED, access_granted: false, overview: null },
    { store: ZERO, access_granted: true, overview: overview(ZERO, "ZERO_START", { tracked_products: 1, in_stock: 1, on_hand_units: 0 }) },
  ];
}

const PREVIEW_NO_HISTORY: ZeroStartPreview = {
  already_started: false, has_history: false, products: 0, catalogue_only_products: 0, counted_products: 0, products_to_initialize: 0,
  posted_receipts: 0, first_receipt_date: null, effective_date: "2026-10-30", basis: "INITIALIZATION_DATE",
  opening_required_after: 0, available_units_after: "0",
};
const PREVIEW_CATALOGUE: ZeroStartPreview = {
  ...PREVIEW_NO_HISTORY, products: 20, catalogue_only_products: 20, products_to_initialize: 20,
};
const PREVIEW_HISTORY: ZeroStartPreview = {
  already_started: false, has_history: true, products: 20, catalogue_only_products: 0, counted_products: 0, products_to_initialize: 20,
  posted_receipts: 2, first_receipt_date: "2026-09-10", effective_date: "2026-09-10", basis: "EARLIEST_POSTED_RECEIPT",
  opening_required_after: 0, available_units_after: "60",
};

function renderPage(path = "/inventory") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><InventoryPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  role = "MANAGER";
  getInventoryStatus.mockResolvedValue({ enabled: true });
  const list = entries();
  getInventoryAllStores.mockResolvedValue({ stores: list, total_physical: list.length, not_granted: 1 } satisfies InventoryAllStores);
  getInventoryOverview.mockImplementation(async (id: string) => list.find((e) => e.store.id === id)!.overview);
  listInventoryItems.mockResolvedValue({ success: true, total: 0, page: 1, page_size: 50, request_id: null, items: [] });
  getZeroStartPreview.mockResolvedValue(PREVIEW_NO_HISTORY);
});

const card = (cards: HTMLElement[], label: string) => cards.find((c) => within(c).queryByText(label))!;

describe("Inventory store list", () => {
  it("lists every store the server returns, including stores with no products, receipts or movements", async () => {
    renderPage();
    const cards = await screen.findAllByTestId("store-card");
    expect(cards).toHaveLength(4);
    const empty = card(cards, "AF Empty Store");
    expect(within(empty).getByText("No receipts yet")).toBeInTheDocument();
    expect(getInventoryOverview).not.toHaveBeenCalled();
  });

  it("never shows an unset opening as zero: unknown stock is a dash, with the state named", async () => {
    renderPage();
    const cards = await screen.findAllByTestId("store-card");
    const empty = card(cards, "AF Empty Store");
    expect(within(empty).getByText("Opening count not entered")).toBeInTheDocument();
    expect(within(empty).getByText("—")).toBeInTheDocument();
    const zero = card(cards, "AF Zero Store");
    expect(within(zero).getByText("Zero start (not counted)")).toBeInTheDocument();
    expect(within(zero).queryByText(/^Counted/)).not.toBeInTheDocument();
    const counted = card(cards, "AF Counted Store");
    expect(within(counted).getByText("Counted · 3 products")).toBeInTheDocument();
    expect(within(counted).getByText("42")).toBeInTheDocument();
  });

  it("shows a store without a grant with no figures and cannot be opened", async () => {
    renderPage("/inventory?store=locked");
    const cards = await screen.findAllByTestId("store-card");
    const locked = card(cards, "AF Locked Store");
    expect(locked).toHaveAttribute("data-access", "none");
    expect(within(locked).getByText("No access for your account")).toBeInTheDocument();
    expect(within(locked).queryByText("Products")).not.toBeInTheDocument();
    expect(screen.queryByTestId("store-header")).not.toBeInTheDocument();        // the URL cannot open it either
    expect(screen.getByTestId("not-granted-note")).toBeInTheDocument();
    expect(listInventoryItems).not.toHaveBeenCalled();
  });

  it("filters by opening state and by missing access, and searches by name", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findAllByTestId("store-card");
    await user.click(screen.getByRole("combobox", { name: "Show stores" }));
    await user.click(await screen.findByRole("option", { name: "Opening not entered" }));
    await waitFor(() => expect(screen.getAllByTestId("store-card")).toHaveLength(1));
    expect(screen.getByText("AF Empty Store")).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "Show stores" }));
    await user.click(await screen.findByRole("option", { name: "No access for me" }));
    await waitFor(() => expect(screen.getByText("AF Locked Store")).toBeInTheDocument());
    expect(screen.getAllByTestId("store-card")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    await user.type(screen.getByRole("textbox", { name: "Search store name or address" }), "counted");
    await waitFor(() => expect(screen.getAllByTestId("store-card")).toHaveLength(1));
  });
});

describe("An administrator opens every store", () => {
  it("shows all stores as openable cards, with figures, when the server reports every one usable", async () => {
    role = "ADMIN";
    const list: StoreInventoryEntry[] = [COUNTED, EMPTY, ZERO, LOCKED].map((st) => ({
      store: st, access_granted: true, overview: overview(st, "NOT_SET") }));
    getInventoryAllStores.mockResolvedValue({ stores: list, total_physical: 4, not_granted: 0 } satisfies InventoryAllStores);
    renderPage();
    const cards = await screen.findAllByTestId("store-card");
    expect(cards).toHaveLength(4);
    expect(cards.every((c) => c.getAttribute("data-access") !== "none")).toBe(true);
    expect(screen.queryByTestId("not-granted-note")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Open AF .* inventory$/ })).toHaveLength(4);
  });

  it("opens a store that has no grant for this account when the URL names it", async () => {
    role = "ADMIN";
    getInventoryAllStores.mockResolvedValue({
      stores: [{ store: LOCKED, access_granted: true, overview: overview(LOCKED, "NOT_SET") }], total_physical: 1, not_granted: 0,
    });
    getInventoryOverview.mockResolvedValue(overview(LOCKED, "NOT_SET"));
    renderPage("/inventory?store=locked");
    expect(await screen.findByTestId("store-header")).toBeInTheDocument();
    expect(within(screen.getByTestId("store-header")).getByText("AF Locked Store")).toBeInTheDocument();
  });
});

describe("Start at zero", () => {
  it("is not offered to a manager", async () => {
    renderPage("/inventory?store=empty");
    expect(await screen.findByTestId("opening-not-set")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start at zero" })).not.toBeInTheDocument();
  });

  it("shows what it will do before anything is written, and never calls it a count", async () => {
    role = "ADMIN";
    getZeroStartPreview.mockResolvedValue(PREVIEW_HISTORY);
    const user = userEvent.setup();
    renderPage("/inventory?store=empty");
    await user.click(await screen.findByRole("button", { name: "Start at zero" }));
    const dialog = await screen.findByTestId("zero-start-dialog");
    expect(await within(dialog).findByTestId("zero-start-products")).toHaveTextContent("20");
    expect(within(dialog).getByTestId("zero-start-available")).toHaveTextContent("60");
    expect(within(dialog).getByTestId("zero-start-date")).toHaveTextContent(/2026|Sep/);
    expect(within(dialog).getByText(/declaration, not a physical count/)).toBeInTheDocument();
    expect(startStoreAtZero).not.toHaveBeenCalled();
  });

  it("for a store with no history needs only a reason and the confirmation", async () => {
    role = "ADMIN";
    startStoreAtZero.mockResolvedValue({ created: true, baseline: {}, products_initialized: 0 });
    const user = userEvent.setup();
    renderPage("/inventory?store=empty");
    await user.click(await screen.findByRole("button", { name: "Start at zero" }));
    const dialog = await screen.findByTestId("zero-start-dialog");
    await within(dialog).findByTestId("zero-start-effect");
    expect(screen.queryByTestId("zero-start-history")).not.toBeInTheDocument();
    const go = within(dialog).getByTestId("zero-start-confirm");
    expect(go).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText(/new store/), "opened with no stock");
    expect(go).toBeDisabled();                                                     // still needs the confirmation
    await user.click(within(dialog).getByRole("checkbox", { name: "Confirm the zero start" }));
    expect(go).toBeEnabled();
    await user.click(go);
    await waitFor(() => expect(startStoreAtZero).toHaveBeenCalledTimes(1));
    expect(startStoreAtZero.mock.calls[0][0]).toBe("empty");
    expect(startStoreAtZero.mock.calls[0][1]).toMatchObject({
      reason: "opened with no stock", confirm: true, acknowledge_prior_stock: false });
  });

  it("for a store with receipts also needs the prior-stock acknowledgement, and points to counting", async () => {
    role = "ADMIN";
    getZeroStartPreview.mockResolvedValue(PREVIEW_HISTORY);
    startStoreAtZero.mockResolvedValue({ created: true, baseline: {}, products_initialized: 20 });
    const user = userEvent.setup();
    renderPage("/inventory?store=empty");
    await user.click(await screen.findByRole("button", { name: "Start at zero" }));
    const dialog = await screen.findByTestId("zero-start-dialog");
    expect(await within(dialog).findByTestId("zero-start-history")).toHaveTextContent(/record physical\s+opening counts instead/);
    await user.type(within(dialog).getByPlaceholderText(/new store/), "go-live");
    await user.click(within(dialog).getByRole("checkbox", { name: "Confirm the zero start" }));
    const go = within(dialog).getByTestId("zero-start-confirm");
    expect(go).toBeDisabled();                                                     // not without the acknowledgement
    await user.click(within(dialog).getByRole("checkbox", { name: "Confirm no stock before the first receipt" }));
    expect(go).toBeEnabled();
    await user.click(go);
    await waitFor(() => expect(startStoreAtZero).toHaveBeenCalledTimes(1));
    expect(startStoreAtZero.mock.calls[0][1]).toMatchObject({ confirm: true, acknowledge_prior_stock: true });
  });

  it("for twenty listed products with no stock movement shows all twenty and needs no prior-stock acknowledgement", async () => {
    role = "ADMIN";
    getZeroStartPreview.mockResolvedValue(PREVIEW_CATALOGUE);
    startStoreAtZero.mockResolvedValue({ created: true, baseline: {}, products_initialized: 20 });
    const user = userEvent.setup();
    renderPage("/inventory?store=empty");
    await user.click(await screen.findByRole("button", { name: "Start at zero" }));
    const dialog = await screen.findByTestId("zero-start-dialog");
    expect(await within(dialog).findByTestId("zero-start-products")).toHaveTextContent("20");
    expect(within(dialog).getByTestId("zero-start-catalogue")).toHaveTextContent("20");
    expect(within(dialog).getByTestId("zero-start-available")).toHaveTextContent("0");
    expect(screen.queryByTestId("zero-start-history")).not.toBeInTheDocument();       // a product list is not stock history
    await user.type(within(dialog).getByPlaceholderText(/new store/), "opened with no stock");
    await user.click(within(dialog).getByRole("checkbox", { name: "Confirm the zero start" }));
    await user.click(within(dialog).getByTestId("zero-start-confirm"));
    await waitFor(() => expect(startStoreAtZero).toHaveBeenCalledTimes(1));
    expect(startStoreAtZero.mock.calls[0][1]).toMatchObject({ confirm: true, acknowledge_prior_stock: false });
  });

  it("offers nothing to change when the store already started at zero", async () => {
    role = "ADMIN";
    getZeroStartPreview.mockResolvedValue({ ...PREVIEW_HISTORY, already_started: true });
    const user = userEvent.setup();
    renderPage("/inventory?store=empty");
    await user.click(await screen.findByRole("button", { name: "Start at zero" }));
    const dialog = await screen.findByTestId("zero-start-dialog");
    expect(await within(dialog).findByText(/already has a zero start/)).toBeInTheDocument();
    expect(within(dialog).getByTestId("zero-start-confirm")).toBeDisabled();
  });

  it("is not offered once the store is counted or already started at zero", async () => {
    role = "ADMIN";
    renderPage("/inventory?store=zero");
    await screen.findByTestId("store-header");
    expect(screen.queryByTestId("opening-not-set")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start at zero" })).not.toBeInTheDocument();
  });
});

describe("After a zero start", () => {
  const STARTED = overview(ZERO, "ZERO_START", {
    tracked_products: 20, in_stock: 20, opening_required: 0, on_hand_units: 60,
    baseline: { baseline_date: "2026-09-10", basis: "EARLIEST_POSTED_RECEIPT", initialization_date: "2026-10-30",
                initialized_by: "tester", reason: "go-live" },
  });

  it("the card keeps all twenty products, shows the available units and no pending opening counts", async () => {
    getInventoryAllStores.mockResolvedValue({
      stores: [{ store: ZERO, access_granted: true, overview: STARTED }], total_physical: 1, not_granted: 0 });
    renderPage();
    const [zeroCard] = await screen.findAllByTestId("store-card");
    expect(within(zeroCard).getByText("Zero start (not counted)")).toBeInTheDocument();
    const stats = within(zeroCard);
    expect(stats.getByText("Products").previousSibling).toHaveTextContent("20");
    expect(stats.getByText("Available units").previousSibling).toHaveTextContent("60");
    expect(stats.getByText("Need opening count").previousSibling).toHaveTextContent("0");
  });

  it("the store view, freshly loaded, shows the state, no banner, and says it is not a count", async () => {
    getInventoryOverview.mockResolvedValue(STARTED);
    renderPage("/inventory?store=zero");
    expect(await screen.findByTestId("opening-state")).toHaveTextContent("Zero start (not counted)");
    expect(screen.getByTestId("opening-state")).toHaveTextContent("60 available units");
    expect(screen.queryByTestId("opening-not-set")).not.toBeInTheDocument();
    expect(screen.getByTestId("baseline-note")).toHaveTextContent(/declared, not counted.*all\s+20 products/);
  });

  it("twenty listed products at an explicit zero show twenty products, zero available and none pending", async () => {
    const started = overview(EMPTY, "ZERO_START", { tracked_products: 20, out_of_stock: 20, opening_required: 0, on_hand_units: 0 });
    getInventoryAllStores.mockResolvedValue({
      stores: [{ store: EMPTY, access_granted: true, overview: started }], total_physical: 1, not_granted: 0 });
    renderPage();
    const [card] = await screen.findAllByTestId("store-card");
    expect(within(card).getByText("Products").previousSibling).toHaveTextContent("20");
    expect(within(card).getByText("Available units").previousSibling).toHaveTextContent("0");
    expect(within(card).getByText("Need opening count").previousSibling).toHaveTextContent("0");
  });

  it("a store with no products that started at zero shows zero available, not unknown", async () => {
    const empty = overview(EMPTY, "ZERO_START", { on_hand_units: 0 });
    getInventoryAllStores.mockResolvedValue({
      stores: [{ store: EMPTY, access_granted: true, overview: empty }], total_physical: 1, not_granted: 0 });
    renderPage();
    const [card] = await screen.findAllByTestId("store-card");
    expect(within(card).getByText("Available units").previousSibling).toHaveTextContent("0");
    expect(within(card).queryByText("—")).not.toBeInTheDocument();
  });

  it("an unset store with twenty received products says twenty need an opening count", async () => {
    const unset = overview(EMPTY, "NOT_SET", { tracked_products: 20, opening_required: 20 });
    getInventoryOverview.mockResolvedValue(unset);
    renderPage("/inventory?store=empty");
    expect(await screen.findByTestId("opening-not-set")).toHaveTextContent("20 of 20 products need an opening count");
    expect(screen.getByTestId("opening-state")).toHaveTextContent("Available units unknown");
  });
});

describe("Reviewed reference values on products", () => {
  const value = (v: Partial<ResolvedValue>): ResolvedValue => ({
    value: null, status: "MISSING", source: null, scope: null, approval: null, mapping_id: null, notes: [], ...v });
  const item = (code: string, description: string | null, units: ResolvedValue, extra: Record<string, unknown> = {}) => ({
    item_code: code, description, status: "OUT_OF_STOCK", base_unit: "SELLABLE_UNIT", opening_date: "2026-10-30",
    opening_quantity: "0", received: "0", sold: "0", other: "0", on_hand: "0", last_movement: null,
    opening_source: "STORE_BASELINE", description_from_reference: false,
    reference: { units_per_case: units, description: value({}) }, ...extra });

  beforeEach(() => {
    getInventoryOverview.mockResolvedValue(overview(EMPTY, "ZERO_START", { tracked_products: 4, out_of_stock: 4, on_hand_units: 0 }));
    listInventoryItems.mockResolvedValue({
      success: true, total: 4, page: 1, page_size: 50, request_id: null,
      items: [
        item("11111111111", "SHARED ITEM", value({ value: 12, status: "RESOLVED", source: "PRODUCT_MASTER_GLOBAL",
                                                   scope: "All stores (shared value)", approval: "APPROVED" })),
        item("22222222222", "STORE ITEM", value({ value: 18, status: "RESOLVED", source: "PRODUCT_MASTER_PHYSICAL_STORE",
                                                  scope: "AF Empty Store", approval: "APPROVED" })),
        item("33333333333", "CONFLICT ITEM", value({ status: "HELD", notes: ["shared value (x12) and global mapping (x24) disagree"] })),
        item("44444444444", "Northwind Cola 12PK", value({}), { description_from_reference: true }),
      ],
    });
  });

  it("shows each product's units per case with where it came from, and holds conflicts out of the numbers", async () => {
    renderPage("/inventory?store=empty");
    const rows = await screen.findAllByTestId("inventory-row");
    expect(rows).toHaveLength(4);
    const units = (i: number) => within(rows[i]).getByTestId("units-per-case");
    expect(units(0)).toHaveTextContent("12");
    expect(units(0)).toHaveTextContent("shared");
    expect(units(1)).toHaveTextContent("18");
    expect(units(1)).toHaveTextContent("store");
    expect(units(2)).toHaveTextContent("Needs review");
    expect(units(2)).not.toHaveTextContent(/\d/);                       // a held value is never shown as a number
    expect(units(3)).toHaveTextContent("—");
  });

  it("shows a description supplied by the reviewed reference like any other, and the figures are unchanged", async () => {
    renderPage("/inventory?store=empty");
    const rows = await screen.findAllByTestId("inventory-row");
    expect(within(rows[3]).getByText("Northwind Cola 12PK")).toBeInTheDocument();
    for (const row of rows) expect(within(row).getByText("Out of stock")).toBeInTheDocument();
  });
});
