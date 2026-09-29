import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { StoreDirectoryEntry } from "@/api/types";

import { StoresPage } from "./stores";

/**
 * The Store Directory after reconciliation to the CStorePro store directory:
 * physical stores are listed as stores; a record known only by a
 * source-system code (Item Sales 47708760) is shown apart, as an unresolved
 * source identity — never as a physical store, and with no confirm action.
 */

vi.mock("@/api/endpoints", () => ({
  listStores: vi.fn(),
  createStore: vi.fn(),
  updateStoreIdentity: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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

describe("StoresPage", () => {
  it("lists physical stores as stores and shows a source-only code apart, with no confirm action", async () => {
    vi.mocked(api.listStores).mockResolvedValue([PB_WOLF, AF_429, CODE_47708760]);
    mount();

    const section = await screen.findByTestId("source-identities");
    expect(within(section).getByText("Unresolved source identities")).toBeInTheDocument();
    expect(within(section).getByText("Store 47708760")).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: /confirm identity/i })).not.toBeInTheDocument();
    expect(within(section).getAllByText(/data-team decision/i).length).toBeGreaterThan(0);

    const cards = screen.getAllByTestId("store-card");
    const physical = cards.filter((card) => !section.contains(card));
    expect(physical.map((card) => within(card).getAllByText(/PB Wolf|AF 429/)[0].textContent)).toEqual(["PB Wolf", "AF 429"]);
    for (const card of physical) {
      expect(within(card).getByRole("button", { name: /confirm identity/i })).toBeInTheDocument();
      expect(within(card).queryByText("Not in store directory")).not.toBeInTheDocument();
    }
  });

  it("shows the reconciled directory as 14 physical stores plus one source identity, and no alias as a store", async () => {
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
    vi.mocked(api.listStores).mockResolvedValue([...directory, CODE_47708760]);
    mount();

    const section = await screen.findByTestId("source-identities");
    const cards = screen.getAllByTestId("store-card");
    const physical = cards.filter((card) => !section.contains(card));
    expect(physical).toHaveLength(14);
    const titles = physical.map((card, n) => within(card).getAllByText(names[n])[0].textContent);
    expect(titles).toEqual(names);

    // 47708760 appears only under unresolved source identities.
    expect(within(section).getAllByTestId("store-card")).toHaveLength(1);
    expect(within(section).getByText("Store 47708760")).toBeInTheDocument();
    for (const card of physical) expect(within(card).queryByText(/47708760/)).not.toBeInTheDocument();

    // No alias is a store of its own: each appears only as an identifier on its canonical store.
    for (const alias of ["RCM", "Apple Foods II", "WOLF", "MIDLER", "TIKKI"]) {
      expect(titles).not.toContain(alias);
      expect(screen.getAllByText(alias)).toHaveLength(1);
    }
  });

  it("keeps the evidence and aliases visible on the reconciled store", async () => {
    vi.mocked(api.listStores).mockResolvedValue([PB_WOLF]);
    mount();
    const card = await screen.findByTestId("store-card");
    expect(within(card).getByText("APPLE FOODS II")).toBeInTheDocument();
    expect(within(card).getByText("WOLF")).toBeInTheDocument();
    expect(within(card).getByText("Apple Foods II")).toBeInTheDocument();
  });

  it("flags a named store the store directory does not list", async () => {
    vi.mocked(api.listStores).mockResolvedValue([STRAY]);
    mount();
    const card = await screen.findByTestId("store-card");
    expect(within(card).getByText("Not in store directory")).toBeInTheDocument();
    expect(screen.queryByTestId("source-identities")).not.toBeInTheDocument();
  });
});
