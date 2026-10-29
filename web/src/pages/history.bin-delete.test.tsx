import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { HistoryRow } from "@/api/types";

/**
 * Binned documents live in the Recycle Bin now: the Invoices list no longer
 * has a bin view or a delete action on its rows (permanent deletion is an
 * administrator decision recorded in the Recycle Bin). Read-only reference
 * copies are listed only through their own filter.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { username: "a", role: "ADMIN" }, isLoading: false, hasRole: () => true }),
}));
const api = { listInvoices: vi.fn() };
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listInvoices: (...a: unknown[]) => api.listInvoices(...a),
}));

const { HistoryPage } = await import("./history");

const ROW = {
  document_id: "doc-ref", invoice_id: "inv-ref", filename: "again.pdf", store: null, status: "REFERENCE",
  vendor_name: "Acme", invoice_number: "101", invoice_date: null, grand_total: null, currency: null,
  composite_confidence: null, source_type: null, photo_count: 1, review: null, mapping_required: null,
  edi_status: null, completion_status: null, created_at: "2026-10-15T10:00:00Z",
} as unknown as HistoryRow;

function mount(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><HistoryPage /></MemoryRouter></QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listInvoices.mockResolvedValue({ success: true, items: [ROW], total: 1, page: 1, page_size: 15 });
});

describe("HistoryPage — after the Recycle Bin", () => {
  it("offers a Reference copies filter and no bin view", async () => {
    const user = userEvent.setup();
    mount("/invoices");
    await screen.findByTestId("invoice-row");
    await user.click(screen.getAllByRole("combobox")[0]);
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(options).toContain("Reference copies");
    expect(options).not.toContain("Removed (bin)");
  });

  it("never offers a delete action on a row, even to an administrator", async () => {
    mount("/invoices?status=REFERENCE");
    const row = await screen.findByTestId("invoice-row");
    expect(within(row).queryByTestId("delete-document")).toBeNull();
    expect(within(row).getByText("Reference copy")).toBeInTheDocument();
  });
});
