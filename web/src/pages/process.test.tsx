import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProcessPage } from "./process";

vi.mock("@/api/endpoints", () => ({
  processInvoice: vi.fn(),
  listStores: vi.fn(async () => []),
  getDocumentStatus: vi.fn(),
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

beforeEach(() => vi.clearAllMocks());

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
});

