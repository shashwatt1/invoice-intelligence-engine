import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { InvoiceDetail, MappingQueueRow } from "@/api/types";

import { MappingWorkbench } from "./mapping-workbench";

/**
 * The Requires Mapping workbench: enter Units/Case and submit for
 * approval WITHOUT navigating to the invoice. Submission must go through
 * the exact same governed endpoint (confirmCaseMappings) the invoice's
 * own CaseMappingCard uses — never a separate write path.
 */

vi.mock("@/api/endpoints", () => ({
  confirmCaseMappings: vi.fn(),
  getInvoice: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import * as api from "@/api/endpoints";
import { toast } from "sonner";

const ROW: MappingQueueRow = {
  store: { id: "store-1", label: "RCM", identity_status: "confirmed", display_name: "RCM", address: null, source_codes: [] },
  item_code: "00025328",
  description: "GM VAN MINI CRE",
  invoice_count: 1,
  occurrences: [
    {
      invoice_id: "623b4a1c-d91d-4023-b702-d4eb8fc2683e", document_id: "doc-1",
      invoice_number: "1234", description: "GM VAN MINI CRE", quantity: 2, unit_price: 9.45, pack_size: "12/1",
    },
  ],
  pending_proposal_id: null,
  pending_value: null,
  pending_proposed_by: null,
};

const INVOICE_WITH_EVIDENCE = {
  case_mappings: [{
    item_code: "00025328", description: "GM VAN MINI CRE", pack_size: "12/1", units_per_case: null,
    suggested_units_per_case: 12, suggestion_source: "pack_size", suggestion_candidates: [],
    reference_description: null, reference_avg_cost: null, pending_proposal_id: null, pending_value: null,
    mapped: false,
  }],
} as unknown as InvoiceDetail;

function mount(node: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MappingWorkbench", () => {
  it("opens without navigating to the invoice, and shows the evidence already computed by GET /invoices/{id}", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(INVOICE_WITH_EVIDENCE);
    mount(<MappingWorkbench row={ROW} open onOpenChange={() => {}} />);

    expect(screen.getByTestId("mapping-workbench")).toBeInTheDocument();
    expect(screen.getByText(/Map GM VAN MINI CRE/)).toBeInTheDocument();
    expect(screen.getByText("00025328")).toBeInTheDocument();
    expect(screen.getByTestId("workbench-invoice-link")).toHaveTextContent("1234");
    await waitFor(() => expect(api.getInvoice).toHaveBeenCalledWith(ROW.occurrences[0].invoice_id));
    await waitFor(() => expect(screen.getByText(/pack size/i)).toBeInTheDocument());
  });

  it("rejects zero, negative and non-integer values with a clear error, without silently coercing them", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getInvoice).mockResolvedValue(INVOICE_WITH_EVIDENCE);
    mount(<MappingWorkbench row={ROW} open onOpenChange={() => {}} />);

    const input = screen.getByLabelText("Units per Case");
    const submit = screen.getByTestId("workbench-submit");

    await user.type(input, "0");
    expect(submit).toBeDisabled();
    expect(screen.getByTestId("workbench-error")).toHaveTextContent(/whole number from 1 to 9999/i);

    await user.clear(input);
    await user.type(input, "-5");
    expect(submit).toBeDisabled();

    await user.clear(input);
    await user.type(input, "3.5");
    expect(submit).toBeDisabled();
  });

  it("submits a valid value through the same governed endpoint invoice detail uses, then closes", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    vi.mocked(api.getInvoice).mockResolvedValue(INVOICE_WITH_EVIDENCE);
    vi.mocked(api.confirmCaseMappings).mockResolvedValue({
      saved: 1, case_mappings: [], pdi_export_allowed: false, pdi_export_blocked_reason: null,
    });
    mount(<MappingWorkbench row={ROW} open onOpenChange={onOpenChange} />);

    await user.type(screen.getByLabelText("Units per Case"), "12");
    expect(screen.getByTestId("workbench-submit")).toBeEnabled();
    await user.click(screen.getByTestId("workbench-submit"));

    await waitFor(() =>
      expect(api.confirmCaseMappings).toHaveBeenCalledWith(
        ROW.occurrences[0].invoice_id,
        [{ item_code: "00025328", units_per_case: 12, description: "GM VAN MINI CRE" }],
      ),
    );
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(toast.success).toHaveBeenCalled();
  });

  it("shows an existing pending proposal clearly, and prefills its value", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(INVOICE_WITH_EVIDENCE);
    const pendingRow: MappingQueueRow = {
      ...ROW, pending_proposal_id: "p1", pending_value: 12, pending_proposed_by: "barj",
    };
    mount(<MappingWorkbench row={pendingRow} open onOpenChange={() => {}} />);

    const pending = screen.getByTestId("workbench-pending");
    expect(pending).toHaveTextContent("12");
    expect(pending).toHaveTextContent("barj");
    expect(pending).toHaveTextContent("PENDING");
    expect(screen.getByLabelText("Units per Case")).toHaveValue("12");
  });

  it("keeps the affected invoice link available as supporting context", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(INVOICE_WITH_EVIDENCE);
    mount(<MappingWorkbench row={ROW} open onOpenChange={() => {}} />);
    const link = screen.getByTestId("workbench-invoice-link");
    expect(link).toHaveAttribute("href", `/invoices/${ROW.occurrences[0].invoice_id}`);
  });
});
