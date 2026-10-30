import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { InventoryAllStores, InventoryOverview, OpeningState, StoreInventoryEntry, StoreRef } from "@/api/types";

/**
 * The Inventory page lists every physical store the server returns — with or without invoices, receipts or
 * movements — says plainly when a store's opening count was never entered (unknown, not zero), shows a store
 * the account has no grant for without figures, and offers "Start at zero" to administrators only.
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

vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  getInventoryStatus: (...a: unknown[]) => getInventoryStatus(...a),
  getInventoryAllStores: (...a: unknown[]) => getInventoryAllStores(...a),
  getInventoryOverview: (...a: unknown[]) => getInventoryOverview(...a),
  listInventoryItems: (...a: unknown[]) => listInventoryItems(...a),
  startStoreAtZero: (...a: unknown[]) => startStoreAtZero(...a),
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

describe("Start at zero", () => {
  it("is not offered to a manager", async () => {
    renderPage("/inventory?store=empty");
    expect(await screen.findByTestId("opening-not-set")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start at zero" })).not.toBeInTheDocument();
  });

  it("is offered to an administrator only while the opening is not entered, and needs a reason", async () => {
    role = "ADMIN";
    startStoreAtZero.mockResolvedValue({ created: true, baseline: {} });
    const user = userEvent.setup();
    renderPage("/inventory?store=empty");
    await user.click(await screen.findByRole("button", { name: "Start at zero" }));
    const dialog = await screen.findByTestId("zero-start-dialog");
    expect(within(dialog).getByTestId("zero-start-confirm")).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText(/new store/), "opened with no stock");
    await user.click(within(dialog).getByTestId("zero-start-confirm"));
    await waitFor(() => expect(startStoreAtZero).toHaveBeenCalledTimes(1));
    expect(startStoreAtZero.mock.calls[0][0]).toBe("empty");
    expect(startStoreAtZero.mock.calls[0][1]).toMatchObject({ reason: "opened with no stock" });
  });

  it("is not offered once the store is counted or already started at zero", async () => {
    role = "ADMIN";
    renderPage("/inventory?store=zero");
    await screen.findByTestId("store-header");
    expect(screen.queryByTestId("opening-not-set")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start at zero" })).not.toBeInTheDocument();
  });
});
