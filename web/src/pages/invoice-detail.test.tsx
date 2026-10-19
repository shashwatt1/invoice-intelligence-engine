import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

describe("InvoiceDetailPage — product nomenclature", () => {
  it("shows the Product Master name and keeps the printed wording as evidence", async () => {
    const detail = rcmInvoice();
    detail.line_items = [
      line({ sort_order: 0, description: "Bud Lt 24pk", normalized_description: "BUD LIGHT 24/12 CAN",
             normalized_description_source: "PRODUCT_MASTER_CANONICAL_DESCRIPTION" }),
      line({ sort_order: 1, description: "LB REG", normalized_description: "LB REG",
             normalized_description_source: "INVOICE_DESCRIPTION" }),
    ];
    mount("ADMIN", detail);

    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(2));
    const [mastered, plain] = screen.getAllByTestId("line-item-row");
    expect(mastered).toHaveTextContent("BUD LIGHT 24/12 CAN");
    expect(mastered.querySelector("[data-testid=source-description]")).toHaveTextContent(
      "Product Master name · as printed: Bud Lt 24pk");
    expect(plain).toHaveTextContent("LB REG");
    expect(plain.querySelector("[data-testid=source-description]")).toBeNull();
  });

  it("falls back to the printed wording when the backend sends no normalized name", async () => {
    mount("ADMIN", rcmInvoice());
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    expect(screen.queryByTestId("source-description")).toBeNull();
    expect(screen.getAllByText("GM VAN MINI CRE").length).toBeGreaterThan(0);
  });

  it("says when units per case came from the global mapping, with its provenance, and flags a disagreeing override", async () => {
    const detail = rcmInvoice();
    detail.case_mappings = [
      { ...CASE_MAPPINGS[0], mapped: true, units_per_case: 12, resolution_path: "PRODUCT_MASTER_GLOBAL",
        resolution_scope: "Global (distributor evidence, held under Item Sales · 47708760)",
        resolution_mapping_id: "m1", resolution_notes: [] },
      { ...CASE_MAPPINGS[1], resolution_path: "CONFLICT", resolution_scope: null, resolution_mapping_id: null,
        resolution_notes: ["physical-store override (UNIT_IS_SELLING_UNIT x24) and global mapping (UNIT_IS_SELLING_UNIT x12) disagree"] },
    ] as InvoiceDetail["case_mappings"];
    mount("ADMIN", detail);
    const notes = await screen.findAllByTestId("resolution-note");
    expect(notes.map((n) => n.textContent)).toEqual(expect.arrayContaining([
      "Product Master · Global",
      "Store override and global mapping disagree — needs a decision",
    ]));
    const global = notes.find((n) => n.textContent === "Product Master · Global")!;
    expect(global).toHaveAttribute("title", "Global (distributor evidence, held under Item Sales · 47708760)");
    expect(notes.map((n) => n.textContent).join(" ")).not.toMatch(/vendor|source identity/i);
  });
});

describe("InvoiceDetailPage — awaiting master data approval", () => {
  it("links straight to this invoice's proposals, filtered to its store", async () => {
    const detail = rcmInvoice();
    detail.review = { status: "PENDING", pending: 15, approved: 0, rejected: 0, proposals: [] } as InvoiceDetail["review"];
    mount("ADMIN", detail);
    const link = await screen.findByTestId("awaiting-master-data-approval");
    expect(link).toHaveTextContent("Awaiting Master Data Approval (15)");
    expect(link).toHaveAttribute("href", `/data-review?invoice=${detail.invoice_id}&store=store-rcm`);
  });
});


