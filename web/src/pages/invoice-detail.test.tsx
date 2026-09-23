import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { InvoiceDetail, LineItem, UserRole } from "@/api/types";
import { AuthProvider } from "@/hooks/use-auth";

import { InvoiceDetailPage } from "./invoice-detail";

/**
 * CORRECTION — the blank Manager/User invoice page.
 *
 * Root cause: `InvoiceDetailData.database` is null for MANAGER/USER (see
 * app.api.v1.invoices._redact_invoice_detail) but the frontend type
 * declared it non-nullable, so DatabaseConfirmationCard was rendered
 * unconditionally and threw on `database.vendor_saved` with no error
 * boundary anywhere in the app — an uncaught render exception unmounts
 * the whole tree, which reads as a blank page. Fixed by (1) correcting
 * the type, (2) making the component null-safe as defense in depth, and
 * (3) actually gating every admin/developer section (Intelligence,
 * Validation, Database persistence, Developer panel, workflow timeline)
 * to ADMIN, splitting the business-relevant half (mapping status, store,
 * EDI readiness) into BusinessStatusPanel so every role gets it.
 *
 * The fixture below mirrors the real RCM acceptance invoice (Red Cliff
 * Petroleum, 623b4a1c-d91d-4023-b702-d4eb8fc2683e): multiple real
 * product lines with UPCs, unmapped, REVIEW_REQUIRED, EDI blocked on
 * mapping — not an empty/mock invoice.
 */

vi.mock("@/api/endpoints", () => ({
  getInvoice: vi.fn(),
  me: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  deleteInvoice: vi.fn(),
  confirmCaseMappings: vi.fn(),
  correctLineItem: vi.fn(),
  correctTotals: vi.fn(),
  correctInvoiceDate: vi.fn(),
  addLineItem: vi.fn(),
  voidLineItem: vi.fn(),
  assignInvoiceStore: vi.fn(),
  listStores: vi.fn(async () => []),
  invoiceExportUrl: vi.fn((invoiceId: string) => `/api/v1/invoices/${invoiceId}/export`),
}));

import * as api from "@/api/endpoints";

function account(role: UserRole) {
  return {
    id: "u1", username: role.toLowerCase(), role, is_active: true,
    created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z",
  };
}

function line(overrides: Partial<LineItem>): LineItem {
  return {
    description: "GM VAN MINI CRE", quantity: 12, unit_price: 1.19, line_total: 14.28,
    tax_rate: null, unit_deposit: null, sort_order: 0, line_type: "product",
    product_code: "00025328", unit_discount: 0, entry_source: "extracted",
    correction_history: [], source_pages: [], duplicate_candidate: null, corrected_fields: [],
    ...overrides,
  };
}

const RCM_LINE_ITEMS: LineItem[] = [
  line({ sort_order: 0, description: "GM VAN MINI CRE", product_code: "00025328", quantity: 12, unit_price: 1.19, line_total: 14.28 }),
  line({ sort_order: 1, description: "GN VANILLA CREM", product_code: "00025294", quantity: 12, unit_price: 1.19, line_total: 14.28 }),
  line({ sort_order: 2, description: "LB REG", product_code: "00049171", quantity: 8, unit_price: 1.95, line_total: 15.60 }),
  line({ sort_order: 3, description: "MT DR SPN", product_code: "00028301", quantity: 5, unit_price: 4.04, line_total: 20.21 }),
];

const CASE_MAPPINGS = RCM_LINE_ITEMS.map((item) => ({
  item_code: item.product_code, description: item.description, pack_size: null,
  units_per_case: null, suggested_units_per_case: null, suggestion_source: null,
  suggestion_candidates: [], reference_description: null, reference_avg_cost: null,
  pending_proposal_id: null, pending_value: null, mapped: false,
}));

