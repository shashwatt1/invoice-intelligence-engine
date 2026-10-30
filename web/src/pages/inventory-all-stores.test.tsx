import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { todayIso } from "@/lib/inventory";
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


/**
 * The start date is the server's call. The dialog never sends the browser's own "today": at 03:09 IST on 31 Oct it
 * is still 30 Oct in UTC, and the server refuses a date after its own today. A date is sent only when a person picks
 * one. A failed preview shows the server's message and request id, and the start can never proceed from it.
 */
describe("Zero-start date handling and failures", () => {
  const SERVER_TODAY = "2026-10-30";
  const FUTURE = new ApiError(422, {
    error_code: "ERR_VALIDATION_FAILED", message: "The start date cannot be in the future.",
    detail: { field: "initialization_date", reason: "future_date" },
  }, "9f1c2d3e-aaaa-bbbb-cccc-0123456789ab");

  /** The server's rule: no date -> its own UTC today; a date after it -> refused. */
  const serverRule = (preview: ZeroStartPreview) => async (_id: string, date?: string) => {
    if (date && date > SERVER_TODAY) throw FUTURE;
    return date ? { ...preview, effective_date: date } : { ...preview, effective_date: preview.has_history ? preview.effective_date : SERVER_TODAY };
  };

  const openDialog = async (path = "/inventory?store=empty") => {
    role = "ADMIN";
    const user = userEvent.setup();
    renderPage(path);
    await user.click(await screen.findByRole("button", { name: "Start at zero" }));
    return { user, dialog: await screen.findByTestId("zero-start-dialog") };
  };
  const submit = async (user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement, acknowledge = false) => {
    await within(dialog).findByTestId("zero-start-effect");
    await user.type(within(dialog).getByPlaceholderText(/new store/), "opened with no stock");
    await user.click(within(dialog).getByRole("checkbox", { name: "Confirm the zero start" }));
    if (acknowledge) await user.click(within(dialog).getByRole("checkbox", { name: "Confirm no stock before the first receipt" }));
    await user.click(within(dialog).getByTestId("zero-start-confirm"));
  };

  beforeEach(() => {
    startStoreAtZero.mockResolvedValue({ created: true, baseline: {}, products_initialized: 0 });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
  });

  it("reproduces the IST/UTC boundary and sends no date, so the server's own rule is never tripped", async () => {
    vi.stubEnv("TZ", "Asia/Kolkata");
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-30T21:39:00Z"));                      // 03:09 IST on 31 Oct, still 30 Oct in UTC
    expect(todayIso()).toBe("2026-10-31");                                   // the browser's calendar is a day ahead of UTC
    getZeroStartPreview.mockImplementation(serverRule(PREVIEW_NO_HISTORY));
    const { user, dialog } = await openDialog();
    expect(await within(dialog).findByTestId("zero-start-effect")).toBeInTheDocument();       // no error: the preview worked
    expect(screen.queryByTestId("zero-start-error")).not.toBeInTheDocument();
    expect(getZeroStartPreview.mock.calls.every((c) => c[1] === undefined)).toBe(true);        // never the browser's 31 Oct
    expect(within(dialog).getByTestId("zero-start-date")).toHaveTextContent("30 Oct 2026");    // the server's date
    await submit(user, dialog);
    await waitFor(() => expect(startStoreAtZero).toHaveBeenCalledTimes(1));
    expect(startStoreAtZero.mock.calls[0][1]).not.toHaveProperty("initialization_date");
  });

  it("works the same when the browser's calendar matches UTC", async () => {
    vi.stubEnv("TZ", "UTC");
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-30T21:39:00Z"));
    expect(todayIso()).toBe("2026-10-30");
    getZeroStartPreview.mockImplementation(serverRule(PREVIEW_NO_HISTORY));
    const { user, dialog } = await openDialog();
    await submit(user, dialog);
    await waitFor(() => expect(startStoreAtZero).toHaveBeenCalledTimes(1));
    expect(getZeroStartPreview.mock.calls.every((c) => c[1] === undefined)).toBe(true);
    expect(startStoreAtZero.mock.calls[0][1]).not.toHaveProperty("initialization_date");
  });

  it("sends an explicitly selected date, to both the preview and the start", async () => {
    getZeroStartPreview.mockImplementation(serverRule(PREVIEW_NO_HISTORY));
    const { user, dialog } = await openDialog();
    await within(dialog).findByTestId("zero-start-effect");
    const input = dialog.querySelector('input[type="date"]') as HTMLInputElement;
    expect(input.value).toBe("");                                             // nothing chosen by default
    expect(input.max).toBe(SERVER_TODAY);                                     // the ceiling is the server's today, not the browser's
    fireEvent.change(input, { target: { value: "2026-10-25" } });
    await waitFor(() => expect(getZeroStartPreview).toHaveBeenLastCalledWith("empty", "2026-10-25"));
    await waitFor(() => expect(within(dialog).getByTestId("zero-start-date")).toHaveTextContent("25 Oct 2026"));
    await submit(user, dialog);
    await waitFor(() => expect(startStoreAtZero).toHaveBeenCalledTimes(1));
    expect(startStoreAtZero.mock.calls[0][1]).toMatchObject({ initialization_date: "2026-10-25", confirm: true });
  });

  it("for a store with receipts offers no date, sends none, and keeps the first-receipt rule", async () => {
    getZeroStartPreview.mockImplementation(serverRule(PREVIEW_HISTORY));
    const { user, dialog } = await openDialog();
    await within(dialog).findByTestId("zero-start-history");
    expect(dialog.querySelector('input[type="date"]')).toBeNull();
    expect(within(dialog).getByTestId("zero-start-date")).toHaveTextContent(/10 Sept? 2026/);    // the first receipt, not today
    await submit(user, dialog, true);
    await waitFor(() => expect(startStoreAtZero).toHaveBeenCalledTimes(1));
    expect(startStoreAtZero.mock.calls[0][1]).toMatchObject({ confirm: true, acknowledge_prior_stock: true });
    expect(startStoreAtZero.mock.calls[0][1]).not.toHaveProperty("initialization_date");
    expect(getZeroStartPreview.mock.calls.every((c) => c[1] === undefined)).toBe(true);
  });

  it("still refuses a future date the person picks, shows why, and keeps the start disabled", async () => {
    getZeroStartPreview.mockImplementation(serverRule(PREVIEW_NO_HISTORY));
    const { dialog } = await openDialog();
    await within(dialog).findByTestId("zero-start-effect");
    fireEvent.change(dialog.querySelector('input[type="date"]') as HTMLInputElement, { target: { value: "2026-12-31" } });
    expect(await within(dialog).findByTestId("zero-start-error-message")).toHaveTextContent("The start date cannot be in the future.");
    expect(within(dialog).getByTestId("zero-start-confirm")).toBeDisabled();
    expect(startStoreAtZero).not.toHaveBeenCalled();
  });

  it("shows the server's message, error code and request id when the preview fails, and the start stays disabled", async () => {
    getZeroStartPreview.mockRejectedValue(FUTURE);
    const { dialog } = await openDialog();
    expect(await within(dialog).findByTestId("zero-start-error-message")).toHaveTextContent("The start date cannot be in the future.");
    expect(within(dialog).getByTestId("zero-start-error")).toHaveTextContent("ERR_VALIDATION_FAILED");
    expect(within(dialog).getByTestId("zero-start-error-request")).toHaveTextContent("9f1c2d3e-aaaa-bbbb-cccc-0123456789ab");   // the full id
    expect(within(dialog).queryByTestId("zero-start-effect")).not.toBeInTheDocument();
    expect(within(dialog).queryByPlaceholderText(/new store/)).not.toBeInTheDocument();          // no way to fill in the form
    expect(within(dialog).getByTestId("zero-start-confirm")).toBeDisabled();
    expect(startStoreAtZero).not.toHaveBeenCalled();
  });

  it("explains a network failure plainly and still never allows the start", async () => {
    getZeroStartPreview.mockRejectedValue(new Error("Cannot reach the backend API. Is the server running?"));
    const { dialog } = await openDialog();
    expect(await within(dialog).findByTestId("zero-start-error-message")).toHaveTextContent("Cannot reach the backend API");
    expect(within(dialog).queryByTestId("zero-start-error-request")).not.toBeInTheDocument();
    expect(within(dialog).getByTestId("zero-start-confirm")).toBeDisabled();
  });

  it("shows a server rejection of the start itself to the person", async () => {
    getZeroStartPreview.mockImplementation(serverRule(PREVIEW_NO_HISTORY));
    startStoreAtZero.mockRejectedValue(FUTURE);
    const { user, dialog } = await openDialog();
    await submit(user, dialog);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(String((toast.error as ReturnType<typeof vi.fn>).mock.calls[0][0])).toContain("The start date cannot be in the future.");
  });
});
