import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StoreDirectoryEntry, VendorOption } from "@/api/types";

import { ProcessPage } from "./process";

vi.mock("@/api/endpoints", () => ({
  processInvoice: vi.fn(),
  listStores: vi.fn(async () => []),
  getDocumentStatus: vi.fn(),
  listVendorOptions: vi.fn(async () => []),
  addVendor: vi.fn(),
  requestVendor: vi.fn(),
}));
// Role-dependent store rules: ADMIN by default (may process or defer without a store).
let currentRole = "ADMIN";
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { id: "u-1", username: "tester", role: currentRole } }),
}));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn(), warning: vi.fn(), success: vi.fn() } }));
vi.mock("framer-motion", () => ({
  AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  motion: { div: ({ children, ...rest }: React.ComponentProps<"div"> & Record<string, unknown>) => {
    const { initial, animate, exit, ...dom } = rest as Record<string, unknown>;
    void initial; void animate; void exit;
    return <div {...(dom as React.ComponentProps<"div">)}>{children}</div>;
  } },
}));

import * as api from "@/api/endpoints";

const processInvoice = vi.mocked(api.processInvoice);

function png(name: string, at = 1) {
  return new File([new Uint8Array(2048)], name, { type: "image/png", lastModified: at });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProcessPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  currentRole = "ADMIN";
});

describe("processing several photos as one invoice", () => {
  it("sends every photo, in order, in ONE request", async () => {
    const user = userEvent.setup();
    processInvoice.mockResolvedValue({
      document_id: "d-1", filename: "p1.jpg (+2 more)", status: "UPLOADED", status_url: "/api/v1/documents/d-1",
    });
    renderPage();
    const button = screen.getByTestId("process-button");
    expect(button).toBeDisabled();

    await user.upload(screen.getByTestId("file-input"), [png("p1.jpg", 1), png("p2.jpg", 2), png("p3.jpg", 3)]);
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent("3 photos");
    await user.click(button);

    await waitFor(() => expect(processInvoice).toHaveBeenCalledTimes(1));
    const [files, storeId] = processInvoice.mock.calls[0];
    expect(files.map((f) => f.name)).toEqual(["p1.jpg", "p2.jpg", "p3.jpg"]);
    expect(storeId).toBeNull();
  });
});

