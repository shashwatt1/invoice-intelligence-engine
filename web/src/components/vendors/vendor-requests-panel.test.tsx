import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { VendorRequest } from "@/api/types";

import { VendorRequestsPanel } from "./vendor-requests-panel";

vi.mock("@/api/endpoints", () => ({
  listVendorRequests: vi.fn(),
  approveVendorRequest: vi.fn(),
  rejectVendorRequest: vi.fn(),
  listVendorOptions: vi.fn(async () => [
    { id: "v-tj", name: "T.J. Sheehan", kind: "vendor", confirmed: true, last_used_at: null },
    { id: "req-2", name: "Another Pending Vendor", kind: "request", confirmed: false, last_used_at: null },
  ]),
}));
let currentRole = "ADMIN";
vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: { username: "barj", role: currentRole } }) }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn(), warning: vi.fn(), success: vi.fn() } }));

import * as api from "@/api/endpoints";

const asked: VendorRequest = {
  id: "req-1", requested_name: "Brand New Vendor", status: "PENDING", requested_by: "alice",
  created_at: "2026-10-30T12:00:00Z", decided_by: null, decided_at: null, decision_note: null, vendor_id: null, waiting_invoices: 2, waiting: [],
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><VendorRequestsPanel /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  currentRole = "ADMIN";
  vi.mocked(api.listVendorRequests).mockResolvedValue([asked]);
});

describe("vendor requests", () => {
  it("lists who asked, when and how many invoices wait", async () => {
    renderPanel();
    const row = await screen.findByTestId("vendor-request");
    expect(row).toHaveTextContent("Brand New Vendor");
    expect(row).toHaveTextContent(/asked by alice/);
    expect(row).toHaveTextContent("2 invoices waiting");
  });

  it.each(["USER", "MANAGER"])("shows nothing to a %s and does not even ask for the requests", async (role) => {
    currentRole = role;
    renderPanel();
    expect(screen.queryByTestId("vendor-requests")).not.toBeInTheDocument();
    expect(api.listVendorRequests).not.toHaveBeenCalled();
  });

  it("lists the invoices waiting on a request and says which only get their vendor filled in", async () => {
    vi.mocked(api.listVendorRequests).mockResolvedValue([{ ...asked, waiting_invoices: 2, waiting: [
      { invoice_id: "i-1", invoice_number: "5001", store: "PB Wolf", retailed: false, received: false },
      { invoice_id: "i-2", invoice_number: "5002", store: "AF 429", retailed: true, received: true },
    ] }]);
    renderPanel();
    const listed = await screen.findByTestId("waiting-invoices");
    expect(listed).toHaveTextContent("Invoice #5001 · PB Wolf");
    expect(listed).toHaveTextContent(/Invoice #5002 · AF 429 · received into inventory \(only its vendor is filled in\)/);
  });

  it("shows nothing when no request is open", async () => {
    vi.mocked(api.listVendorRequests).mockResolvedValue([]);
    renderPanel();
    await waitFor(() => expect(api.listVendorRequests).toHaveBeenCalled());
    expect(screen.queryByTestId("vendor-requests")).not.toBeInTheDocument();
  });

  it("approving creates the vendor", async () => {
    vi.mocked(api.approveVendorRequest).mockResolvedValue({ ...asked, status: "APPROVED", vendor_id: "v-new" });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(api.approveVendorRequest).toHaveBeenCalledWith("req-1", { existingVendorId: undefined, note: undefined }));
  });

  it("a duplicate is resolved by naming the vendor it duplicates", async () => {
    vi.mocked(api.approveVendorRequest).mockResolvedValue({ ...asked, status: "APPROVED", vendor_id: "v-tj" });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: "Already listed…" }));
    expect(screen.getByRole("button", { name: "Use this vendor" })).toBeDisabled();
    await user.click(screen.getByRole("combobox", { name: "Existing vendor" }));
    const offered = await screen.findAllByRole("option");
    expect(offered.map((o) => o.textContent)).toEqual(["T.J. Sheehan"]);       // never another pending request
    await user.click(await screen.findByRole("option", { name: "T.J. Sheehan" }));
    await user.click(screen.getByRole("button", { name: "Use this vendor" }));
    await waitFor(() => expect(api.approveVendorRequest).toHaveBeenCalledWith("req-1", { existingVendorId: "v-tj", note: undefined }));
  });

  it("a rejection cannot be sent without a reason", async () => {
    vi.mocked(api.rejectVendorRequest).mockResolvedValue({ ...asked, status: "REJECTED" });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: "Reject…" }));
    expect(screen.getByRole("button", { name: "Reject request" })).toBeDisabled();
    await user.type(screen.getByLabelText("Reason"), "not a supplier");
    await user.click(screen.getByRole("button", { name: "Reject request" }));
    await waitFor(() => expect(api.rejectVendorRequest).toHaveBeenCalledWith("req-1", "not a supplier"));
  });
});