function rcmInvoice(): InvoiceDetail {
  return {
    invoice_id: "623b4a1c-d91d-4023-b702-d4eb8fc2683e", document_id: "54a7739f-906f-42da-9876-15dc54144110",
    filename: "WhatsApp Image 2026-09-18 at 20.54.40.jpeg", document_status: "REVIEW_REQUIRED",
    source_type: "ocr",
    store: { id: "store-rcm", label: "RCM", identity_status: "confirmed", display_name: "RCM", address: "1409 E St George Blvd, St George, UT 84790", source_codes: ["86357232"] },
    store_pending: false,
    invoice_number: null, invoice_date: null, due_date: null, currency: "USD",
    subtotal: 536.81, tax_amount: 0, discount_amount: 0, deposit_total: null, fuel_surcharge: null,
    corrected_fields: [], correction_history: [], grand_total: 536.81,
    status: "REVIEW_REQUIRED", composite_confidence: 0.914, extraction_model: "gpt-4o-2024-08-06",
    created_at: "2026-09-22T11:44:00Z",
    pdi_export_allowed: false, pdi_export_requires_confirmation: false,
    pdi_export_blocked_reason: "36 products need a units-per-case mapping before this invoice can be exported.",
    photos: [], duplicate_review_required: false,
    review: { status: "NONE", pending: 0, approved: 0, rejected: 0, proposals: [] },
    case_mappings: CASE_MAPPINGS,
    vendor: { id: "v1", name: "RED CLIFF PETROLEUM", tax_id: null, address: null, phone: null, email: null },
    line_items: RCM_LINE_ITEMS,
    validation_report: {
      decision: "REVIEW_REQUIRED",
      confidence: { composite: 0.914, ocr_confidence: 0.93, ai_confidence: 0.90, validation_score: 0.93, weights: {} },
      review_reasons: [
        "INVOICE_NUMBER_PRESENT: Invoice number was not found on the document.",
        "INVOICE_DATE_VALID: Invoice date was not found on the document.",
        "SUBTOTAL_MATCHES_ITEMS: Sum of line totals does not match the printed subtotal.",
      ],
      checks: [
        { name: "INVOICE_NUMBER_PRESENT", status: "FAILED", message: "Invoice number was not found on the document.", field: "invoice_number" },
        { name: "INVOICE_DATE_VALID", status: "FAILED", message: "Invoice date was not found on the document.", field: "invoice_date" },
        { name: "SUBTOTAL_MATCHES_ITEMS", status: "FAILED", message: "Sum of line totals does not match the printed subtotal.", field: "subtotal", expected: "577.22", actual: "536.81" },
      ],
      summary: { passed: 42, failed: 3, warnings: 1, skipped: 2 },
      validated_at: "2026-09-22T11:44:00Z", duration_ms: 30400,
    },
    llm_metadata: { model: "gpt-4o-2024-08-06", prompt_version: "v11", latency_ms: 2100, input_tokens: 4000, output_tokens: 800, finish_reason: "stop", estimated_cost_usd: 0.02 },
    database: { vendor_saved: true, invoice_saved: true, items_saved: 36, logs_saved: 7, duplicate_check_passed: true, processing_duration_ms: 30400 },
    ocr_text: "INVOICE ...", raw_extraction: { line_items: [] },
  } as unknown as InvoiceDetail;
}

/** MANAGER/USER responses, as the backend's _redact_invoice_detail actually produces them. */
function redactForRole(detail: InvoiceDetail, role: "MANAGER" | "USER"): InvoiceDetail {
  const technical = {
    ocr_text: null, raw_extraction: null, llm_metadata: null,
    validation_report: null, database: null, extraction_model: null, composite_confidence: null,
  };
  if (role === "MANAGER") return { ...detail, ...technical };
  return {
    ...detail, ...technical,
    corrected_fields: [], correction_history: [], photos: [], duplicate_review_required: false,
    review: { status: "NONE", pending: 0, approved: 0, rejected: 0, proposals: [] },
  };
}

