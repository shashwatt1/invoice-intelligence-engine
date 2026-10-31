import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { InvoiceDetail, InvoiceReviewSummary, ValidationReport } from "@/api/types";

import { FinancialSummary } from "./financial-summary";
import { IntelligencePanel } from "./intelligence-panel";
import { InvoiceReviewCard } from "./invoice-review-card";
import { ValidationReportCard } from "./validation-report";

vi.mock("@/api/endpoints", () => ({}));

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
}

const report = (over: Partial<ValidationReport> = {}): ValidationReport => ({
  decision: "REVIEW_REQUIRED",
  confidence: { composite: 0.6, ocr_confidence: 0.97, ai_confidence: 0.85, validation_score: 0.96, weights: {},
                completeness: "INCOMPLETE", composite_uncapped: 0.903 },
  review_reasons: [], checks: [], summary: { passed: 47, failed: 3, warnings: 0, skipped: 2 },
  validated_at: "2026-10-31T00:00:00Z", duration_ms: 1,
  completeness: {
    status: "INCOMPLETE", anchors: 59, lines: 40, missing_codes: ["09000000001"], duplicate_codes: [],
    findings: [{ name: "COMPLETENESS_MISSING_LINES", status: "FAILED", field: "line_items",
                 message: "21 product code(s) printed on the document have no extracted line." }],
  },
  ...over,
});

describe("an incomplete invoice never reads as a good extraction", () => {
  it("the validation report says it is incomplete, names why, and says it cannot be exported", () => {
    wrap(<ValidationReportCard report={report()} />);
    const banner = screen.getByTestId("completeness-banner");
    expect(banner).toHaveTextContent("Incomplete extraction.");
    expect(banner).toHaveTextContent("21 product code(s) printed on the document have no extracted line.");
    expect(banner).toHaveTextContent("cannot be exported");
  });

  it("an unverified invoice is not described as complete", () => {
    wrap(<ValidationReportCard report={report({
      confidence: { composite: 0.9, ocr_confidence: 0.97, ai_confidence: 0.9, validation_score: 1, weights: {}, completeness: "UNVERIFIED" },
      completeness: { status: "UNVERIFIED", anchors: 0, lines: 5, missing_codes: [], duplicate_codes: [], findings: [] } })} />);
    expect(screen.getByTestId("completeness-banner")).toHaveTextContent("Completeness not verified.");
  });

  it("a complete invoice shows no warning banner", () => {
    wrap(<ValidationReportCard report={report({
      confidence: { composite: 0.92, ocr_confidence: 0.97, ai_confidence: 0.9, validation_score: 1, weights: {}, completeness: "COMPLETE" },
      completeness: { status: "COMPLETE", anchors: 61, lines: 61, missing_codes: [], duplicate_codes: [], findings: [] } })} />);
    expect(screen.queryByTestId("completeness-banner")).not.toBeInTheDocument();
  });
});

describe("the intelligence panel", () => {
  const detail = (r: ValidationReport) => ({
    status: "REVIEW_REQUIRED", composite_confidence: 0.6, line_items: [], corrected_fields: [], photos: [],
    validation_report: r,
  } as unknown as InvoiceDetail);

  it("states the cap and the uncapped reading quality for an incomplete invoice", () => {
    wrap(<IntelligencePanel detail={detail(report())} />);
    const note = screen.getByTestId("completeness-incomplete");
    expect(note).toHaveTextContent("Incomplete");
    expect(note).toHaveTextContent("capped");
    expect(note).toHaveTextContent("90%");
  });
});

describe("typed additional charges", () => {
  it("shows a state container deposit as its own line, not as tax or deposit", () => {
    const detail = {
      invoice_id: "i", line_items: [], subtotal: 1821.18, tax_amount: 0, discount_amount: 0, deposit_total: null,
      fuel_surcharge: null, grand_total: 1844.48, corrected_fields: [], correction_history: [],
      other_charges: [{ kind: "state_container_deposit", label: "NY St Deposit @ .05", amount: 23.3 }],
    } as unknown as InvoiceDetail;
    wrap(<FinancialSummary detail={detail} by="x" readOnly />);
    const charge = screen.getByTestId("other-charge");
    expect(charge).toHaveTextContent("NY St Deposit @ .05");
    expect(charge).toHaveTextContent("23.30");
  });
});

describe("an approval whose mapping is gone", () => {
  const review: InvoiceReviewSummary = {
    status: "APPROVED", pending: 0, approved: 2, rejected: 0, approved_not_applied: 1,
    proposals: [
      { id: "a", entity_key: "01200000129", field: "units_per_case", current_value: null, proposed_value: 24, status: "APPROVED",
        source: "document_derived", proposed_by: "x", reviewed_by: "y", reviewed_at: "2026-10-29T01:27:00Z", review_note: null,
        revised_from: null, applied: "NOT_APPLIED" },
      { id: "b", entity_key: "01200000131", field: "units_per_case", current_value: null, proposed_value: 24, status: "APPROVED",
        source: "document_derived", proposed_by: "x", reviewed_by: "y", reviewed_at: "2026-10-29T01:27:00Z", review_note: null,
        revised_from: null, applied: "APPLIED" },
    ],
  };

  it("says so in the headline and on the row, instead of looking healthy", () => {
    wrap(<InvoiceReviewCard invoiceId="inv-1" review={review} />);
    expect(screen.getByTestId("invoice-review-status")).toHaveTextContent("2 items approved · 1 not applied (no mapping)");
    expect(screen.getAllByTestId("not-applied")).toHaveLength(1);
  });
});
