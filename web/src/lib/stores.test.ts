import { describe, expect, it } from "vitest";

import type { StoreDirectoryEntry } from "@/api/types";

import { physicalStores, sourceIdentityView, storeOptionLabel } from "./stores";
import helperSource from "./stores.ts?raw";

const base: Omit<StoreDirectoryEntry, "id" | "kind"> = {
  label: "", identity_status: "unresolved", display_name: null, address: null, source_codes: [],
  in_store_directory: false, customer_name: null, address_line_1: null, address_line_2: null, city: null, state: null,
  postal_code: null, status: "active", notes: null, identifiers: [], invoices: 0, pricing_rows: 0, identities: 0,
  catalogue_rows: 0, case_mappings: 0, pending_proposals: 0,
};
const PB_WOLF: StoreDirectoryEntry = { ...base, id: "pb-wolf", kind: "physical", in_store_directory: true };

/** The 47708760 record exactly as the live GET /api/v1/stores returns it. */
const LIVE_SOURCE_IDENTITY: StoreDirectoryEntry = {
  ...base, id: "113a1fd4-d621-4dbe-afdd-b4b9279d5e59", label: "Store 47708760 (location not yet confirmed)",
  identity_status: "unresolved", display_name: null, address: null, source_codes: ["47708760"],
  kind: "source_identity", in_store_directory: false,
  identifiers: [{ source_system: "item_sales", identifier_type: "store_code", identifier_value: "47708760", verified: false }],
  source_identity: { source_system: "item_sales", source_label: "Item Sales", identifier_type: "store_code",
                     identifier_value: "47708760", label: "Item Sales · 47708760" },
};

describe("physical store choices", () => {
  it("never offer a source identity as a physical store", () => {
    expect(physicalStores([PB_WOLF, LIVE_SOURCE_IDENTITY]).map((s) => s.id)).toEqual(["pb-wolf"]);
    expect(physicalStores(undefined)).toEqual([]);
  });

  it("offer only records the Store Master classifies as physical — never one without that classification", () => {
    // An older or partial payload, or a kind this client does not know, is not a physical store.
    const noKind = { ...LIVE_SOURCE_IDENTITY, id: "no-kind", kind: undefined } as unknown as StoreDirectoryEntry;
    const unknownKind = { ...LIVE_SOURCE_IDENTITY, id: "other-kind", kind: "something_new" } as unknown as
      StoreDirectoryEntry;
    expect(physicalStores([PB_WOLF, noKind, unknownKind]).map((s) => s.id)).toEqual(["pb-wolf"]);
  });

  it("decide by classification, not by a store code: a physical store carrying 47708760 stays selectable", () => {
    const physicalWithCode: StoreDirectoryEntry = {
      ...PB_WOLF, id: "physical-with-code", source_codes: ["47708760"],
      identifiers: LIVE_SOURCE_IDENTITY.identifiers,
    };
    expect(physicalStores([physicalWithCode, LIVE_SOURCE_IDENTITY]).map((s) => s.id)).toEqual(["physical-with-code"]);
  });

  it("name no store code in the filtering policy", () => {
    expect(helperSource).not.toContain("47708760");
    expect(helperSource).toMatch(/kind === "physical"/);
  });
});

describe("source identity presentation", () => {
  const identity = LIVE_SOURCE_IDENTITY.source_identity!;

  it("presents a source identity by its source system and identifier", () => {
    expect(sourceIdentityView("source_identity", identity)).toEqual({
      name: "Item Sales · 47708760", system: "Item Sales", identifierLabel: "Store code", identifierValue: "47708760",
    });
  });

  it("presents a physical store as nothing special, whatever codes it carries", () => {
    expect(sourceIdentityView("physical", identity)).toBeNull();
    expect(sourceIdentityView(undefined, identity)).toBeNull();
  });

  it("works from the classification it is given, for any code, and never falls back to 'Store'", () => {
    const other = { ...identity, identifier_value: "12345678", label: "Item Sales · 12345678" };
    expect(sourceIdentityView("source_identity", other)?.name).toBe("Item Sales · 12345678");
    expect(sourceIdentityView("source_identity", null)?.name).toBe("Unidentified source record");
  });

  it("labels list and filter options truthfully", () => {
    expect(storeOptionLabel(LIVE_SOURCE_IDENTITY)).toBe("Item Sales · 47708760 · source identity");
    expect(storeOptionLabel({ ...PB_WOLF, label: "PB Wolf (identity unconfirmed)" })).toBe("PB Wolf (identity unconfirmed)");
  });
});
