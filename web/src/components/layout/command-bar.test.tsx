import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommandBar } from "./command-bar";

vi.mock("@/api/endpoints", () => ({ listInvoices: vi.fn(), listStores: vi.fn() }));
import * as api from "@/api/endpoints";

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname}</output>;
}

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard"]}>
        {node}
        <Routes><Route path="*" element={<Where />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listStores).mockResolvedValue([]);
  vi.mocked(api.listInvoices).mockResolvedValue({ success: true, request_id: null, items: [], total: 0, page: 1, page_size: 6 });
});

describe("command bar", () => {
  it("lists navigation actions and opens one with the keyboard", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    wrap(<CommandBar open onOpenChange={onOpenChange} />);
    expect(screen.getByRole("option", { name: /Master Data Review/ })).toBeInTheDocument();
    await user.type(screen.getByRole("combobox"), "stores");
    await waitFor(() => expect(screen.queryByRole("option", { name: /Dashboard/ })).not.toBeInTheDocument());
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/stores"));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("searches invoices through the existing list endpoint once two characters are typed", async () => {
    const user = userEvent.setup();
    vi.mocked(api.listInvoices).mockResolvedValue({
      success: true, request_id: null, page: 1, page_size: 6, total: 1,
      items: [{
        document_id: "d1", invoice_id: "i1", filename: "a.jpg", store: null, status: "COMPLETED", vendor_name: "ONONDAGA",
        invoice_number: "1012818", invoice_date: null, grand_total: 1216.3, currency: "USD", composite_confidence: 0.95,
        source_type: "ocr", photo_count: 1, review: null, created_at: "2026-09-18T00:00:00Z",
      }],
    });
    wrap(<CommandBar open onOpenChange={() => {}} />);
    await user.type(screen.getByRole("combobox"), "1012");
    await waitFor(() => expect(api.listInvoices).toHaveBeenCalledWith({ search: "1012", page_size: 6 }));
    const hit = await screen.findByRole("option", { name: /#1012818/ });
    expect(hit).toHaveTextContent("ONONDAGA");
    await user.click(hit);
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/invoices/i1"));
  });
});
