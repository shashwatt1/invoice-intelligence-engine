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
  listVendorOptions: vi.fn(async () => [{ id: "v-tj", name: "T.J. Sheehan", confirmed: true, last_used_at: null }]),
}));
let currentRole = "MANAGER";
vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: { username: "barj", role: currentRole } }) }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn(), warning: vi.fn(), success: vi.fn() } }));

import * as api from "@/api/endpoints";

const asked: VendorRequest = {
  id: "req-1", requested_name: "Brand New Vendor", status: "PENDING", requested_by: "alice",
  created_at: "2026-10-30T12:00:00Z", decided_by: null, decided_at: null, decision_note: null, vendor_id: null, waiting_invoices: 2,
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><VendorRequestsPanel /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  currentRole = "MANAGER";
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

  it("shows nothing to a USER and does not even ask for the requests", async () => {
    currentRole = "USER";
    renderPanel();
    expect(screen.queryByTestId("vendor-requests")).not.toBeInTheDocument();
    expect(api.listVendorRequests).not.toHaveBeenCalled();
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
