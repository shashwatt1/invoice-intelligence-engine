import { describe, expect, it } from "vitest";

import type { StoreDirectoryEntry } from "@/api/types";

import { physicalStores, sourceIdentityIds } from "./stores";

const base: Omit<StoreDirectoryEntry, "id" | "kind"> = {
  label: "", identity_status: "unresolved", display_name: null, address: null, source_codes: [],
  in_store_directory: false, customer_name: null, address_line_1: null, address_line_2: null, city: null, state: null,
  postal_code: null, status: "active", notes: null, identifiers: [], invoices: 0, pricing_rows: 0, identities: 0,
  catalogue_rows: 0, case_mappings: 0, pending_proposals: 0,
};
const PB_WOLF: StoreDirectoryEntry = { ...base, id: "pb-wolf", kind: "physical", in_store_directory: true };
const CODE: StoreDirectoryEntry = { ...base, id: "code-47708760", kind: "source_identity", source_codes: ["47708760"] };

describe("physical store choices", () => {
  it("never offer a source identity as a physical store", () => {
    expect(physicalStores([PB_WOLF, CODE]).map((s) => s.id)).toEqual(["pb-wolf"]);
    expect([...sourceIdentityIds([PB_WOLF, CODE])]).toEqual(["code-47708760"]);
    expect(physicalStores(undefined)).toEqual([]);
  });
});
