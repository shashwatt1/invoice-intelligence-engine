import { describe, expect, it } from "vitest";

import type { VendorOption } from "@/api/types";
import { compactName, exactVendorMatch, filterVendorOptions, plainDate } from "@/lib/vendors";

const option = (name: string): VendorOption => ({ id: name, name, kind: "vendor", confirmed: true, last_used_at: null });
const listed = [option("T.J. Sheehan"), option("PepsiCo Pepsi Beverages Company"), option("Acme & Sons")];

describe("vendor names", () => {
  it("compares names without punctuation, case or spacing", () => {
    expect(compactName("T.J.  Sheehan")).toBe(compactName("tj sheehan"));
    expect(compactName("Acme & Sons")).toBe(compactName("ACME AND SONS"));
  });

  it("filters the list by what was typed and keeps the order", () => {
    expect(filterVendorOptions(listed, "tj").map((o) => o.name)).toEqual(["T.J. Sheehan"]);
    expect(filterVendorOptions(listed, "pepsi").map((o) => o.name)).toEqual(["PepsiCo Pepsi Beverages Company"]);
    expect(filterVendorOptions(listed, "  ").map((o) => o.name)).toEqual(listed.map((o) => o.name));
    expect(filterVendorOptions(listed, "zzz")).toEqual([]);
  });

  it("finds an exact listed vendor so it is never added twice", () => {
    expect(exactVendorMatch(listed, "TJ SHEEHAN")?.name).toBe("T.J. Sheehan");
    expect(exactVendorMatch(listed, "Sheehan")).toBeNull();
    expect(exactVendorMatch(listed, "")).toBeNull();
  });
});

describe("plainDate", () => {
  it("keeps a real calendar day exactly as typed", () => {
    expect(plainDate("2026-10-22")).toBe("2026-10-22");
    expect(plainDate(" 2024-02-29 ")).toBe("2024-02-29");
  });

  it.each(["", "22/10/2026", "2026-13-01", "2026-02-30", "2025-02-29", "1999-12-31", "2026-1-5"])("refuses %j", (value) => {
    expect(plainDate(value)).toBeNull();
  });
});