describe("the Process Invoice store picker", () => {
  it("offers only physical stores — never the source identity 47708760", async () => {
    const base = {
      identity_status: "unresolved" as const, source_codes: [] as string[], in_store_directory: true,
      customer_name: null, address_line_1: null, address_line_2: null, city: null, state: null, postal_code: null,
      status: "active", notes: null, identifiers: [], invoices: 0, pricing_rows: 0, identities: 0, catalogue_rows: 0,
      case_mappings: 0, pending_proposals: 0,
    };
    vi.mocked(api.listStores).mockResolvedValue([
      { ...base, id: "pb-wolf", label: "PB Wolf (identity unconfirmed)", display_name: "PB Wolf",
        address: "800 Wolf St, Syracuse, NY 13208", kind: "physical" },
      { ...base, id: "lg-rcm", label: "LG - RCM (identity unconfirmed)", display_name: "LG - RCM",
        address: "1409 E Saint George Blvd, Saint George, UT 84790", kind: "physical" },
      { ...base, id: "code-47708760", label: "Store 47708760 (location not yet confirmed)", display_name: null,
        address: null, source_codes: ["47708760"], kind: "source_identity", in_store_directory: false },
    ]);
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(api.listStores).toHaveBeenCalled());
    await user.click(screen.getByRole("combobox"));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent ?? "");
    expect(options.map((t) => t.split(" (")[0])).toEqual(["PB Wolf", "LG - RCM"]);
    expect(options.some((t) => t.includes("47708760"))).toBe(false);
  });

  it("excludes the live-shaped 47708760 record and anything not classified physical; keeps physical stores", async () => {
    const base = {
      identity_status: "unresolved" as const, source_codes: [] as string[], in_store_directory: true,
      customer_name: null, address_line_1: null, address_line_2: null, city: null, state: null, postal_code: null,
      status: "active", notes: null, identifiers: [], invoices: 0, pricing_rows: 0, identities: 0, catalogue_rows: 0,
      case_mappings: 0, pending_proposals: 0,
    };
    const itemSalesCode = { source_system: "item_sales", identifier_type: "store_code", identifier_value: "47708760",
                            verified: false };
    // Exactly as the live GET /api/v1/stores returns the source identity.
    const liveSourceIdentity: StoreDirectoryEntry = {
      ...base, id: "113a1fd4-d621-4dbe-afdd-b4b9279d5e59", label: "Store 47708760 (location not yet confirmed)",
      identity_status: "unresolved" as const, display_name: null, address: null, source_codes: ["47708760"],
      kind: "source_identity", in_store_directory: false, identifiers: [itemSalesCode],
    };
    // A record with no classification (e.g. an older payload) is not offered either.
    const unclassified = { ...liveSourceIdentity, id: "unclassified", label: "Store 99999999 (location not yet confirmed)",
                           kind: undefined } as unknown as StoreDirectoryEntry;
    vi.mocked(api.listStores).mockResolvedValue([
      { ...base, id: "pb-wolf", label: "PB Wolf (identity unconfirmed)", display_name: "PB Wolf",
        address: "800 Wolf St, Syracuse, NY 13208", kind: "physical" },
      { ...base, id: "af-429", label: "AF 429 (identity unconfirmed)", display_name: "AF 429",
        address: "429 Riverside, Johnson City, NY 13790", kind: "physical", source_codes: ["47708760"],
        identifiers: [itemSalesCode] },
      liveSourceIdentity,
      unclassified,
    ]);
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(api.listStores).toHaveBeenCalled());
    await user.click(screen.getByRole("combobox"));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent ?? "");
    expect(options.map((t) => t.split(" (")[0])).toEqual(["PB Wolf", "AF 429"]);
    expect(options.some((t) => t.includes("location not yet confirmed"))).toBe(false);
  });
});


describe("the physical store rule by role", () => {
  const STORE = {
    id: "pb-wolf", label: "PB Wolf (identity unconfirmed)", identity_status: "unresolved" as const, display_name: "PB Wolf",
    address: "800 Wolf St", source_codes: [] as string[], kind: "physical" as const, in_store_directory: true,
    customer_name: null, address_line_1: null, address_line_2: null, city: null, state: null, postal_code: null,
    status: "active", notes: null, identifiers: [], invoices: 0, pricing_rows: 0, identities: 0, catalogue_rows: 0,
    case_mappings: 0, pending_proposals: 0,
  };

  it.each(["USER", "MANAGER"])("a %s cannot process without choosing a physical store", async (role) => {
    currentRole = role;
    vi.mocked(api.listStores).mockResolvedValue([STORE]);
    const user = userEvent.setup();
    renderPage();
    expect(screen.getAllByText("— required")).toHaveLength(3);                 // store, vendor, delivery date
    await user.upload(screen.getByTestId("file-input"), [png("p1.jpg", 1)]);
    const button = screen.getByTestId("process-button");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Choose the store to process this invoice");
    await waitFor(() => expect(api.listStores).toHaveBeenCalled());
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /PB Wolf/ }));
    // The store alone is not enough any more: the vendor and the delivery day are declared too.
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Choose the vendor to process this invoice");
  });

  it("an ADMIN may leave the store for later", async () => {
    currentRole = "ADMIN";
    renderPage();
    expect(screen.getAllByText(/optional for administrators/)).toHaveLength(3);   // store, vendor, delivery date
  });
});


