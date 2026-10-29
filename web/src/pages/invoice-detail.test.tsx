import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
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

describe("InvoiceDetailPage — financial summary deposits", () => {
  it("labels the line-derived deposit sums as line deposits", async () => {
    mount("ADMIN", rcmInvoice());
    await waitFor(() => expect(screen.getByTestId("financial-summary")).toBeInTheDocument());
    const summary = screen.getByTestId("financial-summary");
    expect(within(summary).getByText("Σ line deposits (deposit × qty)")).toBeInTheDocument();
    expect(within(summary).getByText("merchandise + line deposits + delivery")).toBeInTheDocument();
    expect(within(summary).queryByText("Σ deposit × qty")).toBeNull();
  });

  it("shows a deposit printed only at invoice level on its own row, without changing the line-derived sums", async () => {
    const detail = rcmInvoice();
    detail.deposit_total = 23.3;                                    // printed per package group, not per line
    mount("ADMIN", detail);
    await waitFor(() => expect(screen.getByTestId("financial-summary")).toBeInTheDocument());
    const row = screen.getByTestId("invoice-level-deposit");
    expect(row).toHaveTextContent("Deposit printed at invoice level");
    expect(row).toHaveTextContent("(not attributed to lines)");
    expect(row).not.toHaveTextContent("differs from Σ line deposits");
    expect(row).toHaveTextContent("23.30");
    const sum = within(screen.getByTestId("financial-summary")).getByText("merchandise + line deposits + delivery").closest("div")!;
    expect(sum).toHaveTextContent("64.36");                         // Σ price × qty of the four lines, no deposit folded in
  });

  it("says only that the printed deposit total differs when the lines carry deposits of their own", async () => {
    const detail = rcmInvoice();
    detail.line_items = detail.line_items.map((i) => ({ ...i, unit_deposit: 0.05 }));   // Σ line deposits 1.85
    detail.deposit_total = 2.25;                                                         // printed total disagrees
    mount("ADMIN", detail);
    await waitFor(() => expect(screen.getByTestId("financial-summary")).toBeInTheDocument());
    const row = screen.getByTestId("invoice-level-deposit");
    expect(row).toHaveTextContent("Deposit total printed on the invoice");
    expect(row).toHaveTextContent("(differs from Σ line deposits)");
    expect(row).not.toHaveTextContent("not attributed to lines");
    expect(row).toHaveTextContent("2.25");
    const summary = screen.getByTestId("financial-summary");
    const lineDeposits = within(summary).getByText("Σ line deposits (deposit × qty)").closest("div")!;
    expect(lineDeposits).toHaveTextContent("1.85");                                      // the line-derived figures are unchanged
    const sum = within(summary).getByText("merchandise + line deposits + delivery").closest("div")!;
    expect(sum).toHaveTextContent("66.21");                                              // 64.36 + 1.85, not the printed 2.25
  });

  it("adds no invoice-level row when the printed deposit equals the line deposits, or none is printed", async () => {
    const detail = rcmInvoice();
    detail.line_items = detail.line_items.map((i) => ({ ...i, unit_deposit: 0.05 }));
    detail.deposit_total = 0.05 * (12 + 12 + 8 + 5);                // 1.85: fully attributed to lines
    mount("ADMIN", detail);
    await waitFor(() => expect(screen.getByTestId("financial-summary")).toBeInTheDocument());
    expect(screen.queryByTestId("invoice-level-deposit")).toBeNull();
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
 * PepsiCo 2026-10-22: a dashed UPC wrapped onto the next print line was read only
 * up to the break. The backend marks the row (product_code_cut_off) and blocks the
 * export; this cell is the audited way through — the existing line correction.
 */
describe("InvoiceDetailPage — correcting a cut-off UPC", () => {
  const CUT_OFF = "stops partway through a dashed UPC (1-5-5-1) layout";
  function pepsiInvoice(): InvoiceDetail {
    const detail = rcmInvoice();
    return {
      ...detail,
      pdi_export_blocked_reason: "1 product has a printed UPC that is cut off (MT DEW 20OZ).",
      line_items: [
        line({ sort_order: 0, description: "PEPSI 20OZ", product_code: "0-12000-00113-0" }),
        line({ sort_order: 1, description: "MT DEW 20OZ", product_code: "0-12000-1263", product_code_cut_off: CUT_OFF }),
      ],
    };
  }

  it("marks the raw printed value and lets a manager enter the full UPC through the line correction", async () => {
    const user = userEvent.setup();
    vi.mocked(api.correctLineItem).mockClear();
    vi.mocked(api.correctLineItem).mockResolvedValue({
      item: { sort_order: 1, description: "MT DEW 20OZ", product_code: "0-12000-12630-0", quantity: 12, unit_price: 1.19,
              line_total: 14.28, unit_deposit: null, corrected_fields: ["product_code"] },
      status: "REVIEW_REQUIRED", composite_confidence: 0.9, failed_checks: 1, review_reasons: [],
      pdi_export_allowed: false, pdi_export_blocked_reason: null,
    } as never);
    mount("MANAGER", redactForRole(pepsiInvoice(), "MANAGER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(2));
    const [complete, cutOff] = screen.getAllByTestId("line-item-row");

    expect(within(complete).queryByTestId("edit-upc")).toBeNull();
    expect(within(complete).queryByTestId("upc-cut-off")).toBeNull();
    expect(complete).toHaveTextContent("0-12000-00113-0");

    expect(within(cutOff).getByTestId("upc-cut-off")).toHaveTextContent("0-12000-1263");
    await user.click(within(cutOff).getByRole("button", { name: "Enter the full UPC for row 2: 0-12000-1263" }));
    const input = within(cutOff).getByRole("textbox", { name: "Full UPC printed on this line (row 2)" });
    expect(input).toHaveValue("0-12000-1263");
    // Saving the cut-off value unchanged is refused; nothing is completed for the reviewer.
    expect(within(cutOff).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(within(cutOff).getByTestId("upc-edit")).toHaveTextContent("stays in the line's history");
    await user.clear(input);
    expect(within(cutOff).getByRole("alert")).toHaveTextContent("Enter the full UPC.");
    await user.type(input, " 0-12000-12630-0 {Enter}");
    await waitFor(() => expect(api.correctLineItem).toHaveBeenCalledWith(
      "623b4a1c-d91d-4023-b702-d4eb8fc2683e", 1, { product_code: "0-12000-12630-0", corrected_by: null }));
    expect(api.correctLineItem).toHaveBeenCalledTimes(1);
  });

  it("shows a USER the cut-off value but no way to change it", async () => {
    mount("USER", redactForRole(pepsiInvoice(), "USER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(2));
    expect(screen.getByTestId("upc-cut-off")).toHaveTextContent("0-12000-1263");
    expect(screen.queryByTestId("edit-upc")).toBeNull();
  });

  it("marks a UPC corrected by hand and offers no edit once it is no longer cut off", async () => {
    const detail = pepsiInvoice();
    detail.line_items[1] = { ...detail.line_items[1], product_code: "0-12000-12630-0", product_code_cut_off: null,
                             corrected_fields: ["product_code"] };
    mount("ADMIN", detail);
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(2));
    const row = screen.getAllByTestId("line-item-row")[1];
    expect(within(row).getByTitle("Corrected by hand; the printed value is in the line's history")).toHaveTextContent("0-12000-12630-0");
    expect(screen.queryByTestId("edit-upc")).toBeNull();
    expect(screen.queryByTestId("upc-cut-off")).toBeNull();
  });
});

/**
 * The UPC editor says nothing until the reviewer types. An editor that has just opened,
 * empty or holding the printed value, keeps Save disabled without an error message.
 */
describe("InvoiceDetailPage — UPC editor messages", () => {
  function editorInvoice(): InvoiceDetail {
    const detail = rcmInvoice();
    return {
      ...detail,
      line_items: [
        line({ sort_order: 0, description: "ALANINU CSMCSTDST", product_code: "8-10030-51002-",
               product_code_cut_off: "ends in a separator, so the rest of the code is missing" }),
        line({ sort_order: 1, description: "2.09 GM VAN MINI CRE", product_code: null, product_code_missing: true,
               supplier_item_id: "00025328" }),
      ],
    };
  }

  it("opens an unchanged cut-off code without an error, and reports one once it is edited", async () => {
    const user = userEvent.setup();
    mount("MANAGER", redactForRole(editorInvoice(), "MANAGER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(2));
    const row = screen.getAllByTestId("line-item-row")[0];
    await user.click(within(row).getByRole("button", { name: "Enter the full UPC for row 1: 8-10030-51002-" }));
    const input = within(row).getByRole("textbox", { name: "Full UPC printed on this line (row 1)" });
    expect(input).toHaveValue("8-10030-51002-");
    expect(within(row).queryByRole("alert")).toBeNull();                 // the printed value is not an error
    expect(within(row).getByRole("button", { name: "Save" })).toBeDisabled();

    await user.type(input, "x");                                          // now an invalid edit
    expect(within(row).getByRole("alert")).toHaveTextContent("A UPC is digits only (spaces and dashes are allowed).");
    expect(within(row).getByRole("button", { name: "Save" })).toBeDisabled();
    await user.clear(input);
    expect(within(row).getByRole("alert")).toHaveTextContent("Enter the full UPC.");
    await user.type(input, "8-10030-51002-1");
    expect(within(row).queryByRole("alert")).toBeNull();
    expect(within(row).getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("opens an empty missing-UPC editor with neutral help, and asks for the UPC only after typing", async () => {
    const user = userEvent.setup();
    mount("MANAGER", redactForRole(editorInvoice(), "MANAGER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(2));
    const row = screen.getAllByTestId("line-item-row")[1];
    await user.click(within(row).getByRole("button", { name: "Enter the UPC for row 2" }));
    const input = within(row).getByRole("textbox", { name: "UPC for this line (row 2)" });
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "Enter the UPC");
    expect(within(row).queryByRole("alert")).toBeNull();
    expect(within(row).getByTestId("upc-edit")).toHaveTextContent("Type the UPC printed for this product.");
    expect(within(row).getByRole("button", { name: "Save" })).toBeDisabled();

    await user.type(input, "1");
    await user.clear(input);
    expect(within(row).getByRole("alert")).toHaveTextContent("Enter the UPC.");
    expect(within(row).getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

/**
 * Frito-Lay prints a short UPC fragment and its own 8-digit ITEM number, so a row can
 * arrive with no resolved UPC. The backend marks it (product_code_missing) and blocks
 * the export; a manager enters the UPC through the same audited correction. The
 * supplier ITEM is context only and is refused as a UPC.
 */
describe("InvoiceDetailPage — entering a missing UPC", () => {
  function fritoInvoice(): InvoiceDetail {
    const detail = rcmInvoice();
    return {
      ...detail,
      pdi_export_blocked_reason: "2 products have no resolved UPC/item code (2.09 GM VAN MINI CRE, 2.09 GM PNB CREMES).",
      line_items: [
        line({ sort_order: 0, description: "2.09 GM VAN MINI CRE", product_code: null, product_code_missing: true, supplier_item_id: "00025328" }),
        line({ sort_order: 1, description: "2.09 GM PNB CREMES", product_code: null, product_code_missing: true, supplier_item_id: null }),
        line({ sort_order: 2, description: "PEPSI 20OZ", product_code: "0-12000-00113-0" }),
        line({ sort_order: 3, description: "MT DEW 20OZ", product_code: "0-12000-1263", product_code_cut_off: "stops partway through a dashed UPC (1-5-5-1) layout" }),
      ],
    };
  }

  it("shows the gap with the supplier ITEM as context and lets a manager enter the UPC", async () => {
    const user = userEvent.setup();
    vi.mocked(api.correctLineItem).mockClear();
    vi.mocked(api.correctLineItem).mockResolvedValue({
      item: { sort_order: 0, description: "2.09 GM VAN MINI CRE", product_code: "028400260921", quantity: 12, unit_price: 1.19,
              line_total: 14.28, unit_deposit: null, corrected_fields: ["product_code"] },
      status: "REVIEW_REQUIRED", composite_confidence: 0.9, failed_checks: 1, review_reasons: [],
      pdi_export_allowed: false, pdi_export_blocked_reason: null,
    } as never);
    mount("MANAGER", redactForRole(fritoInvoice(), "MANAGER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    const [missing, missingNoItem, complete, cutOff] = screen.getAllByTestId("line-item-row");

    expect(within(missing).getByTestId("upc-missing")).toHaveTextContent("No UPC");
    expect(within(missing).getByTestId("supplier-item")).toHaveTextContent("Supplier ITEM 00025328");
    expect(within(missingNoItem).getByTestId("upc-missing")).toBeInTheDocument();
    expect(within(missingNoItem).queryByTestId("supplier-item")).toBeNull();
    // A complete UPC renders as before; a cut-off code keeps its own editor.
    expect(within(complete).queryByTestId("upc-missing")).toBeNull();
    expect(within(complete).queryByTestId("edit-upc")).toBeNull();
    expect(within(complete).queryByTestId("supplier-item")).toBeNull();
    expect(within(cutOff).getByTestId("upc-cut-off")).toBeInTheDocument();
    expect(within(cutOff).getByRole("button", { name: "Enter the full UPC for row 4: 0-12000-1263" })).toBeInTheDocument();

    await user.click(within(missing).getByRole("button", { name: "Enter the UPC for row 1" }));
    const input = within(missing).getByRole("textbox", { name: "UPC for this line (row 1)" });
    expect(input).toHaveValue("");                                     // never pre-filled with the ITEM number
    expect(within(missing).getByTestId("upc-edit")).toHaveTextContent("The supplier ITEM number is not a UPC");
    const save = within(missing).getByRole("button", { name: "Save" });

    await user.type(input, "0002-5328");
    expect(within(missing).getByRole("alert")).toHaveTextContent("That is the supplier ITEM number, not a UPC.");
    expect(save).toBeDisabled();
    await user.clear(input);
    await user.type(input, "RB248904");
    expect(within(missing).getByRole("alert")).toHaveTextContent("A UPC is digits only");
    expect(save).toBeDisabled();
    await user.clear(input);
    await user.type(input, "028400260921{Enter}");
    await waitFor(() => expect(api.correctLineItem).toHaveBeenCalledWith(
      "623b4a1c-d91d-4023-b702-d4eb8fc2683e", 0,
      // With a supplier ITEM number on the row, the UPC is remembered for the vendor by default.
      { product_code: "028400260921", corrected_by: null, remember_vendor_item: true }));
    expect(api.correctLineItem).toHaveBeenCalledTimes(1);
  });

  it("shows a USER the gap and the ITEM context but no way to enter a UPC", async () => {
    mount("USER", redactForRole(fritoInvoice(), "USER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    expect(screen.getAllByTestId("upc-missing")).toHaveLength(2);
    expect(screen.getByTestId("supplier-item")).toHaveTextContent("00025328");
    expect(screen.queryByTestId("edit-upc")).toBeNull();
  });

  it("stops offering entry once the row has a resolved UPC, and marks it corrected", async () => {
    const detail = fritoInvoice();
    detail.line_items[0] = { ...detail.line_items[0], product_code: "028400260921", product_code_missing: false,
                             corrected_fields: ["product_code"] };
    mount("ADMIN", detail);
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    const row = screen.getAllByTestId("line-item-row")[0];
    expect(within(row).queryByTestId("upc-missing")).toBeNull();
    expect(within(row).queryByTestId("edit-upc")).toBeNull();
    expect(within(row).queryByTestId("supplier-item")).toBeNull();
    expect(within(row).getByTitle("Corrected by hand; the printed value is in the line's history")).toHaveTextContent("028400260921");
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
    expect(within(rowFor(34)).getByRole("button", { name: "Edit product name for row 35: BUDWEISER 18/12 CAN" })).toBeInTheDocument();
    expect(screen.queryByTestId("check-row-link")).toBeNull();
  });
});


/**
 * jsdom has no layout: give the pinning measurement the geometry it reads — the pinned columns'
 * widths, the Line total header's right edge (table left = 0) and the scroll area's width.
 */
function stubTableGeometry(geometry: { areaWidth: number; moneyRight: number }) {
  const rect = (r: Partial<DOMRect>) => ({ x: 0, y: 0, top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, toJSON: () => ({}), ...r }) as DOMRect;
  const rectSpy = vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const el = this as HTMLElement;
    if (el.dataset?.pin === "checks") return rect({ width: 56 });
    if (el.dataset?.pin === "void") return rect({ width: 40 });
    if (el.hasAttribute?.("data-money-edge")) return rect({ right: geometry.moneyRight });
    return rect({});
  });
  const widthSpy = vi.spyOn(Element.prototype, "clientWidth", "get").mockImplementation(function (this: Element) {
    return (this as HTMLElement).dataset?.testid === "line-items-scroll" ? geometry.areaWidth : 0;
  });
  return () => { rectSpy.mockRestore(); widthSpy.mockRestore(); };
}

function photo(page_number: number) {
  return { page_number, filename: `p${page_number}.jpg`, mime_type: "image/jpeg", file_size_bytes: 1,
           source_type: "ocr", mean_confidence: 0.97, text_chars: 100 };
}

describe("InvoiceDetailPage — line-items table layout", () => {
  it("shows a multi-photo row's photo numbers in its Product cell instead of a separate Photo column", async () => {
    const detail = rcmInvoice();
    detail.photos = [photo(1), photo(2)];
    detail.line_items = [
      line({ sort_order: 0, description: "FIRST", source_pages: [1] }),
      line({ sort_order: 1, description: "OVERLAP", source_pages: [1, 2] }),
      line({ sort_order: 2, description: "ADDED BY HAND", source_pages: [], entry_source: "manual" }),
    ];
    mount("ADMIN", detail);
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(3));
    expect(screen.queryByRole("columnheader", { name: "Photo" })).toBeNull();
    const [first, overlap, manual] = screen.getAllByTestId("line-item-row");
    // Screen readers hear "Photo P1" / "Photos P1 P2"; the prefix is visually hidden.
    expect(within(first).getByTestId("photo-badge")).toHaveTextContent(/^Photo P1$/);
    expect(within(overlap).getByTestId("photo-badge")).toHaveTextContent(/^Photos P1 P2$/);
    expect(within(overlap).getByText("Photos")).toHaveClass("sr-only");
    expect(within(overlap).getByTestId("photo-badge")).toHaveAttribute("title", "Read from photo 1 and 2");
    expect(within(manual).queryByTestId("photo-badge")).toBeNull();
    // The badge sits in the Product cell, next to the name.
    expect(within(overlap).getByTestId("photo-badge").closest("td")).toHaveTextContent("OVERLAP");
  });

  it("shows no photo numbers on a single-photo invoice", async () => {
    const detail = rcmInvoice();
    detail.photos = [photo(1)];
    detail.line_items = detail.line_items.map((item) => ({ ...item, source_pages: [1] }));
    mount("ADMIN", detail);
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    expect(screen.queryByTestId("photo-badge")).toBeNull();
  });

  it("keeps the full product name inspectable: tooltip and the edit button's accessible name", async () => {
    const detail = rcmInvoice();
    const full = "ARIZONA HARD TEA C-12 220Z HARD GRAPE LEMONADE MIXED PACK";
    detail.line_items = [line({ sort_order: 0, description: full })];
    mount("MANAGER", redactForRole(detail, "MANAGER"));
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(1));
    const row = screen.getByTestId("line-item-row");
    expect(within(row).getByTitle(full)).toHaveTextContent(full);
    expect(within(row).getByRole("button", { name: `Edit product name for row 1: ${full}` })).toBeInTheDocument();
  });

  it.each([
    ["ADMIN", { mapping: null, checks: "40px", void: "0px" }],
    ["MANAGER", { mapping: null, checks: null, void: "0px" }],
    ["USER", { mapping: null, checks: null, void: null }],
  ] as const)("pins only Checks and void for a %s when that hides no money, with exact offsets; Mapping scrolls", async (role, expected) => {
    const restore = stubTableGeometry({ areaWidth: 1000, moneyRight: 700 });
    try {
      const detail = role === "ADMIN" ? rcmInvoice() : redactForRole(rcmInvoice(), role);
      mount(role, detail);
      await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
      const mappingHead = screen.getByRole("columnheader", { name: "Mapping" });
      expect(mappingHead).not.toHaveClass("sticky");
      expect(mappingHead.style.right).toBe("");
      await waitFor(() => expect(document.querySelectorAll("thead [data-pinned]").length).toBe(Object.values(expected).filter(Boolean).length));
      assertPinned(expected);
    } finally {
      restore();
    }
  });

  it("falls back to ordinary scrolling when the pinned group would cover the money columns at rest", async () => {
    // Line total ends at 650px; the area is 700px wide and the pinned group 96px: 650 > 604.
    const restore = stubTableGeometry({ areaWidth: 700, moneyRight: 650 });
    try {
      mount("ADMIN", rcmInvoice());
      await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
      expect(document.querySelectorAll("[data-pinned]")).toHaveLength(0);
      document.querySelectorAll<HTMLElement>("[data-pin]").forEach((cell) => {
        expect(cell).not.toHaveClass("sticky");
        expect(cell.style.right).toBe("");
      });
      expect(screen.getByTestId("line-items-scroll").style.scrollPaddingRight).toBe("");
    } finally {
      restore();
    }
  });

  it("re-decides pinning when the table area is resized or the money edge moves", async () => {
    const observers: { cb: ResizeObserverCallback }[] = [];
    vi.stubGlobal("ResizeObserver", class {
      cb: ResizeObserverCallback;
      constructor(cb: ResizeObserverCallback) { this.cb = cb; observers.push(this); }
      observe() {}
      unobserve() {}
      disconnect() {}
    });
    const geometry = { areaWidth: 1000, moneyRight: 700 };
    const restore = stubTableGeometry(geometry);
    const fire = () => act(() => observers.forEach((o) => o.cb([], o as unknown as ResizeObserver)));
    try {
      mount("ADMIN", rcmInvoice());
      await waitFor(() => expect(document.querySelectorAll("thead [data-pinned]")).toHaveLength(2));
      geometry.areaWidth = 700;                    // narrower: Line total (700) would sit under the group
      await fire();
      expect(document.querySelectorAll("[data-pinned]")).toHaveLength(0);
      geometry.areaWidth = 1000;                   // wide again
      await fire();
      expect(document.querySelectorAll("thead [data-pinned]")).toHaveLength(2);
      geometry.moneyRight = 950;                   // content change widens the money columns
      await fire();
      expect(document.querySelectorAll("[data-pinned]")).toHaveLength(0);
    } finally {
      restore();
      vi.unstubAllGlobals();
    }
  });

  it("brings a keyboard-focused figure fully into view when it is only partly visible", async () => {
    const user = userEvent.setup();
    mount("ADMIN", rcmInvoice());
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    const area = screen.getByTestId("line-items-scroll");
    const button = within(screen.getAllByTestId("line-item-row")[0]).getByRole("button", { name: "Correct line total for GM VAN MINI CRE" });
    const rect = (left: number, right: number) => ({ left, right, top: 0, bottom: 0, x: left, y: 0, width: right - left, height: 0, toJSON: () => ({}) }) as DOMRect;
    vi.spyOn(area, "getBoundingClientRect").mockReturnValue(rect(0, 700));
    vi.spyOn(button, "getBoundingClientRect").mockReturnValue(rect(660, 720));   // 20px past the edge, not pinned
    area.scrollLeft = 0;
    await user.click(document.body);
    act(() => button.focus());
    expect(area.scrollLeft).toBe(28);                                            // 20px overhang + 8px margin
  });

  function assertPinned(expected: Readonly<Record<string, string | null>>) {
    for (const [name, right] of Object.entries(expected)) {
      const head = document.querySelector<HTMLElement>(`thead [data-pinned="${name}"]`);
      const cells = document.querySelectorAll<HTMLElement>(`tbody [data-pinned="${name}"]`);
      if (right === null) {
        expect(head).toBeNull();
        expect(cells).toHaveLength(0);
        continue;
      }
      expect(head).toHaveClass("sticky");
      expect(head!.style.right).toBe(right);
      expect(cells).toHaveLength(4);
      cells.forEach((cell) => { expect(cell).toHaveClass("sticky"); expect(cell.style.right).toBe(right); });
    }
  }

  it("makes the scroll area a labelled, keyboard-focusable region", async () => {
    mount("ADMIN", rcmInvoice());
    await waitFor(() => expect(screen.getAllByTestId("line-item-row")).toHaveLength(4));
    const region = screen.getByRole("region", { name: "Line items — scroll sideways for more columns" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toContainElement(screen.getAllByTestId("line-item-row")[0]);
  });
});

describe("InvoiceDetailPage — out of the active workflow", () => {
  it("shows a binned invoice read-only, with no correction, Retailed or link editing", async () => {
    mount("MANAGER", { ...redactForRole(rcmInvoice(), "MANAGER"), document_status: "BINNED" });
    expect(await screen.findByTestId("inactive-notice")).toHaveTextContent("in the Recycle Bin");
    expect(screen.getByRole("link", { name: "Open the Recycle Bin" })).toHaveAttribute("href", "/recycle-bin");
    expect(screen.queryByRole("button", { name: /Add link|Change link/ })).toBeNull();
    expect(screen.queryByText("Correcting as")).toBeNull();
  });

  it("explains a reference copy and keeps it read-only", async () => {
    mount("ADMIN", { ...rcmInvoice(), document_status: "REFERENCE" });
    expect(await screen.findByTestId("inactive-notice")).toHaveTextContent("read-only reference copy");
    expect(screen.queryByRole("button", { name: /Add link|Change link/ })).toBeNull();
  });
});