describe("InvoiceDetailPage — editing the product name on a line", () => {
  it("lets a manager correct the printed name through the line-correction path", async () => {
    const user = userEvent.setup();
    vi.mocked(api.correctLineItem).mockResolvedValue({
      item: { sort_order: 2, description: "LB REGULAR", quantity: 8, unit_price: 1.95, line_total: 15.6, unit_deposit: null, corrected_fields: ["description"] },
      status: "REVIEW_REQUIRED", composite_confidence: 0.9, failed_checks: 3, review_reasons: [],
      pdi_export_allowed: false, pdi_export_blocked_reason: null,
    } as never);
    mount("MANAGER", redactForRole(rcmInvoice(), "MANAGER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    const row = screen.getAllByTestId("line-item-row")[2];
    await user.click(within(row).getByTestId("edit-description"));
    const input = within(row).getByRole("textbox", { name: /Product name printed on this line/ });
    expect(input).toHaveValue("LB REG");
    expect(within(row).getByTestId("description-edit")).toHaveTextContent("its PDI file");
    await user.clear(input);
    await user.type(input, "LB REGULAR{Enter}");
    await waitFor(() => expect(api.correctLineItem).toHaveBeenCalledWith(
      "623b4a1c-d91d-4023-b702-d4eb8fc2683e", 2, { description: "LB REGULAR", corrected_by: null }));
  });

  it("does not save an empty or unchanged name", async () => {
    const user = userEvent.setup();
    vi.mocked(api.correctLineItem).mockClear();
    mount("ADMIN", rcmInvoice());
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    const row = screen.getAllByTestId("line-item-row")[0];
    await user.click(within(row).getByTestId("edit-description"));
    expect(within(row).getByRole("button", { name: "Save" })).toBeDisabled();
    await user.clear(within(row).getByRole("textbox"));
    expect(within(row).getByRole("alert")).toHaveTextContent("The name cannot be empty.");
    expect(api.correctLineItem).not.toHaveBeenCalled();
  });

  it("offers no name editing to a USER", async () => {
    mount("USER", redactForRole(rcmInvoice(), "USER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    expect(screen.queryByTestId("edit-description")).toBeNull();
  });
});


/**
 * Invoice #501315 (T.J. Sheehan → PB Wolf, 38 rows) as the shape for row
 * numbers and check links. Rows 31–38 carry the printed values; earlier rows
 * are fillers. The check fields follow the backend's line_items[<sort_order>]
 * convention — they are not captured from the live invoice, and nothing here
 * corrects or reinterprets that invoice's figures.
 */
function invoice501315(): InvoiceDetail {
  const printed: Partial<LineItem>[] = [
    { description: "BUD LIGHT 24/16 CAN 3/8", quantity: 1, unit_price: 27.6, unit_discount: 2, unit_deposit: 1.2, line_total: 28.8 },
    { description: "BUSCH 24/12 CAN 2/12", quantity: 1, unit_price: 19.2, unit_discount: 2.55, unit_deposit: 1.2, line_total: 40.8 },
    { description: "BUDWEISER 24/12 CAN 2/12", quantity: 1, unit_price: 24.35, unit_discount: 0, unit_deposit: 1.2, line_total: 25.55 },
    { description: "BUD LIGHT 18/12 CAN", quantity: 1, unit_price: 16.85, unit_discount: 0, unit_deposit: 0.9, line_total: 17.75 },
    { description: "BUDWEISER 18/12 CAN", quantity: 1, unit_price: 16.85, unit_discount: 0, unit_deposit: 0.9, line_total: 35.5 },
    { description: "SOUTHERN COMFORT PB-12 355ML", quantity: 0, unit_price: 67.12, line_total: 0 },
    { description: "FIREBALL PB-8/6 100ML", quantity: 0, unit_price: 66.86, unit_deposit: 2.4, line_total: 0 },
    { description: "MISCELLANEOUS DELIVERY CHARGE", quantity: 1, unit_price: 5, line_total: 5, line_type: "charge", product_code: null },
  ];
  const detail = rcmInvoice();
  detail.invoice_number = "501315";
  detail.subtotal = 1619.89;
  detail.grand_total = 1624.89;
  detail.case_mappings = [];
  detail.line_items = Array.from({ length: 38 }, (_, sort_order) =>
    line({ sort_order, description: `SHEEHAN ROW ${sort_order + 1}`, ...(sort_order >= 30 ? printed[sort_order - 30] : {}) }));
  detail.validation_report = {
    ...detail.validation_report!,
    review_reasons: ["LINE_ITEM_MATH: quantity × unit_price does not match the printed line total."],
    checks: [
      { name: "LINE_ITEM_UNRECONCILED", status: "WARNING", field: "line_items[31].line_total", expected: "19.20", actual: "40.80", message: "no discount or deposit explains the difference" },
      { name: "LINE_ITEM_UNRECONCILED", status: "WARNING", field: "line_items[34].line_total", expected: "16.85", actual: "35.50", message: "no discount or deposit explains the difference" },
      { name: "SUBTOTAL_UNRECONCILED", status: "WARNING", field: "subtotal", expected: "1619.89", actual: "1294.56", message: "Line totals do not sum to the printed subtotal." },
      { name: "LINE_ITEM_MATH", status: "FAILED", field: "line_items[31].line_total", expected: "19.20", actual: "40.80", message: "quantity × unit_price does not match the printed line total." },
      { name: "LINE_ITEM_MATH", status: "FAILED", field: "line_items[34].line_total", expected: "16.85", actual: "35.50", message: "quantity × unit_price does not match the printed line total." },
      { name: "SUBTOTAL_MATCHES_ITEMS", status: "FAILED", field: "subtotal", expected: "1294.56", actual: "1619.89", message: "Sum of line totals does not match the printed subtotal." },
    ],
    summary: { passed: 74, failed: 3, warnings: 3, skipped: 3 },
  };
  return detail;
}

function rowFor(sortOrder: number): HTMLElement {
  const row = document.querySelector<HTMLElement>(`[data-testid="line-item-row"][data-sort-order="${sortOrder}"]`);
  if (!row) throw new Error(`no row for sort_order ${sortOrder}`);
  return row;
}

describe("InvoiceDetailPage — row numbers and check links (Invoice #501315 shape)", () => {
  it("numbers all 38 rows 1..38 in invoice order, the charge row included", async () => {
    mount("ADMIN", invoice501315());
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(38));
    expect(screen.getAllByTestId("row-serial").map((c) => c.textContent))
      .toEqual(Array.from({ length: 38 }, (_, i) => String(i + 1)));
    expect(within(rowFor(31)).getByTestId("row-serial")).toHaveTextContent("32");
    expect(rowFor(31)).toHaveTextContent("BUSCH 24/12 CAN 2/12");
    expect(within(rowFor(37)).getByTestId("row-serial")).toHaveTextContent("38");
    expect(rowFor(37)).toHaveTextContent("charge — not a product");
    expect(screen.getByText(/38 rows · 2 with issues/)).toBeInTheDocument();
  });

  it("scrolls to, focuses and highlights the row a line check names", async () => {
    const user = userEvent.setup();
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    mount("ADMIN", invoice501315());
    const lineChecks = await screen.findByTestId("line-checks");
    await user.click(within(lineChecks).getAllByRole("button", { name: "Go to row 32" })[0]);

    const target = rowFor(31);
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.contexts[0]).toBe(target);
    expect(scroll).toHaveBeenCalledWith({ block: "center", behavior: "smooth" });
    expect(target).toHaveAttribute("data-highlighted", "true");
    expect(document.activeElement).toBe(target);
    expect(rowFor(34)).not.toHaveAttribute("data-highlighted");
    scroll.mockRestore();
  });

  it("jumps without animation when the user prefers reduced motion", async () => {
    const user = userEvent.setup();
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({ ...original(query), matches: query.includes("reduce") })) as typeof window.matchMedia;
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    try {
      mount("ADMIN", invoice501315());
      const lineChecks = await screen.findByTestId("line-checks");
      await user.click(within(lineChecks).getAllByRole("button", { name: "Go to row 35" })[0]);
      expect(scroll).toHaveBeenCalledWith({ block: "center", behavior: "auto" });
      expect(scroll.mock.contexts[0]).toBe(rowFor(34));
    } finally {
      scroll.mockRestore();
      window.matchMedia = original;
    }
  });

  it("clears the highlight after a moment", async () => {
    const user = userEvent.setup();
    mount("ADMIN", invoice501315());
    const lineChecks = await screen.findByTestId("line-checks");
    await user.click(within(lineChecks).getAllByRole("button", { name: "Go to row 35" })[0]);
    expect(rowFor(34)).toHaveAttribute("data-highlighted", "true");
    await waitFor(() => expect(rowFor(34)).not.toHaveAttribute("data-highlighted"), { timeout: 4000 });
  });

  it("keeps invoice-level checks unlinked and links the Intelligence panel's line failures", async () => {
    mount("ADMIN", invoice501315());
    const invoiceChecks = await screen.findByTestId("invoice-checks");
    expect(invoiceChecks).toHaveTextContent("Subtotal Unreconciled");
    expect(invoiceChecks).toHaveTextContent("Subtotal Matches Items");
    expect(within(invoiceChecks).queryByRole("button")).toBeNull();

    const panel = screen.getByTestId("intelligence-panel");
    expect(within(panel).getAllByTestId("check-row-link").map((b) => b.textContent)).toEqual(["Row 32", "Row 35"]);
    expect(panel).toHaveTextContent("SUBTOTAL_MATCHES_ITEMS");
  });

  it("numbers charge, voided, duplicate and manual rows from sort_order, not screen position, and names a duplicate's row by serial", async () => {
    const detail = rcmInvoice();
    detail.line_items = [
      line({ sort_order: 4, description: "ADDED BY HAND", entry_source: "manual" }),
      line({ sort_order: 0, description: "FIRST PRINTED" }),
      line({ sort_order: 3, description: "SEEN AGAIN", duplicate_candidate: { of_sort_order: 1, reason: "same UPC and total", resolution: null } }),
      line({ sort_order: 1, description: "SECOND PRINTED" }),
      line({ sort_order: 2, description: "REMOVED", line_type: "voided" }),
      line({ sort_order: 5, description: "DELIVERY", line_type: "charge", product_code: null }),
      line({ sort_order: 6, description: "STALE FLAG", duplicate_candidate: { of_sort_order: 99, reason: null, resolution: null } }),
    ];
    mount("ADMIN", detail);
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(7));
    expect(screen.getAllByTestId("row-serial").map((c) => c.textContent)).toEqual(["5", "1", "4", "2", "3", "6", "7"]);
    expect(within(rowFor(3)).getByTestId("duplicate-of")).toHaveTextContent("possible duplicate of row 2");
    expect(within(rowFor(6)).getByTestId("duplicate-of")).toHaveTextContent("possible duplicate of another row");
  });

  it("shows row numbers to a MANAGER, labels the name editor by row number, and offers no check links", async () => {
    mount("MANAGER", redactForRole(invoice501315(), "MANAGER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(38));
    expect(within(rowFor(34)).getByTestId("row-serial")).toHaveTextContent("35");
    expect(within(rowFor(34)).getByRole("button", { name: "Edit product name for row 35" })).toBeInTheDocument();
    expect(screen.queryByTestId("check-row-link")).toBeNull();
  });
});