describe("the vendor and the delivery date declared at upload", () => {
  const STORE = {
    id: "pb-wolf", label: "PB Wolf (identity unconfirmed)", identity_status: "unresolved" as const, display_name: "PB Wolf",
    address: "800 Wolf St", source_codes: [] as string[], kind: "physical" as const, in_store_directory: true,
    customer_name: null, address_line_1: null, address_line_2: null, city: null, state: null, postal_code: null,
    status: "active", notes: null, identifiers: [], invoices: 0, pricing_rows: 0, identities: 0, catalogue_rows: 0,
    case_mappings: 0, pending_proposals: 0,
  };
  const VENDORS: VendorOption[] = [
    { id: "v-tj", name: "T.J. Sheehan", kind: "vendor", confirmed: true, last_used_at: null },
    { id: "v-pepsi", name: "PepsiCo Pepsi Beverages Company", kind: "vendor", confirmed: true, last_used_at: null },
    { id: "req-9", name: "Northwind Brewing", kind: "request", confirmed: false, last_used_at: null },
  ];
  const accepted = { document_id: "d-9", filename: "p1.jpg", status: "UPLOADED" as const, status_url: "/x" };

  async function chooseStoreAndFile(user: ReturnType<typeof userEvent.setup>) {
    await user.upload(screen.getByTestId("file-input"), [png("p1.jpg", 1)]);
    await waitFor(() => expect(api.listStores).toHaveBeenCalled());
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /PB Wolf/ }));
  }

  beforeEach(() => {
    vi.mocked(api.listStores).mockResolvedValue([STORE]);
    vi.mocked(api.listVendorOptions).mockResolvedValue(VENDORS);
    processInvoice.mockResolvedValue(accepted);
  });

  it("starts with no delivery date: nothing is guessed, least of all today", async () => {
    currentRole = "USER";
    renderPage();
    expect(screen.getByLabelText("Delivery date")).toHaveValue("");
  });

  it("a USER cannot process until a vendor AND a delivery date are given, then both are sent exactly as chosen", async () => {
    currentRole = "USER";
    const user = userEvent.setup();
    renderPage();
    await chooseStoreAndFile(user);
    const button = screen.getByTestId("process-button");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Choose the vendor");

    await user.click(await screen.findByRole("button", { name: "T.J. Sheehan" }));
    expect(screen.getByTestId("chosen-vendor")).toHaveTextContent("T.J. Sheehan");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Enter the delivery date");

    await user.type(screen.getByLabelText("Delivery date"), "2026-10-22");
    expect(button).toBeEnabled();
    await user.click(button);

    await waitFor(() => expect(processInvoice).toHaveBeenCalledTimes(1));
    const [, storeId, declared] = processInvoice.mock.calls[0];
    expect(storeId).toBe("pb-wolf");
    expect(declared).toEqual({ vendorId: "v-tj", vendorRequestId: null, deliveryDate: "2026-10-22" });
  });

  it("the vendor list is searched without regard to punctuation", async () => {
    currentRole = "USER";
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("button", { name: "T.J. Sheehan" });
    await user.type(screen.getByLabelText("Vendor"), "tj sheehan");
    expect(screen.getByRole("button", { name: "T.J. Sheehan" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /PepsiCo/ })).not.toBeInTheDocument();
  });

  it("a USER whose vendor is not listed asks for it, and the upload goes against the request", async () => {
    currentRole = "USER";
    vi.mocked(api.requestVendor).mockResolvedValue({
      id: "req-1", requested_name: "Brand New Vendor", status: "PENDING", requested_by: "tester",
      created_at: "2026-11-01T00:00:00Z", decided_by: null, decided_at: null, decision_note: null, vendor_id: null,
      waiting_invoices: 0, waiting: [],
    });
    const user = userEvent.setup();
    renderPage();
    await chooseStoreAndFile(user);
    await user.type(screen.getByLabelText("Vendor"), "Brand New Vendor");
    await user.click(await screen.findByRole("button", { name: /Ask for .Brand New Vendor. to be added/ }));
    await user.click(screen.getByRole("button", { name: "Send request" }));

    expect(await screen.findByTestId("chosen-vendor")).toHaveTextContent(/waiting for an administrator.*finish the invoice now/i);
    expect(api.addVendor).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Delivery date"), "2026-10-22");
    await user.click(screen.getByTestId("process-button"));
    await waitFor(() => expect(processInvoice).toHaveBeenCalledTimes(1));
    expect(processInvoice.mock.calls[0][2]).toEqual({
      vendorId: null, vendorRequestId: "req-1", deliveryDate: "2026-10-22",
    });
  });

  it("a MANAGER asks for a vendor too: only an administrator adds one directly", async () => {
    currentRole = "MANAGER";
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText("Vendor"), "Brand New Vendor");
    expect(await screen.findByRole("button", { name: /Ask for .Brand New Vendor. to be added/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /as a new vendor/ })).not.toBeInTheDocument();
  });

  it("a pending request is offered, labelled, and choosing it uploads against the request", async () => {
    currentRole = "USER";
    const user = userEvent.setup();
    renderPage();
    await chooseStoreAndFile(user);
    const pending = await screen.findByRole("button", { name: /Northwind Brewing/ });
    expect(pending).toHaveTextContent("pending approval");
    await user.click(pending);
    await user.type(screen.getByLabelText("Delivery date"), "2026-10-22");
    await user.click(screen.getByTestId("process-button"));
    await waitFor(() => expect(processInvoice).toHaveBeenCalledTimes(1));
    expect(processInvoice.mock.calls[0][2]).toEqual({ vendorId: null, vendorRequestId: "req-9", deliveryDate: "2026-10-22" });
    expect(api.requestVendor).not.toHaveBeenCalled();
  });

  it("an ADMIN adds the vendor directly and it is chosen at once", async () => {
    currentRole = "ADMIN";
    vi.mocked(api.addVendor).mockResolvedValue({ id: "v-new", name: "Brand New Vendor", kind: "vendor", confirmed: true, last_used_at: null });
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText("Vendor"), "Brand New Vendor");
    await user.click(await screen.findByRole("button", { name: /Add .Brand New Vendor. as a new vendor/ }));
    await user.click(screen.getByRole("button", { name: "Add vendor" }));

    expect(await screen.findByTestId("chosen-vendor")).toHaveTextContent("Brand New Vendor");
    expect(screen.getByTestId("chosen-vendor")).not.toHaveTextContent(/waiting/i);
    expect(api.requestVendor).not.toHaveBeenCalled();
  });

  it("a vendor that is already listed is never offered for adding again", async () => {
    currentRole = "MANAGER";
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("button", { name: "T.J. Sheehan" });
    await user.type(screen.getByLabelText("Vendor"), "TJ SHEEHAN");
    expect(screen.queryByRole("button", { name: /as a new vendor/ })).not.toBeInTheDocument();
  });

  it("an impossible date keeps Process disabled and says so", async () => {
    currentRole = "USER";
    const user = userEvent.setup();
    renderPage();
    await chooseStoreAndFile(user);
    await user.click(await screen.findByRole("button", { name: "T.J. Sheehan" }));
    fireEvent.change(screen.getByLabelText("Delivery date"), { target: { value: "1990-01-01" } });
    expect(screen.getByTestId("process-button")).toBeDisabled();
    expect(screen.getByText("That is not a valid date.")).toBeInTheDocument();
  });

  it("an ADMIN may process without either, and sends neither", async () => {
    currentRole = "ADMIN";
    const user = userEvent.setup();
    renderPage();
    await user.upload(screen.getByTestId("file-input"), [png("p1.jpg", 1)]);
    await user.click(screen.getByTestId("process-button"));
    await waitFor(() => expect(processInvoice).toHaveBeenCalledTimes(1));
    expect(processInvoice.mock.calls[0][2]).toEqual({ vendorId: null, vendorRequestId: null, deliveryDate: null });
  });
});
