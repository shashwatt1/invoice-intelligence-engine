import { describe, expect, it } from "vitest";

import type { LineItem } from "@/api/types";

import { buildRowRefs, checkTarget, parseLineReference, rowDomId } from "./line-references";

function row(sort_order: number, overrides: Partial<LineItem> = {}): LineItem {
  return {
    description: `ROW ${sort_order}`, quantity: 1, unit_price: 1, line_total: 1, tax_rate: null,
    unit_deposit: null, sort_order, line_type: "product", product_code: null, unit_discount: 0,
    entry_source: "extracted", correction_history: [], source_pages: [], duplicate_candidate: null,
    corrected_fields: [], ...overrides,
  };
}

describe("buildRowRefs — serial numbers", () => {
  it("numbers every row 1..n in sort_order order, whatever order the rows arrive in", () => {
    const rows = [row(3, { line_type: "charge" }), row(0), row(2, { line_type: "voided" }),
                  row(1, { line_type: "duplicate" }), row(4, { entry_source: "manual" })];
    const refs = buildRowRefs(rows);
    expect(rows.map((r) => refs.serialOf(r))).toEqual([4, 1, 3, 2, 5]);
  });

  it("restarts at 1 for each invoice and ignores gaps or offsets in sort_order", () => {
    const first = [row(0), row(1)];
    const second = [row(5), row(9), row(6)];
    expect(first.map((r) => buildRowRefs(first).serialOf(r))).toEqual([1, 2]);
    const refs = buildRowRefs(second);
    expect(second.map((r) => refs.serialOf(r))).toEqual([1, 3, 2]);
    expect(refs.serialForSortOrder(9)).toBe(3);
  });

  it("still numbers two rows that share a sort_order, but resolves neither by reference", () => {
    const a = row(1, { description: "A" });
    const b = row(1, { description: "B" });
    const rows = [row(0), a, b];
    const refs = buildRowRefs(rows);
    expect([refs.serialOf(a), refs.serialOf(b)]).toEqual([2, 3]);
    expect(refs.isUnique(1)).toBe(false);
    expect(refs.serialForSortOrder(1)).toBeNull();
    expect(refs.isUnique(0)).toBe(true);
  });

  it("has no serial for a row that is not in the list", () => {
    expect(buildRowRefs([row(0)]).serialOf(row(0))).toBeUndefined();
  });
});

describe("parseLineReference", () => {
  it.each([
    ["line_items[31].line_total", 31],
    ["line_items[0].unit_price", 0],
    ["line_items[3]", 3],
    ["line_items[14].quantity", 14],
  ])("reads %s as row %i", (field, expected) => {
    expect(parseLineReference(field)).toBe(expected);
  });

  it.each([
    [undefined], [null], [""], ["subtotal"], ["grand_total"], ["invoice_number"],
    ["line_items[*].unit_price"], ["line_items[]"], ["line_items[-1]"], ["line_items[1.5]"],
    ["line_items[07]"], ["line_items[3"], ["line_items[3]x"], ["xline_items[3]"], [" line_items[3]"],
    ["line_items[99999999999999999999]"],
  ])("names no single row for %s", (field) => {
    expect(parseLineReference(field as string | null | undefined)).toBeNull();
  });
});

describe("checkTarget", () => {
  const rows = [row(0), row(1), row(2), row(2)];
  const refs = buildRowRefs(rows);

  it("resolves a check to the one row holding its sort_order, with that row's serial", () => {
    expect(checkTarget({ field: "line_items[1].line_total" }, refs)).toEqual({ sortOrder: 1, serial: 2 });
  });

  it("does not link invoice-level, wildcard, stale or ambiguous references", () => {
    expect(checkTarget({ field: "subtotal" }, refs)).toBeNull();
    expect(checkTarget({}, refs)).toBeNull();
    expect(checkTarget({ field: "line_items[*].unit_price" }, refs)).toBeNull();
    expect(checkTarget({ field: "line_items[42].line_total" }, refs)).toBeNull();   // stale: no such row
    expect(checkTarget({ field: "line_items[2].line_total" }, refs)).toBeNull();    // ambiguous: two rows
  });

  it("keeps a row prefix from matching a longer index (line_items[1] is not row 14)", () => {
    const many = Array.from({ length: 15 }, (_, i) => row(i));
    const manyRefs = buildRowRefs(many);
    expect(checkTarget({ field: "line_items[14].line_total" }, manyRefs)).toEqual({ sortOrder: 14, serial: 15 });
    expect(checkTarget({ field: "line_items[1].line_total" }, manyRefs)).toEqual({ sortOrder: 1, serial: 2 });
  });
});

describe("rowDomId", () => {
  it("is derived from sort_order", () => {
    expect(rowDomId(31)).toBe("line-row-31");
  });
});
