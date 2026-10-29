import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StoreDirectoryEntry } from "@/api/types";

import { StoresPage } from "./stores";

/**
 * The Store Directory after reconciliation to the CStorePro store directory.
 * The server scopes the list (GET /stores/directory). An ADMIN sees every
 * physical store in detail and is the only role offered identity actions; a
 * MANAGER sees a short task view of their granted stores. A record known only
 * by a source-system code (Item Sales 47708760) is not shown on this page.
 * Check Inventory opens a store's inventory where the account holds a grant.
 */

vi.mock("@/api/endpoints", () => ({
  listStoreDirectory: vi.fn(),
  createStore: vi.fn(),
  updateStoreIdentity: vi.fn(),
  getInventoryStatus: vi.fn(),
  listInventoryStores: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
let role = "ADMIN";
vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: { id: "me", username: "me", role } }) }));

import * as api from "@/api/endpoints";

function entry(overrides: Partial<StoreDirectoryEntry>): StoreDirectoryEntry {
  return {
    id: "id", label: "Store", identity_status: "unresolved", display_name: null, address: null, source_codes: [],
    kind: "physical", in_store_directory: true, customer_name: null, address_line_1: null, address_line_2: null,
    city: null, state: null, postal_code: null, status: "active", notes: null, identifiers: [], invoices: 0,
    pricing_rows: 0, identities: 0, catalogue_rows: 0, case_mappings: 0, pending_proposals: 0,
    ...overrides,
  };
}

const PB_WOLF = entry({
  id: "pb-wolf", label: "PB Wolf (identity unconfirmed)", display_name: "PB Wolf",
  address: "800 Wolf St, Syracuse, NY 13208", customer_name: "PB Wolf Group Inc", invoices: 2, pending_proposals: 7,
  identifiers: [
    { source_system: "document", identifier_type: "customer_name", identifier_value: "APPLE FOODS II", verified: false },
    { source_system: "store_master", identifier_type: "store_alias", identifier_value: "Apple Foods II", verified: false },
    { source_system: "operator", identifier_type: "store_alias", identifier_value: "WOLF", verified: false },
    { source_system: "cstorepro", identifier_type: "directory_name", identifier_value: "PB Wolf", verified: false },
  ],
});
const AF_429 = entry({ id: "af-429", label: "AF 429 (identity unconfirmed)", display_name: "AF 429",
                       address: "429 Riverside, Johnson City, NY 13790" });
const STRAY = entry({ id: "stray", label: "Somewhere (identity unconfirmed)", display_name: "Somewhere",
                      in_store_directory: false });
const CODE_47708760 = entry({
  id: "code-47708760", label: "Store 47708760 (location not yet confirmed)", source_codes: ["47708760"],
  kind: "source_identity", in_store_directory: false,
  identifiers: [{ source_system: "item_sales", identifier_type: "store_code", identifier_value: "47708760", verified: false }],
  // As the API describes it: a source system's identifier, not a store.
  source_identity: { source_system: "item_sales", source_label: "Item Sales", identifier_type: "store_code",
                     identifier_value: "47708760", label: "Item Sales · 47708760" },
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <StoresPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  role = "ADMIN";
  vi.mocked(api.getInventoryStatus).mockResolvedValue({ enabled: true } as never);
  vi.mocked(api.listInventoryStores).mockResolvedValue([]);
});

