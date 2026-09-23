import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { InvoiceDetail, MappingQueueRow } from "@/api/types";

import { RequiresMappingPage } from "./requires-mapping";

/**
 * The Requires Mapping page itself: every unresolved row carries a
 * "Map" action that opens the workbench directly — the affected
 * invoice's link stays separately clickable as supporting context, but
 * is not the only way to perform the mapping (see MappingWorkbench's
 * own tests for the workbench's submit/validation behavior).
 */

vi.mock("@/api/endpoints", () => ({
  listMappingQueue: vi.fn(),
  getMappingQueueSummary: vi.fn(async () => ({ unique_products: 1, invoice_occurrences: 1, stores: 1 })),
  listStores: vi.fn(async () => []),
  getInvoice: vi.fn(),
  confirmCaseMappings: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import * as api from "@/api/endpoints";

const ROW: MappingQueueRow = {
  store: { id: "store-1", label: "RCM", identity_status: "confirmed", display_name: "RCM", address: null, source_codes: [] },
  item_code: "00025328",
  description: "GM VAN MINI CRE",
  invoice_count: 1,
  occurrences: [
    {
      invoice_id: "623b4a1c-d91d-4023-b702-d4eb8fc2683e", document_id: "doc-1",
      invoice_number: "1234", description: "GM VAN MINI CRE", quantity: 2, unit_price: 9.45, pack_size: null,
    },
  ],
  pending_proposal_id: null,
  pending_value: null,
  pending_proposed_by: null,
};

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RequiresMappingPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RequiresMappingPage", () => {
  it("gives every unresolved row a Map action and keeps the affected-invoice link separately clickable", async () => {
    vi.mocked(api.listMappingQueue).mockResolvedValue({
      success: true, items: [ROW], total: 1, page: 1, page_size: 25, request_id: null,
    });
    mount();

    await waitFor(() => expect(screen.getByTestId("mapping-queue-row")).toBeInTheDocument());
    expect(screen.getByTestId("mapping-queue-map-button")).toHaveTextContent("Map");
    expect(screen.getByTestId("mapping-queue-invoice-link")).toHaveAttribute(
      "href", `/invoices/${ROW.occurrences[0].invoice_id}`,
    );
  });

  it("opens the workbench on Map, without navigating away from Requires Mapping", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listMappingQueue).mockResolvedValue({
      success: true, items: [ROW], total: 1, page: 1, page_size: 25, request_id: null,
    });
    vi.mocked(api.getInvoice).mockResolvedValue({ case_mappings: [] } as unknown as InvoiceDetail);
    mount();

    await waitFor(() => expect(screen.getByTestId("mapping-queue-map-button")).toBeInTheDocument());
    await user.click(screen.getByTestId("mapping-queue-map-button"));

    expect(await screen.findByTestId("mapping-workbench")).toBeInTheDocument();
    expect(screen.getByText(/Map GM VAN MINI CRE/)).toBeInTheDocument();
    // Still on the Requires Mapping page underneath — the row is still there.
    expect(screen.getByTestId("mapping-queue-row")).toBeInTheDocument();
  });

  it("shows a pending proposal as 'Pending approval', not as an authoritative mapping", async () => {
    vi.mocked(api.listMappingQueue).mockResolvedValue({
      success: true, items: [{ ...ROW, pending_proposal_id: "p1", pending_value: 12, pending_proposed_by: "barj" }],
      total: 1, page: 1, page_size: 25, request_id: null,
    });
    mount();

    const pending = await screen.findByTestId("mapping-queue-pending");
    expect(pending).toHaveTextContent("Pending approval");
    expect(pending).toHaveTextContent("12");
  });

  it("reads as a real empty state once nothing needs mapping", async () => {
    vi.mocked(api.listMappingQueue).mockResolvedValue({
      success: true, items: [], total: 0, page: 1, page_size: 25, request_id: null,
    });
    vi.mocked(api.getMappingQueueSummary).mockResolvedValue({ unique_products: 0, invoice_occurrences: 0, stores: 0 });
    mount();

    expect(await screen.findByText("Nothing needs mapping")).toBeInTheDocument();
  });
});
