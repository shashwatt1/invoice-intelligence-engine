import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentStatusData, StoreCandidate, StoreDirectoryEntry } from "@/api/types";
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
  address: "1409 E St George Blvd", source_codes: [], kind: "physical", in_store_directory: true,
  customer_name: null, address_line_1: null,
  address_line_2: null, city: null, state: null, postal_code: null, status: "confirmed", notes: null,
  identifiers: [], invoices: 0, pricing_rows: 0, identities: 0, catalogue_rows: 0, case_mappings: 0, pending_proposals: 0,
};

/** RCM as the directory lists it: unresolved, no source code — the same shape as any store. */
const RCM: StoreDirectoryEntry = {
  ...STORE, id: "rcm-1", label: "RCM (identity unconfirmed)", identity_status: "unresolved", display_name: "RCM",
  address: "1409 E St George Blvd, St George, UT 84790", status: "active",
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
  vi.mocked(api.listStores).mockResolvedValue([STORE, RCM]);
});

describe("RCM is offered wherever a store is chosen, through the existing pickers", () => {
  it("in the store confirmation and in the pending-invoice assignment", async () => {
    const user = userEvent.setup();
    wrap(<StoreConfirmation status={PAUSED} stores={[STORE, RCM]} />);
    await user.click(screen.getByRole("combobox"));
    expect(await screen.findByRole("option", { name: /RCM \(identity unconfirmed\)/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    vi.mocked(api.assignInvoiceStore).mockResolvedValue({ store: RCM } as never);
    wrap(<StorePendingCard invoiceId="inv-9" />);
    const [, pendingCombo] = screen.getAllByRole("combobox");
    await user.click(pendingCombo);
    await user.click(await screen.findByRole("option", { name: /RCM \(identity unconfirmed\)/ }));
    await user.type(screen.getByLabelText(/assigned by/i), "data-team:shashwat");
    await user.click(screen.getByTestId("assign-store"));
    await waitFor(() => expect(api.assignInvoiceStore).toHaveBeenCalledWith("inv-9", "rcm-1", "data-team:shashwat", null));
  });
});

/** Item Sales store 47708760: a source identity, never a physical store. */
const CODE_47708760: StoreDirectoryEntry = {
  ...STORE, id: "code-47708760", label: "Store 47708760 (location not yet confirmed)", identity_status: "unresolved",
  display_name: null, address: null, source_codes: ["47708760"], kind: "source_identity", in_store_directory: false,
  status: "active",
};

describe("a source identity is never offered as a physical store", () => {
  it("is hidden from the confirmation candidates and from every store dropdown", async () => {
    const user = userEvent.setup();
    const paused: DocumentStatusData = {
      ...PAUSED,
      store_candidates: [
        { store_id: "code-47708760", label: CODE_47708760.label, identity_status: "unresolved", address: null,
          matched_on: [{ kind: "store_code", value: "47708760", source_system: "item_sales", verified: true }] },
        { store_id: "s-1", label: "Red Cliff Market", identity_status: "confirmed", address: null,
          matched_on: [{ kind: "address", value: "1409 E ST GEORGE BLVD", source_system: "document", verified: true }] },
      ],
    };
    wrap(<StoreConfirmation status={paused} stores={[STORE, RCM, CODE_47708760]} />);
    expect(screen.queryByText(/Store 47708760/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("combobox"));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent ?? "");
    expect(options.some((t) => t.includes("47708760"))).toBe(false);
    expect(options.some((t) => t.includes("Red Cliff Market"))).toBe(true);
    await user.keyboard("{Escape}");

    vi.mocked(api.listStores).mockResolvedValue([STORE, RCM, CODE_47708760]);
    wrap(<StorePendingCard invoiceId="inv-9" />);
    const [, pendingCombo] = screen.getAllByRole("combobox");
    await user.click(pendingCombo);
    const pendingOptions = (await screen.findAllByRole("option")).map((o) => o.textContent ?? "");
    expect(pendingOptions.some((t) => t.includes("47708760"))).toBe(false);
    expect(pendingOptions.some((t) => t.includes("RCM"))).toBe(true);
  });

  it("is never suggested while the store directory is still loading, and a physical suggestion still preselects", () => {
    const sourceCandidate: StoreCandidate = {
      store_id: "113a1fd4-d621-4dbe-afdd-b4b9279d5e59", label: "Store 47708760 (location not yet confirmed)",
      identity_status: "unresolved", address: null,
      matched_on: [{ kind: "store_code", value: "47708760", source_system: "item_sales", verified: true }],
    };
    const paused: DocumentStatusData = { ...PAUSED, store_candidates: [sourceCandidate] };
    // Directory not loaded yet: nothing is known to be physical, so nothing is suggested.
    const loading = wrap(<StoreConfirmation status={paused} stores={[]} />);
    expect(screen.queryByText(/Store 47708760/)).not.toBeInTheDocument();
    loading.unmount();

    // A lone physical suggestion is preselected once the directory has arrived.
    const physical: StoreCandidate = { ...sourceCandidate, store_id: "s-1", label: "Red Cliff Market",
                                       identity_status: "confirmed" };
    const later = wrap(<StoreConfirmation status={{ ...PAUSED, store_candidates: [physical] }} stores={[]} />);
    later.rerender(
      <QueryClientProvider client={new QueryClient()}><MemoryRouter>
        <StoreConfirmation status={{ ...PAUSED, store_candidates: [physical] }} stores={[STORE]} />
      </MemoryRouter></QueryClientProvider>,
    );
    expect(screen.getByRole("button", { name: /Red Cliff Market/ })).toHaveClass("border-primary");
  });
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
