import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { DashboardData, HistoryRow } from "@/api/types";
import { AppShell } from "@/components/layout/app-shell";
import { MetricStrip } from "@/components/shared/metric-strip";
import { AuthProvider } from "@/hooks/use-auth";

import { DashboardPage } from "./dashboard";
import { SettingsPage } from "./settings";

/**
 * The dashboard is an operator's command center: every number on it is a
 * queue a person can open. The pipeline's own telemetry — processing
 * time, tokens, model cost — is real and kept, but it lives on Settings.
 */

vi.mock("@/api/endpoints", () => ({
  getDashboardSummary: vi.fn(),
  listProposals: vi.fn(async () => ({ success: true, items: [], total: 3, page: 1, page_size: 1, request_id: null })),
  getMappingQueueSummary: vi.fn(async () => ({ unique_products: 0, invoice_occurrences: 0, stores: 0 })),
  listStores: vi.fn(async () => []),
  // The shell reads the signed-in account for its role-gated rail.
  me: vi.fn(async () => ({
    id: "u1", username: "admin", role: "ADMIN", is_active: true,
    created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
  })),
  login: vi.fn(),
  logout: vi.fn(),
}));
vi.mock("@/api/client", () => ({
  apiClient: { get: vi.fn(async () => ({ data: {} })) },
  ApiError: class extends Error {},
}));

import * as api from "@/api/endpoints";

const RECENT: HistoryRow = {
  document_id: "d1", invoice_id: "i1", filename: "IMG_1.jpg", store: null, status: "REVIEW_REQUIRED",
  vendor_name: "ONONDAGA BEVERAGE", invoice_number: "1012818", invoice_date: "2026-09-17", grand_total: 1216.3,
  currency: "USD", composite_confidence: 0.96, source_type: "ocr", photo_count: 1, review: null,
  mapping_required: null, edi_status: null, created_at: "2026-09-18T00:06:00Z",
};

const DATA: DashboardData = {
  total_documents: 8, completed: 4, review_required: 4, failed: 0, in_progress: 0, success_rate: 0.5,
  average_confidence: 0.974, average_processing_ms: 948_000, total_tokens: 62_662, total_estimated_cost_usd: 0.1958,
  status_breakdown: { COMPLETED: 4, REVIEW_REQUIRED: 4 }, recent: [RECENT],
};

function mount(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/dashboard"]}>
        <AuthProvider>{ui}</AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DashboardPage", () => {
  it("states the operation from real counts and shows no processing-time, token or cost telemetry", async () => {
    vi.mocked(api.getDashboardSummary).mockResolvedValue(DATA);
    mount(<DashboardPage />);

    await waitFor(() => expect(screen.getByTestId("operational-statement")).toHaveTextContent("4 invoices need review"));
    expect(screen.getByTestId("operational-statement")).toHaveTextContent("3 master-data decisions pending");

    // The queue: every figure opens the list it counts.
    const queue = screen.getByTestId("queue");
    const rows = screen.getAllByTestId("queue-row");
    expect(rows.map((r) => r.getAttribute("href"))).toEqual([
      "/invoices?status=REVIEW_REQUIRED", "/stores", "/data-review", "/invoices?status=FAILED",
    ]);
    expect(queue).toHaveTextContent("Need review");
    expect(queue).toHaveTextContent("Master-data decisions");
    expect(screen.getByTestId("throughput")).toHaveTextContent("8 invoices processed · 4 validated (50%)");
    expect(screen.getByTestId("process-rail")).toHaveTextContent("Master data");
    expect(screen.getByTestId("clock")).toHaveTextContent(/^\d{2}:\d{2}:\d{2}$/);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/^Good (Morning|Afternoon|Evening)$/);

    const page = document.body.textContent ?? "";
    expect(page).not.toMatch(/15\.8 min|min per invoice|tokens?\b|\$0\.19|AI cost|processing time/i);
  });

  it("reads as a real empty state, not a placeholder, when nothing has been processed", async () => {
    vi.mocked(api.getDashboardSummary).mockResolvedValue({ ...DATA, total_documents: 0, completed: 0, review_required: 0, status_breakdown: {}, recent: [] });
    vi.mocked(api.listProposals).mockResolvedValue({ success: true, items: [], total: 0, page: 1, page_size: 1, request_id: null });
    mount(<DashboardPage />);
    await waitFor(() => expect(screen.getByTestId("operational-statement")).toHaveTextContent("No invoices have been processed yet."));
    expect(screen.getByText("No invoices yet")).toBeInTheDocument();
  });
});

describe("SettingsPage diagnostics", () => {
  it("is where the telemetry lives, from the same dashboard endpoint", async () => {
    vi.mocked(api.getDashboardSummary).mockResolvedValue(DATA);
    mount(<SettingsPage />);
    const diagnostics = await screen.findByTestId("diagnostics");
    await waitFor(() => expect(diagnostics).toHaveTextContent("15.8 min"));
    expect(diagnostics).toHaveTextContent("62.7k");
    expect(diagnostics).toHaveTextContent("$0.1958");
  });
});

describe("MetricStrip", () => {
  it("renders one button per clickable metric and plain cells otherwise", () => {
    const open = vi.fn();
    render(<MetricStrip items={[
      { key: "a", label: "Needs review", value: 4, tone: "warning", hint: "flagged", onClick: open },
      { key: "b", label: "Validated", value: 4 },
    ]} />);
    expect(screen.getAllByTestId("metric")).toHaveLength(2);
    screen.getByRole("button", { name: /Needs review/ }).click();
    expect(open).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /Validated/ })).toBeNull();
  });
});

describe("AppShell branding", () => {
  it("carries the supplied mark and the product name in the rail", async () => {
    vi.mocked(api.getDashboardSummary).mockResolvedValue(DATA);
    mount(<AppShell />);
    const mark = await screen.findByAltText("Invoice Intelligence");
    expect(mark).toHaveAttribute("src", "/brand/mark-512.png");
    expect(screen.getByTestId("sidebar")).toHaveTextContent("Enterprise AP platform");
  });
});
