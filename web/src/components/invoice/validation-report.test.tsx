import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, type Mock } from "vitest";

import type { LineItem, ValidationCheck, ValidationReport } from "@/api/types";
import { buildRowRefs, checkTarget, type RowLinks } from "@/lib/line-references";

import { ValidationReportCard } from "./validation-report";

/**
 * Shaped like Invoice #501315 (T.J. Sheehan, 38 rows). The two line-math
 * failures and their warnings name the Busch 24/12 row (sort_order 31,
 * row 32) and the Budweiser 18/12 row (sort_order 34, row 35); the two
 * subtotal checks are invoice-level. The field strings follow the backend
 * convention (line_items[<sort_order>]...); they are not captured data.
 */
const CHECKS: ValidationCheck[] = [
  { name: "LINE_ITEM_UNRECONCILED", status: "WARNING", field: "line_items[31].line_total", expected: "19.20", actual: "40.80",
    message: "quantity x unit_price does not match the line total and no discount or deposit on the document explains the difference." },
  { name: "LINE_ITEM_UNRECONCILED", status: "WARNING", field: "line_items[34].line_total", expected: "16.85", actual: "35.50",
    message: "quantity x unit_price does not match the line total and no discount or deposit on the document explains the difference." },
  { name: "SUBTOTAL_UNRECONCILED", status: "WARNING", field: "subtotal", expected: "1619.89", actual: "1294.56",
    message: "Line totals do not sum to the printed subtotal." },
  { name: "LINE_ITEM_MATH", status: "FAILED", field: "line_items[31].line_total", expected: "19.20", actual: "40.80",
    message: "quantity × unit_price does not match the printed line total." },
  { name: "LINE_ITEM_MATH", status: "FAILED", field: "line_items[34].line_total", expected: "16.85", actual: "35.50",
    message: "quantity × unit_price does not match the printed line total." },
  { name: "SUBTOTAL_MATCHES_ITEMS", status: "FAILED", field: "subtotal", expected: "1294.56", actual: "1619.89",
    message: "Sum of line totals does not match the printed subtotal." },
  { name: "INVOICE_NUMBER_PRESENT", status: "PASSED", field: "invoice_number" },
];

function report(checks: ValidationCheck[] = CHECKS): ValidationReport {
  return {
    decision: "REVIEW_REQUIRED",
    confidence: { composite: 0.956, ocr_confidence: 0.97, ai_confidence: 0.95, validation_score: 0.9, weights: {} },
    review_reasons: ["LINE_ITEM_MATH: quantity × unit_price does not match the printed line total."],
    checks,
    summary: { passed: 74, failed: 3, warnings: 3, skipped: 3 },
    validated_at: "2026-10-15T21:41:00Z", duration_ms: 66000,
  } as ValidationReport;
}

function rows(count: number): LineItem[] {
  return Array.from({ length: count }, (_, sort_order) => ({
    description: `ROW ${sort_order}`, quantity: 1, unit_price: 1, line_total: 1, tax_rate: null, unit_deposit: null,
    sort_order, line_type: sort_order === count - 1 ? "charge" : "product", product_code: null, unit_discount: 0,
    entry_source: "extracted", correction_history: [], source_pages: [], duplicate_candidate: null, corrected_fields: [],
  }));
}

function links(items: LineItem[]): RowLinks & { goToRow: Mock<(sortOrder: number) => void> } {
  const refs = buildRowRefs(items);
  return { targetOf: (check) => checkTarget(check, refs), goToRow: vi.fn<(sortOrder: number) => void>() };
}

describe("ValidationReportCard — row links", () => {
  it("links each line check to its row by serial and keeps invoice checks unlinked", async () => {
    const user = userEvent.setup();
    const rowLinks = links(rows(38));
    render(<ValidationReportCard report={report()} rowLinks={rowLinks} />);

    const lineGroup = screen.getByTestId("line-checks");
    const buttons = within(lineGroup).getAllByTestId("check-row-link");
    expect(buttons.map((b) => b.textContent)).toEqual(["Row 32", "Row 35", "Row 32", "Row 35"]);

    const invoiceGroup = screen.getByTestId("invoice-checks");
    expect(invoiceGroup).toHaveTextContent("Subtotal Unreconciled");
    expect(invoiceGroup).toHaveTextContent("Subtotal Matches Items");
    expect(within(invoiceGroup).queryByRole("button")).toBeNull();

    await user.click(within(lineGroup).getAllByRole("button", { name: "Go to row 35" })[0]);
    expect(rowLinks.goToRow).toHaveBeenCalledWith(34);
  });

  it("renders no link for a stale reference or one held by two rows, but keeps it a line check", () => {
    const items = rows(5);
    items.push({ ...items[2], description: "SAME SORT ORDER" });          // two rows hold sort_order 2
    const checks: ValidationCheck[] = [
      { name: "LINE_ITEM_MATH", status: "FAILED", field: "line_items[42].line_total", message: "stale" },
      { name: "LINE_ITEM_MATH", status: "FAILED", field: "line_items[2].line_total", message: "ambiguous" },
      { name: "LINE_ITEM_MATH", status: "FAILED", field: "line_items[1].line_total", message: "reliable" },
    ];
    render(<ValidationReportCard report={report(checks)} rowLinks={links(items)} />);

    const lineGroup = screen.getByTestId("line-checks");
    expect(within(lineGroup).getAllByTestId("check-row-link").map((b) => b.textContent)).toEqual(["Row 2"]);
    expect(lineGroup).toHaveTextContent("stale");
    expect(lineGroup).toHaveTextContent("ambiguous");
    expect(screen.queryByTestId("invoice-checks")).toBeNull();
  });

  it("never links a wildcard or field-less check", () => {
    const checks: ValidationCheck[] = [
      { name: "PRICE_COLUMN", status: "WARNING", field: "line_items[*].unit_price", message: "wildcard" },
      { name: "GRAND_TOTAL_PRESENT", status: "FAILED", message: "no field" },
    ];
    render(<ValidationReportCard report={report(checks)} rowLinks={links(rows(3))} />);
    expect(screen.queryByTestId("check-row-link")).toBeNull();
    expect(screen.queryByTestId("line-checks")).toBeNull();
    expect(screen.getByTestId("invoice-checks")).toHaveTextContent("wildcard");
  });

  it("renders the original flat list, with no links or groups, when no row links are given", () => {
    render(<ValidationReportCard report={report()} />);
    expect(screen.queryByTestId("check-row-link")).toBeNull();
    expect(screen.queryByTestId("line-checks")).toBeNull();
    expect(screen.queryByTestId("invoice-checks")).toBeNull();
    expect(screen.getAllByText("Line Item Math")).toHaveLength(2);
    expect(screen.getByText("Subtotal Matches Items")).toBeInTheDocument();
  });
});
