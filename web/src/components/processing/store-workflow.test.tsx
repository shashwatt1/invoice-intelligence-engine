import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentStatusData, StoreDirectoryEntry } from "@/api/types";
import { StorePendingCard } from "@/components/invoice/store-pending-card";

import { StoreConfirmation } from "./store-confirmation";

vi.mock("@/api/endpoints", () => ({
  confirmDocumentStore: vi.fn(),
  deferDocumentStore: vi.fn(),
  assignInvoiceStore: vi.fn(),
  listStores: vi.fn(),
  createStore: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import * as api from "@/api/endpoints";

const STORE: StoreDirectoryEntry = {
  id: "s-1", label: "Red Cliff Market", identity_status: "confirmed", display_name: "Red Cliff Market",
  address: "1409 E St George Blvd", source_codes: [], customer_name: null, address_line_1: null,
  address_line_2: null, city: null, state: null, postal_code: null, status: "confirmed", notes: null,
  identifiers: [], invoices: 0, pricing_rows: 0, identities: 0, catalogue_rows: 0, case_mappings: 0, pending_proposals: 0,
};

const PAUSED: DocumentStatusData = {
  document_id: "d-1", filename: "inv.jpg", status: "STORE_CONFIRMATION_REQUIRED", is_terminal: false,
  source_type: "ocr", store: null, awaiting_store_confirmation: true, store_candidates: [],
  invoice_id: null, photos: [], error: null, stages: [], created_at: "", updated_at: "",
};

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(api.listStores).mockResolvedValue([STORE]);
});

describe("store confirmation: confirm, or read now and assign later", () => {
  it("still confirms a store exactly as before", async () => {
    const user = userEvent.setup();
    vi.mocked(api.confirmDocumentStore).mockResolvedValue({ ...PAUSED, status: "OCR_COMPLETED", store: STORE });
    wrap(<StoreConfirmation status={PAUSED} stores={[STORE]} />);
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /red cliff market/i }));
    await user.type(screen.getByLabelText(/confirmed by/i), "data-team:shashwat");
    await user.click(screen.getByRole("button", { name: /confirm store and continue/i }));
    await waitFor(() => expect(api.confirmDocumentStore).toHaveBeenCalledWith("d-1", "s-1", "data-team:shashwat"));
    expect(api.deferDocumentStore).not.toHaveBeenCalled();
  });

  it("defers only with a name on record, and never sends a store", async () => {
    const user = userEvent.setup();
    vi.mocked(api.deferDocumentStore).mockResolvedValue({ ...PAUSED, status: "OCR_COMPLETED" });
    wrap(<StoreConfirmation status={PAUSED} stores={[STORE]} />);
    const defer = screen.getByTestId("defer-store");
    expect(defer).toBeDisabled();
    await user.type(screen.getByLabelText(/confirmed by/i), "data-team:shashwat");
    expect(defer).toBeEnabled();
    await user.click(defer);
    await waitFor(() => expect(api.deferDocumentStore).toHaveBeenCalledWith("d-1", "data-team:shashwat", null));
    expect(api.confirmDocumentStore).not.toHaveBeenCalled();
  });
});

describe("a pending invoice gets its store from a person", () => {
  it("assigns the chosen store with a name and note", async () => {
    const user = userEvent.setup();
    vi.mocked(api.assignInvoiceStore).mockResolvedValue({ store: STORE } as never);
    wrap(<StorePendingCard invoiceId="inv-1" />);
    const assign = screen.getByTestId("assign-store");
    expect(assign).toBeDisabled();
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /red cliff market/i }));
    await user.type(screen.getByLabelText(/assigned by/i), "data-team:shashwat");
    await user.type(screen.getByLabelText(/note/i), "confirmed with the manager");
    await user.click(assign);
    await waitFor(() => expect(api.assignInvoiceStore).toHaveBeenCalledWith(
      "inv-1", "s-1", "data-team:shashwat", "confirmed with the manager"));
  });
});
