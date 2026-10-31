import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import type { InvoiceDetail } from "@/api/types";

import { DeclarationsCard } from "./declarations-card";

vi.mock("@/api/endpoints", () => ({
  setDeliveryDate: vi.fn(),
  chooseInvoiceVendor: vi.fn(),
  listVendorOptions: vi.fn(async () => [{ id: "v-tj", name: "T.J. Sheehan", confirmed: true, last_used_at: null }]),
  addVendor: vi.fn(),
  requestVendor: vi.fn(),
}));
let currentRole = "MANAGER";
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: { id: "u-1", username: "tester", role: currentRole },
    hasRole: (role: string) => {
      const rank: Record<string, number> = { USER: 1, MANAGER: 2, ADMIN: 3 };
      return rank[currentRole] >= rank[role];
    },
  }),
}));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn(), warning: vi.fn(), success: vi.fn() } }));

import * as api from "@/api/endpoints";

function detail(overrides: Partial<InvoiceDetail> = {}): InvoiceDetail {
  return {
    invoice_id: "inv-1", vendor: { id: "v-x", name: "ACME CORP", display_name: null }, store: { id: "s-1", label: "PB Wolf" },
    vendor_source: "extracted", delivery_date: null, vendor_request: null, vendor_mismatch: null,
    declarations_missing: "Before this invoice can be marked Retailed: set the delivery date (the day the goods arrived) and choose the vendor from the vendor list.",
    ...overrides,
  } as unknown as InvoiceDetail;
}

function renderCard(d: InvoiceDetail) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DeclarationsCard detail={d} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  currentRole = "MANAGER";
});

describe("what a person declared about the invoice", () => {
  it("says plainly when nothing was chosen yet and what Retailed is waiting for", () => {
    renderCard(detail());
    expect(screen.getByTestId("declared-vendor")).toHaveTextContent("ACME CORP");
    expect(screen.getByTestId("vendor-source")).toHaveTextContent(/read from the document, not yet chosen/);
    expect(screen.getByTestId("declared-date")).toHaveTextContent("Not set");
    expect(screen.getByTestId("declarations-missing")).toHaveTextContent(/delivery date.*vendor list/);
  });

  it("shows the delivery day exactly as declared, whatever the browser's time zone", () => {
    vi.stubEnv("TZ", "America/Los_Angeles");
    renderCard(detail({ delivery_date: "2026-10-22", vendor_source: "operator", declarations_missing: null }));
    expect(screen.getByTestId("declared-date")).toHaveTextContent("22 Oct 2026");
    expect(screen.queryByTestId("declarations-missing")).not.toBeInTheDocument();
    vi.unstubAllEnvs();
  });

  it("a vendor still waiting for approval says so, by the name that was asked for", () => {
    renderCard(detail({
      vendor_source: "requested",
      vendor_request: { id: "r-1", name: "Brand New Vendor", status: "PENDING" },
    }));
    expect(screen.getByTestId("declared-vendor")).toHaveTextContent("Brand New Vendor");
    expect(screen.getByTestId("vendor-source")).toHaveTextContent(/waiting for a manager or administrator/);
  });

  it("a vendor that disagrees with the document is stated in words, to anyone who can read the invoice", () => {
    currentRole = "USER";
    renderCard(detail({
      vendor_source: "operator",
      vendor_mismatch: "The document prints the vendor 'Zenith', which does not match the vendor chosen ('Acme').",
    }));
    expect(screen.getByTestId("vendor-mismatch")).toHaveTextContent(/Zenith.*Acme/);
  });
});

describe("changing them", () => {
  it("a USER can read both and change neither", () => {
    currentRole = "USER";
    renderCard(detail());
    expect(screen.queryByRole("button", { name: "Set" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Choose" })).not.toBeInTheDocument();
  });

  it("a MANAGER sets the delivery date as typed", async () => {
    vi.mocked(api.setDeliveryDate).mockResolvedValue({
      delivery_date: "2026-10-22", vendor_id: null, vendor_label: null, vendor_source: "extracted", status: "VALIDATED",
      review_reasons: [], retailed_blocker: null,
    });
    const user = userEvent.setup();
    renderCard(detail());
    await user.click(screen.getByRole("button", { name: "Set" }));
    expect(screen.getByLabelText("Delivery date")).toHaveValue("");
    await user.type(screen.getByLabelText("Delivery date"), "2026-10-22");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setDeliveryDate).toHaveBeenCalledWith("inv-1", "2026-10-22", undefined));
  });

  it("a MANAGER chooses the vendor from the list", async () => {
    vi.mocked(api.chooseInvoiceVendor).mockResolvedValue({
      delivery_date: null, vendor_id: "v-tj", vendor_label: "T.J. Sheehan", vendor_source: "operator", status: "VALIDATED",
      review_reasons: [], retailed_blocker: null,
    });
    const user = userEvent.setup();
    renderCard(detail());
    await user.click(screen.getByRole("button", { name: "Choose" }));
    await user.click(await screen.findByRole("button", { name: "T.J. Sheehan" }));
    await waitFor(() => expect(api.chooseInvoiceVendor).toHaveBeenCalledWith("inv-1", { vendorId: "v-tj" }));
  });

  it("a refusal because a receipt is POSTED is shown in the server's words and nothing looks changed", async () => {
    const { toast } = await import("sonner");
    vi.mocked(api.setDeliveryDate).mockRejectedValue(new ApiError(409, {
      error_code: "ERR_INVOICE_RECEIVED", message: "This invoice has been received into inventory; reverse the receipt first.",
      detail: { reason: "invoice_received" },
    }));
    const user = userEvent.setup();
    renderCard(detail({ delivery_date: "2026-10-09", vendor_source: "operator", declarations_missing: null }));
    const [, changeDate] = screen.getAllByRole("button", { name: "Change" });      // vendor first, then the date
    await user.click(changeDate);
    await user.clear(screen.getByLabelText("Delivery date"));
    await user.type(screen.getByLabelText("Delivery date"), "2026-10-10");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("reverse the receipt first"), expect.anything()));
    expect(screen.getByTestId("declared-date")).toHaveTextContent("09 Oct 2026");
  });

  it("an impossible date cannot be saved", async () => {
    const user = userEvent.setup();
    renderCard(detail());
    await user.click(screen.getByRole("button", { name: "Set" }));
    const input = screen.getByLabelText("Delivery date");
    await user.type(input, "1990-01-01");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});
