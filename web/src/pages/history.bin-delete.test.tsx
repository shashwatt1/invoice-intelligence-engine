import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { HistoryRow } from "@/api/types";

/**
 * The "Removed (bin)" view: an admin can permanently delete an invoice-less run
 * straight from its row (confirming first, without opening the row); nobody
 * else sees the action, and it never appears outside the bin view.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
const role = { current: "ADMIN" as "USER" | "MANAGER" | "ADMIN" };
const RANK = { USER: 0, MANAGER: 1, ADMIN: 2 } as const;
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: { username: "u", role: role.current }, isLoading: false,
    hasRole: (minimum: keyof typeof RANK) => RANK[role.current] >= RANK[minimum],
  }),
}));
const api = { listInvoices: vi.fn(), deleteDocument: vi.fn() };
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listInvoices: (...a: unknown[]) => api.listInvoices(...a),
  deleteDocument: (...a: unknown[]) => api.deleteDocument(...a),
}));

const { HistoryPage } = await import("./history");

const ROW: HistoryRow = {
  document_id: "doc-bin", invoice_id: null, filename: "WhatsApp Image 2026-10-14.jpeg", store: null,
  status: "BINNED", vendor_name: null, invoice_number: null, invoice_date: null, grand_total: null,
  currency: null, composite_confidence: null, source_type: null, photo_count: 2, review: null,
  mapping_required: null, edi_status: null, completion_status: null, created_at: "2026-10-15T10:00:00Z",
} as HistoryRow;

function Where() {
  return <output data-testid="where">{useLocation().pathname}</output>;
}

function mount(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/invoices" element={<><HistoryPage /><Where /></>} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  role.current = "ADMIN";
  api.listInvoices.mockResolvedValue({ success: true, items: [ROW], total: 1, page: 1, page_size: 20 });
});

describe("HistoryPage — deleting from the bin", () => {
  it("lets an admin delete a binned run from its row, after confirming, without opening it", async () => {
    const user = userEvent.setup();
    api.deleteDocument.mockResolvedValue(undefined);
    mount("/invoices?status=BINNED");
    const row = await screen.findByTestId("invoice-row");
    await user.click(within(row).getByTestId("delete-document"));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("there is no undo");
    expect(screen.getByTestId("where")).toHaveTextContent("/invoices");
    await user.click(screen.getByRole("button", { name: "Delete permanently" }));
    await waitFor(() => expect(api.deleteDocument).toHaveBeenCalledWith("doc-bin"));
    expect(screen.getByTestId("where")).toHaveTextContent("/invoices");
    await waitFor(() => expect(api.listInvoices.mock.calls.length).toBeGreaterThan(1)); // the list is re-read
  });

  it("cancelling changes nothing and does not open the row", async () => {
    const user = userEvent.setup();
    mount("/invoices?status=BINNED");
    const row = await screen.findByTestId("invoice-row");
    await user.click(within(row).getByTestId("delete-document"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.deleteDocument).not.toHaveBeenCalled();
    expect(screen.getByTestId("where")).toHaveTextContent(/^\/invoices$/);
  });

  it("is not shown to a manager", async () => {
    role.current = "MANAGER";
    mount("/invoices?status=BINNED");
    await screen.findByTestId("invoice-row");
    expect(screen.queryByTestId("delete-document")).toBeNull();
  });

  it("is not shown outside the bin view", async () => {
    api.listInvoices.mockResolvedValue({ success: true, items: [{ ...ROW, status: "FAILED" }], total: 1, page: 1, page_size: 20 });
    mount("/invoices?status=FAILED");
    await screen.findByTestId("invoice-row");
    expect(screen.queryByTestId("delete-document")).toBeNull();
  });
});