describe("StoresPage", () => {
  it("lists physical stores with their identity actions and does not show a source-only code", async () => {
    vi.mocked(api.listStoreDirectory).mockResolvedValue([PB_WOLF, AF_429, CODE_47708760]);
    mount();

    const cards = await screen.findAllByTestId("store-card");
    expect(cards.map((card) => within(card).getAllByText(/PB Wolf|AF 429/)[0].textContent)).toEqual(["PB Wolf", "AF 429"]);
    for (const card of cards) {
      expect(within(card).getByRole("button", { name: /confirm identity/i })).toBeInTheDocument();
      expect(within(card).queryByText("Not in store directory")).not.toBeInTheDocument();
    }
    expect(screen.queryByTestId("source-identities")).not.toBeInTheDocument();
    expect(screen.queryByText("Unresolved source identities")).not.toBeInTheDocument();
    expect(screen.queryByText(/47708760/)).not.toBeInTheDocument();
    expect(screen.getByTestId("add-store-toggle")).toBeInTheDocument();
  });

  it("shows the reconciled directory as 14 physical stores, and no alias as a store", async () => {
    // The Store Master API's shape after reconciliation: the 14 CStorePro stores
    // (aliases carried as identifiers on them) and Item Sales 47708760.
    const names = ["LG - RCM", "AF 429", "PB Wolf", "AF Conklin", "LG - Singh Market", "AF McKinley", "AF Hooper",
                   "Tonopah Texaco", "LG - SGM", "AF 143", "Tonopah Shell", "AF North", "AF MV Tiki", "AF NV Midler"];
    const aliases: Record<string, string[]> = {
      "LG - RCM": ["RCM"], "PB Wolf": ["Apple Foods II", "WOLF"], "AF NV Midler": ["MIDLER"], "AF MV Tiki": ["TIKKI"],
    };
    const directory = names.map((name, n) => entry({
      id: `store-${n}`, label: `${name} (identity unconfirmed)`, display_name: name,
      identifiers: [
        { source_system: "cstorepro", identifier_type: "directory_name", identifier_value: name, verified: false },
        ...(aliases[name] ?? []).map((alias) => (
          { source_system: "operator", identifier_type: "store_alias", identifier_value: alias, verified: false })),
      ],
    }));
    vi.mocked(api.listStoreDirectory).mockResolvedValue([...directory, CODE_47708760]);
    mount();

    const physical = await screen.findAllByTestId("store-card");
    expect(physical).toHaveLength(14);
    const titles = physical.map((card, n) => within(card).getAllByText(names[n])[0].textContent);
    expect(titles).toEqual(names);
    expect(screen.queryByText(/47708760/)).not.toBeInTheDocument();

    // No alias is a store of its own: each appears only as an identifier on its canonical store.
    for (const alias of ["RCM", "Apple Foods II", "WOLF", "MIDLER", "TIKKI"]) {
      expect(titles).not.toContain(alias);
      expect(screen.getAllByText(alias)).toHaveLength(1);
    }
  });

  it("keeps the evidence and aliases visible on the reconciled store", async () => {
    vi.mocked(api.listStoreDirectory).mockResolvedValue([PB_WOLF]);
    mount();
    const card = await screen.findByTestId("store-card");
    expect(within(card).getByText("APPLE FOODS II")).toBeInTheDocument();
    expect(within(card).getByText("WOLF")).toBeInTheDocument();
    expect(within(card).getByText("Apple Foods II")).toBeInTheDocument();
  });

  it("flags a named store the store directory does not list", async () => {
    vi.mocked(api.listStoreDirectory).mockResolvedValue([STRAY]);
    mount();
    const card = await screen.findByTestId("store-card");
    expect(within(card).getByText("Not in store directory")).toBeInTheDocument();
    expect(screen.queryByTestId("source-identities")).not.toBeInTheDocument();
  });

  it("opens inventory for a granted store and explains why an ungranted one cannot be opened", async () => {
    vi.mocked(api.listStoreDirectory).mockResolvedValue([PB_WOLF, AF_429]);
    vi.mocked(api.listInventoryStores).mockResolvedValue([PB_WOLF]);
    mount();
    const link = await screen.findByRole("link", { name: "Check Inventory for PB Wolf" });
    expect(link).toHaveAttribute("href", "/inventory?store=pb-wolf");
    const af = screen.getAllByTestId("store-card")[1];
    expect(within(af).getByTestId("check-inventory")).toBeDisabled();
    expect(within(af).getByText("No inventory access")).toBeInTheDocument();
  });

  it("offers no Check Inventory while inventory is switched off", async () => {
    vi.mocked(api.getInventoryStatus).mockResolvedValue({ enabled: false } as never);
    vi.mocked(api.listStoreDirectory).mockResolvedValue([PB_WOLF]);
    mount();
    await screen.findByTestId("store-card");
    expect(screen.queryByTestId("check-inventory")).not.toBeInTheDocument();
    expect(api.listInventoryStores).not.toHaveBeenCalled();
  });

  it("gives a manager a short task view of their stores, with no identity or data-team detail", async () => {
    role = "MANAGER";
    vi.mocked(api.listStoreDirectory).mockResolvedValue([PB_WOLF]);
    vi.mocked(api.listInventoryStores).mockResolvedValue([PB_WOLF]);
    mount();
    const card = await screen.findByTestId("store-card");
    expect(within(card).getByText("PB Wolf")).toBeInTheDocument();
    expect(within(card).getByText("800 Wolf St, Syracuse, NY 13208", { exact: false })).toBeInTheDocument();
    expect(await within(card).findByRole("link", { name: "Check Inventory for PB Wolf" })).toHaveAttribute(
      "href", "/inventory?store=pb-wolf");
    for (const hidden of [/confirm identity/i, /correct identity/i]) {
      expect(screen.queryByRole("button", { name: hidden })).not.toBeInTheDocument();
    }
    for (const detail of ["Pending proposals", "Approved mappings", "Pricing rows", "Identifiers", "APPLE FOODS II"]) {
      expect(screen.queryByText(detail, { exact: false })).not.toBeInTheDocument();
    }
    expect(screen.queryByTestId("add-store-toggle")).not.toBeInTheDocument();
  });

  it("tells a manager with no granted stores how to get access", async () => {
    role = "MANAGER";
    vi.mocked(api.listStoreDirectory).mockResolvedValue([]);
    mount();
    expect(await screen.findByText("No stores assigned to you")).toBeInTheDocument();
    expect(screen.queryByTestId("add-store-toggle")).not.toBeInTheDocument();
  });
});
