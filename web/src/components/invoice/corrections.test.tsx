import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { InvoiceDetail, LineItem } from "@/api/types";

import { AddRowForm, EditableTotal, VoidRowButton } from "./corrections";

vi.mock("@/api/endpoints", () => ({ addLineItem: vi.fn(), voidLineItem: vi.fn(), correctTotals: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

import * as api from "@/api/endpoints";
import { toast } from "sonner";

const OK = { status: "VALIDATED", failed_checks: 0, pdi_export_allowed: false, pdi_export_blocked_reason: null,
  review_reasons: [], composite_confidence: 1 };
const ITEM = { sort_order: 0, description: "", quantity: 1, unit_price: null, line_total: null, unit_deposit: null, corrected_fields: [] };

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(window, "prompt").mockReturnValue("read off photo 2");
});

describe("adding a line the photos missed", () => {
  it("needs a name and a description, sends the row to THIS invoice, and reports the revalidation", async () => {
    const user = userEvent.setup();
    vi.mocked(api.addLineItem).mockResolvedValue({ item: ITEM, ...OK });
    wrap(<AddRowForm invoiceId="inv-1" by="data-team:shashwat" />);
    await user.click(screen.getByTestId("add-row-toggle"));
    const submit = screen.getByTestId("add-row-submit");
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("Description"), "KEYSTONE LIGHT");
    await user.type(screen.getByLabelText("UPC"), "071990480080");
    await user.type(screen.getByLabelText("Unit price"), "17.70");
    await user.type(screen.getByLabelText("Note"), "on photo 2");
    expect(submit).toBeEnabled();
    await user.click(submit);
    await waitFor(() => expect(api.addLineItem).toHaveBeenCalledWith("inv-1", {
      description: "KEYSTONE LIGHT", product_code: "071990480080", pack_size: null, quantity: "1",
      unit_price: "17.7", unit_deposit: null, unit_discount: null, line_total: null,
      added_by: "data-team:shashwat", note: "on photo 2",
    }));
    expect(toast.success).toHaveBeenCalled();
  });

  it("refuses without a name", async () => {
    const user = userEvent.setup();
    wrap(<AddRowForm invoiceId="inv-1" by="" />);
    await user.click(screen.getByTestId("add-row-toggle"));
    await user.type(screen.getByLabelText("Description"), "X");
    expect(screen.getByTestId("add-row-submit")).toBeDisabled();
    expect(screen.getByText(/enter your name above first/i)).toBeInTheDocument();
  });
});

describe("voiding and correcting totals", () => {
  const item = {
    description: "PHANTOM", sort_order: 2, line_type: "product", quantity: 1, unit_price: 9.99, line_total: 9.99,
    tax_rate: null, unit_deposit: null, unit_discount: null, product_code: null, entry_source: "extracted",
    correction_history: [], source_pages: [], duplicate_candidate: null, corrected_fields: [],
  } as LineItem;

  it("voids with the name and the prompted note", async () => {
    const user = userEvent.setup();
    vi.mocked(api.voidLineItem).mockResolvedValue({ item: ITEM, ...OK });
    wrap(<VoidRowButton invoiceId="inv-1" item={item} by="rev" />);
    await user.click(screen.getByRole("button", { name: "Void PHANTOM" }));
    await waitFor(() => expect(api.voidLineItem).toHaveBeenCalledWith("inv-1", 2, "rev", "read off photo 2"));
  });

  it("does not offer to void an already voided row, or without a name", () => {
    const { container } = wrap(<VoidRowButton invoiceId="inv-1" item={{ ...item, line_type: "voided" }} by="rev" />);
    expect(container.querySelector("button")).toBeNull();
    wrap(<VoidRowButton invoiceId="inv-1" item={item} by="" />);
    expect(screen.getByRole("button", { name: "Void PHANTOM" })).toBeDisabled();
  });

  it("corrects the grand total with attribution and shows the extracted value in the history", async () => {
    const user = userEvent.setup();
    vi.mocked(api.correctTotals).mockResolvedValue({ ...OK, subtotal: null, tax_amount: null, discount_amount: null,
      deposit_total: null, fuel_surcharge: null, grand_total: 100.65, corrected_fields: ["grand_total"], correction_history: [],
      pdi_export_blocked_reason: null });
    const detail = {
      invoice_id: "inv-1", currency: "USD", grand_total: 100, subtotal: 100.65, tax_amount: null, discount_amount: null,
      deposit_total: null, fuel_surcharge: null, corrected_fields: ["grand_total"],
      correction_history: [{ field: "grand_total", old: 99, new: 100, by: "rev", at: "2026-09-18T10:00:00Z", note: "first read" }],
    } as unknown as InvoiceDetail;
    wrap(<EditableTotal detail={detail} field="grand_total" by="data-team:shashwat" emphasized />);
    expect(screen.getByTestId("history-note")).toHaveAttribute("title", expect.stringContaining("grand_total: 99 → 100 · rev"));
    await user.click(screen.getByRole("button", { name: "Edit Grand total" }));
    const input = screen.getByLabelText("Correct Grand total");
    await user.clear(input);
    await user.type(input, "100.65");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.correctTotals).toHaveBeenCalledWith("inv-1", {
      grand_total: "100.65", corrected_by: "data-team:shashwat", note: "read off photo 2",
    }));
  });
});
