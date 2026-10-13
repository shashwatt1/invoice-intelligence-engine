import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Completion, HistoryRow } from "@/api/types";

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
const api = { saveReferenceLink: vi.fn(), markRetailed: vi.fn(), reopenRetailed: vi.fn() };
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  saveReferenceLink: (...a: unknown[]) => api.saveReferenceLink(...a),
  markRetailed: (...a: unknown[]) => api.markRetailed(...a),
  reopenRetailed: (...a: unknown[]) => api.reopenRetailed(...a),
}));

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
});

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
  it("explains why it cannot be marked yet and keeps the button disabled", () => {
    wrap(<tr><td><RetailedControl invoiceId="i" completion={completion({ reason: "No delivered PDI file is recorded for this invoice yet." })} /></td></tr>);
    expect(screen.getByTestId("retailed-mark")).toBeDisabled();
    expect(screen.getByTestId("retailed-reason")).toHaveTextContent("No delivered PDI file is recorded");
  });

  it("marks when the server says it can", async () => {
    const user = userEvent.setup();
    wrap(<tr><td><RetailedControl invoiceId="inv-1" completion={completion({ can_mark: true, latest_export_number: 1 })} /></td></tr>);
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
    expect(screen.getByTestId("retailed-control")).toHaveTextContent("Retailed · changed since");
    expect(screen.getByTestId("retailed-mark")).toHaveTextContent("Mark Retailed again");
  });

  it("a USER sees the status but cannot act", () => {
    role.current = "USER";
    wrap(<tr><td><RetailedControl invoiceId="i" completion={completion({ status: "retailed" })} /></td></tr>);
    expect(screen.getByTestId("retailed-control")).toHaveTextContent("Retailed");
    expect(screen.queryByRole("button")).toBeNull();
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
    expect(screen.getByText("Retailed · changed")).toBeInTheDocument();
  });
});
