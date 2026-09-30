import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { VendorDetail, VendorRow } from "@/api/types";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let currentRole = "MANAGER";
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { username: "barj", role: currentRole } }),
}));

const listVendors = vi.fn();
const getVendor = vi.fn();
const confirmVendor = vi.fn();
const reopenVendor = vi.fn();

vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listVendors: (...a: unknown[]) => listVendors(...a),
  getVendor: (...a: unknown[]) => getVendor(...a),
  confirmVendor: (...a: unknown[]) => confirmVendor(...a),
  reopenVendor: (...a: unknown[]) => reopenVendor(...a),
}));

const { VendorsPage } = await import("./vendors");

const UNRESOLVED: VendorRow = {
  id: "v1", label: "TESTANI DISTRIBUTORS INC.", name: "TESTANI DISTRIBUTORS INC.", display_name: null,
  identity_status: "unresolved", tax_id: "12-3456789", invoices: 4, observed_names: 2,
  last_seen_at: "2026-09-29T10:00:00Z",
};
const CONFIRMED: VendorRow = {
  id: "v2", label: "Rocco J. Testani", name: "ROCCO TESTANI", display_name: "Rocco J. Testani",
  identity_status: "confirmed", tax_id: null, invoices: 2, observed_names: 1, last_seen_at: null,
};

function detailOf(row: VendorRow): VendorDetail {
  return {
    ...row, address: "1 Mill Rd, Syracuse NY", phone: null, email: null,
    observed_name_list: [
      { name: "TESTANI DISTRIBUTORS INC.", invoices: 3, first_seen_at: null, last_seen_at: "2026-09-29T10:00:00Z" },
      { name: "Testani Distributors", invoices: 1, first_seen_at: null, last_seen_at: null },
    ],
    observed_tax_ids: [{ tax_id: "12-3456789", invoices: 4 }],
    recent_invoices: [{
      invoice_id: "i1", document_id: "d1", invoice_number: "3376587", invoice_date: "2026-09-12",
      observed_vendor_name: "Testani Distributors", grand_total: "412.50",
      store: { id: "s1", label: "PB Wolf (identity unconfirmed)", identity_status: "unresolved", display_name: "PB Wolf",
               address: null, source_codes: [], kind: "physical", source_identity: null },
    }],
    discrepancies: row.identity_status === "confirmed" ? [{
      invoice_id: "i9", invoice_number: "3376590", observed_vendor_name: "TESTANI BROS",
      observed_vendor_tax_id: null, recorded_at: "2026-09-30T08:00:00Z",
    }] : [],
    history: row.identity_status === "confirmed" ? [{
      decision: "CONFIRM", previous_status: "unresolved", new_status: "confirmed", previous_display_name: null,
      new_display_name: "Rocco J. Testani", reviewer: "prabh", reviewer_role: "MANAGER",
      basis: "Remit-to matches the statement.", decided_at: "2026-09-30T09:00:00Z",
    }] : [],
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><VendorsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openReview(label: string) {
  const user = userEvent.setup();
  renderPage();
  const row = (await screen.findByText(label)).closest("tr")!;
  await user.click(within(row).getByRole("button", { name: /review/i }));
  return { user, dialog: await screen.findByRole("alertdialog") };
}

beforeEach(() => {
  vi.clearAllMocks();
  currentRole = "MANAGER";
  listVendors.mockResolvedValue({ items: [UNRESOLVED, CONFIRMED], total: 2, page: 1, page_size: 50 });
  getVendor.mockImplementation(async (id: string) => detailOf(id === "v2" ? CONFIRMED : UNRESOLVED));
  confirmVendor.mockResolvedValue({ vendor_id: "v1", previous_status: "unresolved", new_status: "confirmed",
                                    display_name: "Rocco J. Testani" });
  reopenVendor.mockResolvedValue({ vendor_id: "v2", previous_status: "confirmed", new_status: "unresolved",
                                   display_name: null });
});

describe("Vendor Master list", () => {
  it("shows each vendor's identity status beside what its invoices printed", async () => {
    renderPage();
    const unresolved = (await screen.findByText("TESTANI DISTRIBUTORS INC.")).closest("tr")!;
    expect(within(unresolved).getByText("Unresolved")).toBeInTheDocument();
    expect(within(unresolved).getByText("12-3456789")).toBeInTheDocument();
    const confirmed = screen.getByText("Rocco J. Testani").closest("tr")!;
    expect(within(confirmed).getByText("Identity confirmed")).toBeInTheDocument();
    expect(within(confirmed).getByText("first printed as ROCCO TESTANI")).toBeInTheDocument();
    expect(listVendors).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 50 }));
  });

  it("filters on the server by status and search", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("TESTANI DISTRIBUTORS INC.");
    await user.click(screen.getByRole("combobox", { name: "Identity status" }));
    await user.click(await screen.findByRole("option", { name: "Unresolved" }));
    await user.type(screen.getByPlaceholderText(/search vendor/i), "testani");
    await waitFor(() => expect(listVendors).toHaveBeenLastCalledWith(
      expect.objectContaining({ identity_status: "unresolved", search: "testani", page: 1 })));
  });
});