function mount(role: UserRole, detail: InvoiceDetail) {
  vi.mocked(api.me).mockResolvedValue(account(role));
  vi.mocked(api.getInvoice).mockResolvedValue(detail);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/invoices/${detail.invoice_id}`]}>
        <AuthProvider>
          <Routes>
            <Route path="/invoices/:invoiceId" element={<InvoiceDetailPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("InvoiceDetailPage — ADMIN", () => {
  it("renders the full invoice detail, unchanged, including developer/technical sections", async () => {
    mount("ADMIN", rcmInvoice());

    await waitFor(() => expect(screen.getByTestId("financial-summary")).toBeInTheDocument());
    expect(screen.getAllByTestId("line-item-row")).toHaveLength(4);
    expect(screen.getAllByText("GM VAN MINI CRE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("00025328").length).toBeGreaterThan(0);

    expect(screen.getByTestId("intelligence-panel")).toBeInTheDocument();
    expect(screen.getByText("Validation report")).toBeInTheDocument();
    expect(screen.getByText("Database persistence")).toBeInTheDocument();
    expect(screen.getByText("Developer panel")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-timeline")).toBeInTheDocument();
    expect(screen.getByTestId("business-status-panel")).toBeInTheDocument();
  });
});

describe("InvoiceDetailPage — MANAGER", () => {
  it("renders a functional business detail page — no blank screen — with no developer/technical sections", async () => {
    mount("MANAGER", redactForRole(rcmInvoice(), "MANAGER"));

    // The bug: this used to crash before any of these assertions could run.
    await waitFor(() => expect(screen.getByTestId("financial-summary")).toBeInTheDocument());

    // Header / totals / line items — the business data must be visible.
    expect(screen.getByText(/RED CLIFF PETROLEUM/)).toBeInTheDocument();
    expect(screen.getAllByText(/536\.81/).length).toBeGreaterThan(0);
    expect(screen.getAllByTestId("line-item-row")).toHaveLength(4);
    expect(screen.getAllByText("GM VAN MINI CRE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("00025328").length).toBeGreaterThan(0);
    expect(screen.getAllByText("needs mapping").length).toBeGreaterThan(0);

    // Store + EDI readiness (BusinessStatusPanel).
    expect(screen.getByTestId("business-status-panel")).toBeInTheDocument();
    expect(screen.getByTestId("business-status-panel")).toHaveTextContent("RCM");
    expect(screen.getByTestId("business-status-panel")).toHaveTextContent("Blocked");

    // Absent: everything developer/technical.
    expect(screen.queryByTestId("intelligence-panel")).toBeNull();
    expect(screen.queryByText("Validation report")).toBeNull();
    expect(screen.queryByText("Database persistence")).toBeNull();
    expect(screen.queryByText("Developer panel")).toBeNull();
    expect(screen.queryByTestId("workflow-timeline")).toBeNull();
  });
});

describe("InvoiceDetailPage — USER", () => {
  it("renders a functional operational detail page — no blank screen — with no developer/technical sections and no approve/reject actions", async () => {
    mount("USER", redactForRole(rcmInvoice(), "USER"));

    await waitFor(() => expect(screen.getByTestId("financial-summary")).toBeInTheDocument());

    expect(screen.getByText(/RED CLIFF PETROLEUM/)).toBeInTheDocument();
    expect(screen.getAllByTestId("line-item-row")).toHaveLength(4);
    expect(screen.getAllByText("GM VAN MINI CRE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("00025328").length).toBeGreaterThan(0);
    expect(screen.getAllByText("needs mapping").length).toBeGreaterThan(0);

    expect(screen.getByTestId("business-status-panel")).toBeInTheDocument();
    expect(screen.getByTestId("business-status-panel")).toHaveTextContent("RCM");
    expect(screen.getByTestId("business-status-panel")).toHaveTextContent("Blocked");

    expect(screen.queryByTestId("intelligence-panel")).toBeNull();
    expect(screen.queryByText("Validation report")).toBeNull();
    expect(screen.queryByText("Database persistence")).toBeNull();
    expect(screen.queryByText("Developer panel")).toBeNull();
    expect(screen.queryByTestId("workflow-timeline")).toBeNull();

    // No approve/reject or manager-only review surface on this page.
    expect(screen.queryByTestId("invoice-review")).toBeNull();
    expect(screen.queryByRole("button", { name: /approve/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /reject/i })).toBeNull();
    // No manager-only correction affordances either.
    expect(screen.queryByTestId("add-row-toggle")).toBeNull();
  });
});
