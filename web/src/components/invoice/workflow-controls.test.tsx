import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Completion, HistoryRow, ReceiptPreview } from "@/api/types";

/**
 * The original-invoice link (manager edits, everyone can open it in a new tab,
 * https only) and the Retailed control (the server decides; the reason is shown
 * when it cannot be marked; stale is visible; reopen is explicit; USER cannot act).
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const role = { current: "MANAGER" as "USER" | "MANAGER" };
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: { username: "barj", role: role.current }, isLoading: false,
    hasRole: (minimum: string) => (role.current === "MANAGER" ? true : minimum === "USER"),
  }),
}));
const api = { saveReferenceLink: vi.fn(), markRetailed: vi.fn(), reopenRetailed: vi.fn(),
              getInventoryStatus: vi.fn(), getReceiptPreview: vi.fn() };
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  saveReferenceLink: (...a: unknown[]) => api.saveReferenceLink(...a),
  markRetailed: (...a: unknown[]) => api.markRetailed(...a),
  reopenRetailed: (...a: unknown[]) => api.reopenRetailed(...a),
  getInventoryStatus: (...a: unknown[]) => api.getInventoryStatus(...a),
  getReceiptPreview: (...a: unknown[]) => api.getReceiptPreview(...a),
}));
const { ApiError } = await import("@/api/client");

const { ReferenceLinkCard } = await import("./reference-link-card");
const { RetailedControl } = await import("./retailed-control");
const { InvoiceRow } = await import("@/components/shared/invoice-table");

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><table><tbody>{node}</tbody></table></MemoryRouter></QueryClientProvider>);
}

function completion(extra: Partial<Completion> = {}): Completion {
  return { status: "not_retailed", can_mark: false, reason: null, latest_export_number: null, latest_export_at: null,
           ledger_enabled: true, retailed_at: null, retailed_by: null, ...extra };
}

beforeEach(() => {
  vi.clearAllMocks();
  role.current = "MANAGER";
  api.saveReferenceLink.mockImplementation(async (_id: string, url: string | null) =>
    url ? { url, set_by: "barj", set_at: "2026-10-13T10:00:00Z" } : null);
  api.markRetailed.mockResolvedValue(completion({ status: "retailed" }));
  api.reopenRetailed.mockResolvedValue(completion());
  api.getInventoryStatus.mockResolvedValue({ enabled: false });
  api.getReceiptPreview.mockRejectedValue(new Error("not expected"));
});

function receiptPreview(extra: Partial<ReceiptPreview> = {}): ReceiptPreview {
  const submitted = { id: "r1", invoice_id: "inv-1", invoice_number: "50185", status: "SUBMITTED" as const, line_count: 2,
                      created_at: "2026-10-13T10:00:00Z", submitted_by: "barj", submitted_at: "2026-10-13T10:00:00Z",
                      received_on: null, confirmed_by: null, confirmed_role: null, discrepancy_count: null };
  return {
    invoice_id: "inv-1", invoice_number: "50185", store: { id: "store-b", label: "AF NV Midler" } as ReceiptPreview["store"],
    eligibility_reasons: [], lines: [], blockers: [], receipt: null, history: [submitted], can_approve: true,
    submission: { receipt: submitted, proposed_lines: [], proposed_blockers: [], export_number: 1, stale_reasons: [],
                  line_changes: [] },
    ...extra,
  };
}

describe("original invoice link", () => {
  it("opens in a new tab with safe attributes and says who saved it", () => {
    wrap(<tr><td><ReferenceLinkCard invoiceId="i" link={{ url: "https://chat.example.com/m/50185", set_by: "barj", set_at: "2026-10-13T10:00:00Z" }} /></td></tr>);
    const link = screen.getByTestId("reference-link-open");
    expect(link).toHaveAttribute("href", "https://chat.example.com/m/50185");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByTestId("reference-link-card")).toHaveTextContent("Saved by barj");
    expect(screen.getByTestId("reference-link-card")).toHaveTextContent("still needs your own access");
  });

  it("manager adds a link; an http link is refused before saving", async () => {
    const user = userEvent.setup();
    wrap(<tr><td><ReferenceLinkCard invoiceId="inv-1" link={null} /></td></tr>);
    await user.click(screen.getByRole("button", { name: "Add link" }));
    await user.type(screen.getByRole("textbox", { name: "Original invoice or chat link" }), "http://example.com/x");
    expect(screen.getByRole("alert")).toHaveTextContent("Only https:// links");
    expect(screen.getByRole("button", { name: "Save link" })).toBeDisabled();
    await user.clear(screen.getByRole("textbox", { name: "Original invoice or chat link" }));
    await user.type(screen.getByRole("textbox", { name: "Original invoice or chat link" }), "https://example.com/x");
    await user.click(screen.getByRole("button", { name: "Save link" }));
    await waitFor(() => expect(api.saveReferenceLink).toHaveBeenCalledWith("inv-1", "https://example.com/x"));
  });

  it("manager removes a link", async () => {
    const user = userEvent.setup();
    wrap(<tr><td><ReferenceLinkCard invoiceId="inv-1" link={{ url: "https://example.com/x", set_by: "barj", set_at: null }} /></td></tr>);
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(api.saveReferenceLink).toHaveBeenCalledWith("inv-1", null));
  });

  it("a USER can open the link but not change it", () => {
    role.current = "USER";
    wrap(<tr><td><ReferenceLinkCard invoiceId="i" link={{ url: "https://example.com/x", set_by: "barj", set_at: null }} /></td></tr>);
    expect(screen.getByTestId("reference-link-open")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Change link|Add link|Remove/ })).toBeNull();
  });
});

describe("Retailed control", () => {
  it("explains that a REVIEW_REQUIRED invoice must be VALIDATED first", () => {
    wrap(<tr><td><RetailedControl invoiceId="i" completion={completion({
      reason: "The invoice must be VALIDATED before it can be marked Retailed (it is REVIEW_REQUIRED). Resolve the failed checks or correct the invoice first." })} /></td></tr>);
    expect(screen.getByTestId("retailed-mark")).toBeDisabled();
    expect(screen.getByTestId("retailed-reason")).toHaveTextContent("must be VALIDATED");
    expect(screen.queryByTestId("retailed-attestation-note")).toBeNull();
  });

  it("explains why it cannot be marked yet and keeps the button disabled", () => {
    wrap(<tr><td><RetailedControl invoiceId="i" completion={completion({ reason: "No delivered PDI file is recorded for this invoice yet." })} /></td></tr>);
    expect(screen.getByTestId("retailed-mark")).toBeDisabled();
    expect(screen.getByTestId("retailed-reason")).toHaveTextContent("No delivered PDI file is recorded");
  });

  it("offers the way to the vendor and delivery date when those are what Retailed waits for", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    const target = document.createElement("section");
    target.id = "declarations";
    target.scrollIntoView = scrollIntoView;
    document.body.appendChild(target);
    wrap(<tr><td><RetailedControl invoiceId="i" completion={completion({ reason: "Before this invoice can be marked Retailed: set the delivery date." })}
                                  declarationsMissing="Before this invoice can be marked Retailed: set the delivery date." /></td></tr>);
    await user.click(screen.getByTestId("retailed-set-declarations"));
    expect(scrollIntoView).toHaveBeenCalled();
    target.remove();
  });

  it("does not offer it when nothing is missing, when already Retailed, or to a USER", () => {
    wrap(<tr><td><RetailedControl invoiceId="i" completion={completion()} declarationsMissing={null} /></td></tr>);
    expect(screen.queryByTestId("retailed-set-declarations")).toBeNull();
  });

  it("marks when the server says it can, after reminding what the person attests", async () => {
    const user = userEvent.setup();
    wrap(<tr><td><RetailedControl invoiceId="inv-1" completion={completion({ can_mark: true, latest_export_number: 1 })} /></td></tr>);
    expect(screen.getByTestId("retailed-attestation-note")).toHaveTextContent(
      "check the item list, quantities, unit prices, line totals and totals against the source invoice");
    await user.click(screen.getByTestId("retailed-mark"));
    await waitFor(() => expect(api.markRetailed).toHaveBeenCalledWith("inv-1"));
  });

  it("shows Retailed and offers an explicit reopen with a reason", async () => {
    const user = userEvent.setup();
    wrap(<tr><td><RetailedControl invoiceId="inv-1" completion={completion({ status: "retailed", retailed_by: "barj", retailed_at: "2026-10-13T10:00:00Z" })} /></td></tr>);
    expect(screen.getByTestId("retailed-control")).toHaveTextContent("Retailed");
    expect(screen.queryByTestId("retailed-mark")).toBeNull();
    await user.click(screen.getByTestId("retailed-reopen"));
    await user.type(screen.getByRole("textbox", { name: "Reason for reopening" }), "credit note");
    await user.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(api.reopenRetailed).toHaveBeenCalledWith("inv-1", "credit note"));
  });

  it("shows a stale mark as changed since, and lets it be marked again once provable", () => {
    wrap(<tr><td><RetailedControl invoiceId="i" completion={completion({ status: "retailed_stale", can_mark: true, retailed_by: "barj" })} /></td></tr>);
    expect(screen.getByTestId("retailed-control")).toHaveTextContent("Retailed · re-verify");
    expect(screen.getByTestId("retailed-mark")).toHaveTextContent("Mark Retailed again");
  });

  it("a USER sees the status but cannot act", () => {
    role.current = "USER";
    wrap(<tr><td><RetailedControl invoiceId="i" completion={completion({ status: "retailed" })} /></td></tr>);
    expect(screen.getByTestId("retailed-control")).toHaveTextContent("Retailed");
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("inventory receipt beside Retailed", () => {
  it("asks nothing about the receipt while inventory is off", async () => {
    wrap(<tr><td><RetailedControl invoiceId="inv-1" completion={completion({ status: "retailed" })} /></td></tr>);
    await waitFor(() => expect(api.getInventoryStatus).toHaveBeenCalled());
    expect(api.getReceiptPreview).not.toHaveBeenCalled();
    expect(screen.queryByTestId("inventory-receipt-status")).not.toBeInTheDocument();
  });

  it("shows a pending submission with a link to the store's approval queue", async () => {
    api.getInventoryStatus.mockResolvedValue({ enabled: true });
    api.getReceiptPreview.mockResolvedValue(receiptPreview());
    wrap(<tr><td><RetailedControl invoiceId="inv-1" completion={completion({ status: "retailed" })} /></td></tr>);
    const chip = await screen.findByTestId("inventory-receipt-status");
    expect(chip).toHaveTextContent("Inventory · awaiting approval");
    expect(within(chip).getByRole("link", { name: "Review" })).toHaveAttribute("href", "/inventory?store=store-b&tab=approvals");
  });

  it("says when the submission is out of date", async () => {
    api.getInventoryStatus.mockResolvedValue({ enabled: true });
    const stale = receiptPreview();
    stale.submission = { ...stale.submission!, stale_reasons: ["invoice_changed_since_retailed"] };
    api.getReceiptPreview.mockResolvedValue(stale);
    wrap(<tr><td><RetailedControl invoiceId="inv-1" completion={completion({ status: "retailed_stale" })} /></td></tr>);
    expect(await screen.findByTestId("inventory-receipt-status")).toHaveTextContent("submission out of date");
  });

  it("shows nothing at all for a store the account cannot use (the server's 404)", async () => {
    api.getInventoryStatus.mockResolvedValue({ enabled: true });
    api.getReceiptPreview.mockRejectedValue(new ApiError(404, { error_code: "ERR_NOT_FOUND", message: "Invoice not found.", detail: null }));
    wrap(<tr><td><RetailedControl invoiceId="inv-1" completion={completion({ status: "retailed" })} /></td></tr>);
    await waitFor(() => expect(api.getReceiptPreview).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("inventory-receipt-status")).not.toBeInTheDocument();
    expect(screen.queryByText(/Inventory ·/)).not.toBeInTheDocument();
  });

  it("is never asked for by USER", async () => {
    role.current = "USER";
    api.getInventoryStatus.mockResolvedValue({ enabled: true });
    wrap(<tr><td><RetailedControl invoiceId="inv-1" completion={completion({ status: "retailed" })} /></td></tr>);
    await screen.findByTestId("retailed-control");
    expect(api.getInventoryStatus).not.toHaveBeenCalled();
    expect(api.getReceiptPreview).not.toHaveBeenCalled();
  });
});

describe("invoice list", () => {
  const base = {
    document_id: "d", invoice_id: "i", filename: "f.jpg", document_status: "COMPLETED", invoice_number: "50185",
    vendor_name: "V", store: null, grand_total: 60, currency: "USD", status: "VALIDATED", composite_confidence: null,
    source_type: "photo", photo_count: 1, review: null, mapping_required: 0, edi_status: "ready", created_at: "2026-10-13T10:00:00Z",
  } as unknown as HistoryRow;

  it("shows Retailed, or Retailed · changed, beside the EDI status", () => {
    const { unmount } = wrap(<InvoiceRow row={{ ...base, completion_status: "retailed" }} onOpen={() => {}} showMapping />);
    expect(screen.getByText("Retailed")).toBeInTheDocument();
    unmount();
    wrap(<InvoiceRow row={{ ...base, completion_status: "retailed_stale" }} onOpen={() => {}} showMapping />);
    expect(screen.getByText("Retailed · re-verify")).toBeInTheDocument();
  });
});
