import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StoreDirectoryEntry } from "@/api/types";

/**
 * The Invoices list filters by store on the server (so totals and paging stay right),
 * offers physical stores and never a bare source identity without invoices, and keeps
 * the choice in the URL so a link lands on the same view.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { username: "u", role: "ADMIN" }, isLoading: false, hasRole: () => true }),
}));
const api = { listInvoices: vi.fn(), listStores: vi.fn() };
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listInvoices: (...a: unknown[]) => api.listInvoices(...a),
  listStores: (...a: unknown[]) => api.listStores(...a),
}));

const { HistoryPage } = await import("./history");

function store(id: string, label: string, kind: "physical" | "source_identity", invoices = 0): StoreDirectoryEntry {
  return {
    id, label, kind, identity_status: "confirmed", display_name: label, address: null, source_codes: [],
    source_identity: null, in_store_directory: true, customer_name: null, address_line_1: null, address_line_2: null,
    city: null, state: null, postal_code: null, status: "active", notes: null, identifiers: [], invoices,
    pricing_rows: 0, identities: 0, catalogue_rows: 0, case_mappings: 0, pending_proposals: 0,
  };
}

function Search() {
  return <output data-testid="search">{useLocation().search}</output>;
}

function mount(path = "/invoices") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><HistoryPage /><Search /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listInvoices.mockResolvedValue({ success: true, items: [], total: 0, page: 1, page_size: 15 });
  api.listStores.mockResolvedValue([
    store("wolf", "PB Wolf", "physical"),
    store("midler", "AF NV Midler", "physical"),
    store("src", "Item Sales · 47708760", "source_identity"),
  ]);
});

describe("HistoryPage — store filter", () => {
  it("offers physical stores A–Z and filters on the server, keeping the choice in the URL", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("combobox", { name: "Store filter" }));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(options).toEqual(["All stores", "AF NV Midler", "PB Wolf"]);
    await user.click(screen.getByRole("option", { name: "PB Wolf" }));
    await waitFor(() => expect(api.listInvoices).toHaveBeenLastCalledWith(expect.objectContaining({ store_id: "wolf", page: 1 })));
    expect(screen.getByTestId("search")).toHaveTextContent("store=wolf");
    expect(screen.getByTestId("active-filters")).toHaveTextContent("PB Wolf");
  });

  it("reads the store from the URL, and clearing it lists every store again", async () => {
    const user = userEvent.setup();
    mount("/invoices?store=midler");
    await waitFor(() => expect(api.listInvoices).toHaveBeenCalledWith(expect.objectContaining({ store_id: "midler" })));
    await user.click(await screen.findByRole("button", { name: "Remove filter AF NV Midler" }));
    await waitFor(() => expect(api.listInvoices).toHaveBeenLastCalledWith(expect.objectContaining({ store_id: undefined })));
    expect(screen.getByTestId("search")).not.toHaveTextContent("store=");
  });
});
