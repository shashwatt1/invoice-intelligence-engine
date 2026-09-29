import { describe, expect, it } from "vitest";

import { roleLabel, sellingUnitCost } from "./commercial";

describe("sellingUnitCost", () => {
  it("divides the case cost by the multiplier exactly when it can", () => {
    expect(sellingUnitCost(37.4, 4)).toEqual({ value: "9.35", exact: true });
    expect(sellingUnitCost(26.45, 1)).toEqual({ value: "26.45", exact: true });
  });

  it("rounds half-up to the cent in whole-cent arithmetic, and says it rounded", () => {
    expect(sellingUnitCost(16.7, 18)).toEqual({ value: "0.93", exact: false }); // 1670 / 18 = 92.78
    expect(sellingUnitCost(0.1, 4)).toEqual({ value: "0.03", exact: false }); // 10 / 4 = 2.5 → 3
    expect(sellingUnitCost(0.3, 3)).toEqual({ value: "0.10", exact: true }); // no 0.1 + 0.2 noise
  });

  it("gives no figure without a case cost or a valid multiplier", () => {
    expect(sellingUnitCost(null, 4)).toBeNull();
    expect(sellingUnitCost(37.4, null)).toBeNull();
    expect(sellingUnitCost(37.4, 0)).toBeNull();
    expect(sellingUnitCost(37.4, 2.5)).toBeNull();
  });
});

describe("roleLabel", () => {
  it("names each role and never invents one", () => {
    expect([roleLabel("ADMIN"), roleLabel("MANAGER"), roleLabel("USER")]).toEqual(["Administrator", "Manager", "User"]);
    expect(roleLabel(null)).toBe("role not recorded");
  });
});
