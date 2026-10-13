import { describe, expect, it } from "vitest";

import { formatMoney, formatPerUnit, formatQuantity, pricePerSellingUnit } from "./invoice-price";

describe("invoice price helpers", () => {
  it("formats money and never shows a missing price as $0.00", () => {
    expect(formatMoney(30)).toBe("$30.00");
    expect(formatMoney(0.125)).toBe("$0.13");
    expect(formatMoney(-4.5)).toBe("-$4.50");
    expect(formatMoney(0)).toBe("$0.00");
    expect([formatMoney(null), formatMoney(undefined), formatMoney(Number.NaN)]).toEqual([null, null, null]);
  });

  it("formats quantities without trailing zeros", () => {
    expect([formatQuantity(2), formatQuantity(1.5), formatQuantity(0.33333)]).toEqual(["2", "1.5", "0.3333"]);
    expect(formatQuantity(null)).toBeNull();
  });

  it("derives the price per selling unit only from a positive price and whole units", () => {
    expect(pricePerSellingUnit(30, 24)).toBeCloseTo(1.25);
    expect(pricePerSellingUnit(10, 24)).toBeCloseTo(0.416666, 5);
    for (const [price, units] of [[null, 24], [0, 24], [-3, 24], [30, null], [30, 0], [30, -2], [30, 2.5], [Number.NaN, 24]] as const) {
      expect(pricePerSellingUnit(price, units)).toBeNull();
    }
  });

  it("shows four decimals below a dollar and two from a dollar up", () => {
    expect(formatPerUnit(0.416666)).toBe("$0.4167");
    expect(formatPerUnit(1.25)).toBe("$1.25");
  });
});
