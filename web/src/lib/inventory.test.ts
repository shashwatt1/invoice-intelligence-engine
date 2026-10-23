import { describe, expect, it } from "vitest";

import type { ReceiptLine } from "@/api/types";
import { formatUnits, isDiscrepancy, lineProblem, parseCountLines, todayIso } from "@/lib/inventory";

const LINE: ReceiptLine = {
  invoice_item_id: "l1", sort_order: 1, description: "BUD LIGHT", item_code: "01820000833", cases: "2",
  units_per_case: 24, expected_units: "48", conversion_path: "PRODUCT_MASTER_GLOBAL", conversion_scope: null,
};

describe("receipt line rules", () => {
  it("accepts the expected quantity without a reason", () => {
    expect(lineProblem(LINE, { accepted: "48", reason: "" })).toBeNull();
    expect(isDiscrepancy(LINE, { accepted: "48", reason: "" })).toBe(false);
  });

  it("requires a reason for any difference, including zero", () => {
    expect(lineProblem(LINE, { accepted: "0", reason: "  " })).toMatch(/reason/);
    expect(lineProblem(LINE, { accepted: "0", reason: "refused: damaged" })).toBeNull();
    expect(isDiscrepancy(LINE, { accepted: "50", reason: "" })).toBe(true);
  });

  it("refuses blank, negative and fractional quantities", () => {
    expect(lineProblem(LINE, { accepted: " ", reason: "" })).toMatch(/Enter/);
    expect(lineProblem(LINE, { accepted: "-1", reason: "x" })).toMatch(/negative/);
    expect(lineProblem(LINE, { accepted: "47.5", reason: "x" })).toMatch(/Whole/);
  });
});

describe("opening count lines", () => {
  it("reads code and quantity separated by comma, tab or space", () => {
    expect(parseCountLines("01820053030, 48\n08769283102\t12\n\n08800404783 0")).toEqual({
      entries: [
        { item_code: "01820053030", quantity: 48 },
        { item_code: "08769283102", quantity: 12 },
        { item_code: "08800404783", quantity: 0 },
      ],
      errors: [],
    });
  });

  it("reports an unreadable line instead of dropping it", () => {
    const { entries, errors } = parseCountLines("01820053030, 4.5\nBUD LIGHT, 3\n08769283102, -1");
    expect(entries).toEqual([]);
    expect(errors).toHaveLength(3);
    expect(errors[0]).toMatch(/^Line 1:/);
  });
});

describe("formatting", () => {
  it("shows whole units plainly and nothing as a dash", () => {
    expect(formatUnits("1234.0000")).toBe("1,234");
    expect(formatUnits(null)).toBe("—");
    expect(todayIso(new Date(2026, 9, 9))).toBe("2026-10-09");
  });
});
