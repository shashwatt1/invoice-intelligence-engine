import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { InvoiceDetail, InvoiceReviewSummary, LineItem } from "@/api/types";

import { DuplicateReviewCard } from "./duplicate-review-card";
import { InvoiceReviewCard } from "./invoice-review-card";

vi.mock("@/api/endpoints", () => ({ decideDuplicate: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

import * as api from "@/api/endpoints";
import { toast } from "sonner";

const decideDuplicate = vi.mocked(api.decideDuplicate);

function line(sort_order: number, over: Partial<LineItem> = {}): LineItem {
  return {
    description: `PRODUCT ${sort_order}`, quantity: 1, unit_price: 10, line_total: 10, tax_rate: null,
    unit_deposit: null, sort_order, line_type: "product", source_pages: [], duplicate_candidate: null,
    product_code: null, unit_discount: null, entry_source: "extracted", correction_history: [],
    corrected_fields: [], ...over,
  };
}

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe("possible duplicates across photos", () => {
  const detail = {
    invoice_id: "inv-1",
    photos: [{ page_number: 1 }, { page_number: 2 }],
    line_items: [
      line(0, { source_pages: [1] }),
      line(1, { description: "PRODUCT 20", source_pages: [1, 2] }),
      line(2, { description: "PRODUCT 20", source_pages: [2],
                duplicate_candidate: { of_sort_order: 1, reason: "same figures; neighbours differ", resolution: null } }),
    ],
  } as unknown as InvoiceDetail;

  it("shows both rows side by side with their photos and the model's reason", () => {
    wrap(<DuplicateReviewCard detail={detail} />);
    const pair = screen.getByTestId("duplicate-pair");
    expect(within(pair).getByText("Earlier row")).toBeInTheDocument();
    expect(within(pair).getByText("Flagged row")).toBeInTheDocument();
    expect(pair).toHaveTextContent("row 1 · photo 1, 2");
    expect(pair).toHaveTextContent("row 2 · photo 2");
    expect(pair).toHaveTextContent("same figures; neighbours differ");
    expect(screen.getByText("1 to decide")).toBeInTheDocument();
  });

  it("requires a name, then records the decision without touching quantities", async () => {
    const user = userEvent.setup();
    decideDuplicate.mockResolvedValue({
      sort_order: 2, of_sort_order: 1, decision: "same_row", line_type: "duplicate", status: "VALIDATED",
      failed_checks: 0, review_reasons: [], pdi_export_allowed: true, pdi_export_blocked_reason: null,
    });
    wrap(<DuplicateReviewCard detail={detail} />);
    const same = screen.getByRole("button", { name: /same row seen twice/i });
    expect(same).toBeDisabled();
    await user.type(screen.getByLabelText(/decided by/i), "data-team:shashwat");
    await user.type(screen.getByLabelText(/note/i), "photo 2 starts where 1 ended");
    await user.click(same);
    await waitFor(() => expect(decideDuplicate).toHaveBeenCalledWith("inv-1", 2, {
      decision: "same_row", decided_by: "data-team:shashwat", note: "photo 2 starts where 1 ended",
    }));
    expect(toast.success).toHaveBeenCalledWith("Row 2 recorded as the same row as 1 — now validated.");
  });

  it("warns when the totals still disagree after a decision", async () => {
    const user = userEvent.setup();
    localStorage.setItem("data-review.reviewer", "r");
    decideDuplicate.mockResolvedValue({
      sort_order: 2, of_sort_order: 1, decision: "separate_rows", line_type: "product", status: "REVIEW_REQUIRED",
      failed_checks: 1, review_reasons: ["SUBTOTAL_MATCHES_ITEMS: Sum of line totals does not match the printed subtotal."],
      pdi_export_allowed: false, pdi_export_blocked_reason: "review",
    });
    wrap(<DuplicateReviewCard detail={detail} />);
    await user.click(screen.getByRole("button", { name: /two separate rows/i }));
    await waitFor(() => expect(decideDuplicate).toHaveBeenCalledTimes(1));
    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining("SUBTOTAL_MATCHES_ITEMS"));
  });

  it("lists decided pairs and renders nothing when there are none", () => {
    const decided = {
      ...detail,
      line_items: [line(0), line(1, { line_type: "duplicate",
        duplicate_candidate: { of_sort_order: 0, reason: null, resolution: "same_row", decided_by: "rev", note: "ok" } })],
    } as unknown as InvoiceDetail;
    wrap(<DuplicateReviewCard detail={decided} />);
    expect(screen.getByText("1 decided")).toBeInTheDocument();
    expect(screen.getByText(/same row, counted once/)).toBeInTheDocument();
    expect(screen.queryByTestId("duplicate-pair")).not.toBeInTheDocument();

    const { container } = wrap(<DuplicateReviewCard detail={{ ...detail, line_items: [line(0)] } as InvoiceDetail} />);
    expect(container.querySelector('[data-testid="duplicate-review"]')).toBeNull();
  });
});

describe("the invoice's own Data Review", () => {
  const proposal = (id: string, status: InvoiceReviewSummary["proposals"][number]["status"], over = {}) => ({
    id, entity_key: "08200079165", field: "units_per_case", current_value: null, proposed_value: 12,
    status, source: "reference_derived" as const, proposed_by: "frontend:review-ui", reviewed_by: null,
    reviewed_at: null, review_note: null, revised_from: null, ...over,
  });

  it("says nothing is pending when the invoice raised no proposals", () => {
    wrap(<InvoiceReviewCard invoiceId="inv-1" review={{ status: "NONE", pending: 0, approved: 0, rejected: 0, proposals: [] }} />);
    expect(screen.getByTestId("invoice-review-status")).toHaveTextContent("No master-data review raised");
    expect(screen.queryByRole("link", { name: /open master data review/i })).not.toBeInTheDocument();
  });

  it("lists pending items with a link into Data Review filtered to this invoice", () => {
    wrap(<InvoiceReviewCard invoiceId="inv-1" review={{
      status: "PENDING", pending: 2, approved: 0, rejected: 0,
      proposals: [proposal("p1", "PENDING"), proposal("p2", "PENDING", { entity_key: "08200081436" })],
    }} />);
    expect(screen.getByTestId("invoice-review-status")).toHaveTextContent("2 items pending review");
    expect(screen.getAllByTestId("invoice-review-row")).toHaveLength(2);
    expect(screen.getByRole("link", { name: /open master data review/i })).toHaveAttribute("href", "/data-review?status=ALL&invoice=inv-1");
  });

  it("shows the outcome, the reviewer, the note and the revision trail", () => {
    wrap(<InvoiceReviewCard invoiceId="inv-1" review={{
      status: "APPROVED", pending: 0, approved: 1, rejected: 1,
      proposals: [
        proposal("p1", "REJECTED", { reviewed_by: "rev", review_note: "Superseded by revised proposal p2: 12 -> 15." }),
        proposal("p2", "APPROVED", { proposed_value: 15, reviewed_by: "rev", review_note: "verified on package", revised_from: "p1" }),
      ],
    }} />);
    expect(screen.getByTestId("invoice-review-status")).toHaveTextContent("1 item approved, 1 superseded or rejected");
    const rows = screen.getAllByTestId("invoice-review-row");
    expect(rows[1]).toHaveTextContent("revised");
    expect(rows[1]).toHaveTextContent("verified on package");
    expect(rows[1]).toHaveTextContent("15");
  });
});
