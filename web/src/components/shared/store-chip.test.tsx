import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { StoreRef } from "@/api/types";

import { StoreChip } from "./store-chip";

const PHYSICAL: StoreRef = {
  id: "pb-wolf", label: "PB Wolf (identity unconfirmed)", identity_status: "unresolved", display_name: "PB Wolf",
  address: "800 Wolf St, Syracuse, NY 13208", source_codes: [], kind: "physical", source_identity: null,
};
const SOURCE: StoreRef = {
  id: "code", label: "Store 47708760 (location not yet confirmed)", identity_status: "unresolved",
  display_name: null, address: null, source_codes: ["47708760"], kind: "source_identity",
  source_identity: { source_system: "item_sales", source_label: "Item Sales", identifier_type: "store_code",
                     identifier_value: "47708760", label: "Item Sales · 47708760" },
};

const chip = (store: StoreRef, compact = false) =>
  render(<MemoryRouter><StoreChip store={store} compact={compact} /></MemoryRouter>);

describe("StoreChip", () => {
  it("shows a physical store as before", () => {
    chip(PHYSICAL);
    expect(screen.getByText("PB Wolf")).toBeInTheDocument();
    expect(screen.getByText("identity unconfirmed")).toBeInTheDocument();
  });

  it("shows a physical store by its name even when it carries a source code", () => {
    chip({ ...PHYSICAL, source_codes: ["47708760"] }, true);
    expect(screen.getByText("PB Wolf")).toBeInTheDocument();
    expect(screen.queryByText("Source identity")).not.toBeInTheDocument();
  });

  it.each([false, true])("shows a source identity as what it is (compact: %s)", (compact) => {
    const { container } = chip(SOURCE, compact);
    expect(screen.getByText("Item Sales · 47708760")).toBeInTheDocument();
    expect(screen.getByText(/source identity/i)).toBeInTheDocument();
    expect(container).not.toHaveTextContent("Store 47708760");
    expect(container).not.toHaveTextContent(/identity unconfirmed|unconfirmed/);
    expect(screen.getByRole("link")).toHaveAttribute("title", "Item Sales source identity — physical store not identified.");
  });
});
