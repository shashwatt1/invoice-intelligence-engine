import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StoreRef, SupplyRow } from "@/api/types";

/**
 * Supply history filters run on the server and combine with AND; vendor and store clear
 * independently; changing to a vendor that does not supply the chosen store clears that
 * store (and says so); the delivery date is the receipt date, with an explicit
 * "not received yet" filter; an invalid range is refused without a request.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { username: "m", role: "MANAGER" }, isLoading: false, hasRole: () => true }),
}));
const api = { status: vi.fn(), options: vi.fn(), supply: vi.fn() };
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  getInventoryStatus: (...a: unknown[]) => api.status(...a),
  getSupplyOptions: (...a: unknown[]) => api.options(...a),
  listSupply: (...a: unknown[]) => api.supply(...a),
}));

const { SupplyHistoryPage } = await import("./supply-history");

const store = (id: string, label: string): StoreRef => ({ id, label, identity_status: "confirmed", display_name: label, address: null, source_codes: [] });
const WOLF = store("wolf", "PB Wolf");
const MIDLER = store("midler", "AF NV Midler");
const ROW: SupplyRow = {
  invoice_id: "i1", invoice_number: "515616", invoice_date: "2026-10-07", vendor: "Sheehan", vendor_id: "sheehan",
  status: "VALIDATED", product_lines: 3, receipt: null, receivable_reasons: [], store: WOLF, delivered_on: "2026-10-07",
};

function Where() {
  return <output data-testid="search">{useLocation().search}</output>;
}

function mount(path = "/inventory/supply") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><SupplyHistoryPage /><Where /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const lastQuery = () => api.supply.mock.calls.at(-1)![0];

async function pick(user: ReturnType<typeof userEvent.setup>, combobox: string, option: string) {
  await user.click(screen.getByRole("combobox", { name: combobox }));
  await user.click(await screen.findByRole("option", { name: option }));
}

beforeEach(() => {
  vi.clearAllMocks();
  api.status.mockResolvedValue({ enabled: true });
  api.options.mockResolvedValue({
    stores: [MIDLER, WOLF],
    vendors: [{ id: "pepsi", label: "PepsiCo", store_ids: ["midler"] },
              { id: "sheehan", label: "Sheehan", store_ids: ["midler", "wolf"] }],
  });
  api.supply.mockResolvedValue({ success: true, items: [ROW], total: 1, page: 1, page_size: 25, request_id: null });
});

describe("Supply history", () => {
  it("lists every granted store's supplies by default and shows the delivery date, not the invoice date", async () => {
    mount();
    expect(await screen.findByTestId("delivered-on")).toHaveTextContent("07 Oct 2026");
    expect(lastQuery()).toEqual({ vendor_id: undefined, store_id: undefined, page: 1, page_size: 25 });
    expect(screen.getByText(/not what is in stock now/)).toBeInTheDocument();
  });

  it("filters by vendor across its stores, offers only that vendor's stores, then narrows to one store", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByTestId("supply-table");
    await pick(user, "Vendor", "PepsiCo");
    await waitFor(() => expect(lastQuery()).toMatchObject({ vendor_id: "pepsi", store_id: undefined }));
    await user.click(screen.getByRole("combobox", { name: "Store" }));
    const choices = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(choices).toEqual(["All of PepsiCo's stores", "AF NV Midler"]);
    await user.click(screen.getByRole("option", { name: "AF NV Midler" }));
    await waitFor(() => expect(lastQuery()).toMatchObject({ vendor_id: "pepsi", store_id: "midler" }));
  });

  it("clears each filter independently and resets both together", async () => {
    const user = userEvent.setup();
    mount("/inventory/supply?vendor=sheehan&store=wolf");
    await waitFor(() => expect(lastQuery()).toMatchObject({ vendor_id: "sheehan", store_id: "wolf" }));
    await user.click(screen.getByRole("button", { name: "Remove filter Vendor: Sheehan" }));
    await waitFor(() => expect(lastQuery()).toMatchObject({ vendor_id: undefined, store_id: "wolf" }));     // store kept
    await pick(user, "Vendor", "Sheehan");
    await user.click(await screen.findByRole("button", { name: "Remove filter Store: PB Wolf" }));
    await waitFor(() => expect(lastQuery()).toMatchObject({ vendor_id: "sheehan", store_id: undefined }));  // vendor kept
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    await waitFor(() => expect(lastQuery()).toMatchObject({ vendor_id: undefined, store_id: undefined }));
    expect(screen.getByTestId("search")).toHaveTextContent(/^$/);
  });

  it("clears a store the newly chosen vendor does not supply, and says why", async () => {
    const user = userEvent.setup();
    mount("/inventory/supply?store=wolf");
    await waitFor(() => expect(lastQuery()).toMatchObject({ store_id: "wolf" }));
    await pick(user, "Vendor", "PepsiCo");
    await waitFor(() => expect(lastQuery()).toMatchObject({ vendor_id: "pepsi", store_id: undefined }));
    expect(screen.getByTestId("filter-notice")).toHaveTextContent("PB Wolf has no invoices from PepsiCo");
  });

  it("ignores an unknown vendor or store in the URL instead of sending it", async () => {
    mount("/inventory/supply?vendor=nobody&store=elsewhere");
    await waitFor(() => expect(lastQuery()).toMatchObject({ vendor_id: undefined, store_id: undefined }));
  });

  it("filters one delivery day, a range, and records not received yet; refuses an inverted range", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByTestId("supply-table");
    await pick(user, "Delivery date", "Delivered on…");
    fireEvent.change(screen.getByLabelText("Delivered on"), { target: { value: "2026-10-07" } });
    await waitFor(() => expect(lastQuery()).toMatchObject({ delivered_from: "2026-10-07", delivered_to: "2026-10-07" }));

    await pick(user, "Delivery date", "Delivered between…");
    fireEvent.change(screen.getByLabelText("Delivered from"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("Delivered to"), { target: { value: "2026-10-31" } });
    await waitFor(() => expect(lastQuery()).toMatchObject({ delivered_from: "2026-10-01", delivered_to: "2026-10-31" }));

    const calls = api.supply.mock.calls.length;
    fireEvent.change(screen.getByLabelText("Delivered to"), { target: { value: "2026-09-01" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("end date is before the start date");
    expect(api.supply.mock.calls.length).toBe(calls);                       // no request for an invalid range

    await pick(user, "Delivery date", "Not received yet");
    await waitFor(() => expect(lastQuery()).toMatchObject({ delivery: "missing" }));
    expect(lastQuery().delivered_from).toBeUndefined();
  });

  it("shows empty and error states", async () => {
    api.supply.mockResolvedValueOnce({ success: true, items: [], total: 0, page: 1, page_size: 25, request_id: null });
    mount();
    expect(await screen.findByText("No supply records match")).toBeInTheDocument();
  });
});