describe("Vendor review", () => {
  it("shows the source evidence: printed names, tax ids, address and recent invoices", async () => {
    const { dialog } = await openReview("TESTANI DISTRIBUTORS INC.");
    const names = await within(dialog).findByTestId("observed-names");
    expect(within(names).getByText("Testani Distributors")).toBeInTheDocument();
    expect(within(dialog).getAllByText("12-3456789").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("1 Mill Rd, Syracuse NY")).toBeInTheDocument();
    const invoices = within(dialog).getByTestId("vendor-invoices");
    expect(invoices).toHaveTextContent("#3376587");
    expect(invoices).toHaveTextContent("printed “Testani Distributors”");
    expect(invoices).toHaveTextContent("PB Wolf");
    expect(within(dialog).getByText("Not confirmed")).toBeInTheDocument();
  });

  it("confirms only with a canonical name and a decision basis, and never sends who decided", async () => {
    const { user, dialog } = await openReview("TESTANI DISTRIBUTORS INC.");
    const button = within(dialog).getByRole("button", { name: /confirm vendor identity/i });
    expect(button).toBeDisabled();
    const name = within(dialog).getByLabelText(/canonical vendor name/i);
    expect(name).toHaveValue("TESTANI DISTRIBUTORS INC.");
    await user.clear(name);
    await user.type(name, "Rocco J. Testani");
    expect(button).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/decision basis/i), "Remit-to matches the statement");
    expect(button).toBeEnabled();
    await user.click(button);
    expect(confirmVendor).toHaveBeenCalledWith("v1", { display_name: "Rocco J. Testani",
                                                       basis: "Remit-to matches the statement" });
  });

  it.each([["MANAGER", "Manager"], ["ADMIN", "Administrator"]])(
    "shows a %s the decision, recorded from the signed-in account", async (role, label) => {
      currentRole = role;
      const { dialog } = await openReview("TESTANI DISTRIBUTORS INC.");
      expect(within(dialog).getByTestId("decision-maker")).toHaveTextContent(`barj · ${label}`);
      expect(within(dialog).queryByLabelText(/reviewer|approved by|confirmed by/i)).not.toBeInTheDocument();
    });

  it("lets a USER see the evidence but not decide", async () => {
    currentRole = "USER";
    const { dialog } = await openReview("TESTANI DISTRIBUTORS INC.");
    expect(await within(dialog).findByTestId("observed-names")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /confirm vendor identity/i })).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/decision basis/i)).not.toBeInTheDocument();
    expect(within(dialog).getByText(/manager decision/i)).toBeInTheDocument();
  });

  it("shows a confirmed vendor's history and reopens it only with a basis", async () => {
    const { user, dialog } = await openReview("Rocco J. Testani");
    expect(await within(dialog).findByText(/confirmed by prabh \(Manager\)/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText(/canonical vendor name/i)).not.toBeInTheDocument();
    const reopen = within(dialog).getByRole("button", { name: /^reopen$/i });
    expect(reopen).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/decision basis/i), "Two remit-to entities share this name");
    await user.click(reopen);
    expect(reopenVendor).toHaveBeenCalledWith("v2", { basis: "Two remit-to entities share this name" });
  });
});

describe("vendor reading discrepancies", () => {
  it("shows readings that disagreed with a confirmed vendor", async () => {
    const { dialog } = await openReview("Rocco J. Testani");
    const section = await within(dialog).findByTestId("vendor-discrepancies");
    expect(section).toHaveTextContent("#3376590");
    expect(section).toHaveTextContent("read as “TESTANI BROS”");
  });
});
