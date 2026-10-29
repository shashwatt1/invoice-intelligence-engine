import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DeletionRequest, RecycleBinEntry } from "@/api/types";

/**
 * The Recycle Bin page follows the server's rules: anyone who sees an item may
 * restore it unless an active copy exists, or ask for its deletion with a
 * reason; only an administrator deletes permanently (with a reason), restores a
 * conflicting item as a reference copy, and approves or rejects (with a note)
 * deletion requests. A requester can withdraw their own request.
 */

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const role = { current: "MANAGER" as "USER" | "MANAGER" | "ADMIN" };
const RANK = { USER: 0, MANAGER: 1, ADMIN: 2 } as const;
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ user: { username: "u", role: role.current }, isLoading: false,
                    hasRole: (m: keyof typeof RANK) => RANK[role.current] >= RANK[m] }),
}));
const api = { list: vi.fn(), summary: vi.fn(), requests: vi.fn(), restore: vi.fn(), request: vi.fn(),
              cancel: vi.fn(), decide: vi.fn(), purge: vi.fn() };
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listRecycleBin: (...a: unknown[]) => api.list(...a),
  getRecycleBinSummary: (...a: unknown[]) => api.summary(...a),
  listDeletionRequests: (...a: unknown[]) => api.requests(...a),
  restoreBinEntry: (...a: unknown[]) => api.restore(...a),
  requestBinDeletion: (...a: unknown[]) => api.request(...a),
  cancelDeletionRequest: (...a: unknown[]) => api.cancel(...a),
  decideDeletionRequest: (...a: unknown[]) => api.decide(...a),
  purgeBinEntry: (...a: unknown[]) => api.purge(...a),
}));

const { RecycleBinPage } = await import("./recycle-bin");

const entry = (id: string, extra: Partial<RecycleBinEntry> = {}): RecycleBinEntry => ({
  id, document_id: `doc-${id}`, filename: `${id}.pdf`, invoice_id: `inv-${id}`, invoice_number: `N-${id}`,
  vendor_name: "Acme", store: null, previous_status: "COMPLETED", binned_at: "2026-10-28T10:00:00Z",
  binned_by: "barj", binned_by_role: "MANAGER", reason: "wrong photo", status: "IN_BIN", closed_at: null,
  closed_by: null, close_note: null, reuploaded_as_document_id: null, conflicting_document_id: null,
  pending_request: null, ...extra,
});
const PLAIN = entry("plain");
const CLASH = entry("clash", { conflicting_document_id: "doc-active", reuploaded_as_document_id: "doc-active" });
const REQUEST: DeletionRequest = {
  id: "req-1", entry_id: "plain", requested_at: "2026-10-28T11:00:00Z", requested_by: "barj",
  requested_by_role: "MANAGER", reason: "duplicate", status: "PENDING", decided_at: null, decided_by: null,
  decision_note: null, filename: "plain.pdf", invoice_number: "N-plain", vendor_name: "Acme",
};

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><RecycleBinPage /></MemoryRouter></QueryClientProvider>);
}
const rowOf = async (text: string) => (await screen.findByText(text)).closest("tr")!;

beforeEach(() => {
  vi.clearAllMocks();
  role.current = "MANAGER";
  api.list.mockResolvedValue({ success: true, items: [PLAIN, CLASH], total: 2, page: 1, page_size: 25 });
  api.summary.mockResolvedValue({ in_bin: 2, pending_requests: 1 });
  api.requests.mockResolvedValue({ success: true, items: [REQUEST], total: 1, page: 1, page_size: 100 });
  for (const fn of [api.restore, api.request, api.cancel, api.decide, api.purge]) fn.mockResolvedValue({});
});

describe("Recycle Bin — manager", () => {
  it("restores an item, but not one an active copy now holds", async () => {
    const user = userEvent.setup();
    mount();
    const plain = await rowOf("#N-plain");
    await user.click(within(plain).getByRole("button", { name: /Restore/ }));
    await user.click(screen.getByTestId("bin-dialog-submit"));
    await waitFor(() => expect(api.restore).toHaveBeenCalledWith("plain", undefined, undefined));
    const clash = await rowOf("#N-clash");
    expect(within(clash).getByText("Active copy exists")).toBeInTheDocument();
    expect(within(clash).getByText("Processed again")).toBeInTheDocument();
    expect(within(clash).queryByRole("button", { name: /Restore/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Delete permanently/ })).toBeNull();
  });

  it("asks for permanent deletion only with a reason", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(within(await rowOf("#N-plain")).getByRole("button", { name: "Request deletion" }));
    expect(screen.getByTestId("bin-dialog-submit")).toBeDisabled();
    await user.type(screen.getByLabelText("Reason"), "duplicate");
    await user.click(screen.getByTestId("bin-dialog-submit"));
    await waitFor(() => expect(api.request).toHaveBeenCalledWith("plain", "duplicate"));
  });

  it("withdraws its own pending request", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("tab", { name: /My deletion requests/ }));
    await user.click(within(await rowOf("duplicate")).getByRole("button", { name: "Withdraw" }));
    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith("req-1"));
  });
});

describe("Recycle Bin — administrator", () => {
  beforeEach(() => { role.current = "ADMIN"; });

  it("restores a conflicting item as a reference copy and deletes permanently only with a reason", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(within(await rowOf("#N-clash")).getByRole("button", { name: /Restore as reference copy/ }));
    await user.type(screen.getByLabelText("Note (optional)"), "debugging");
    await user.click(screen.getByTestId("bin-dialog-submit"));
    await waitFor(() => expect(api.restore).toHaveBeenCalledWith("clash", true, "debugging"));

    await user.click(within(await rowOf("#N-plain")).getByRole("button", { name: /Delete permanently/ }));
    expect(screen.getByTestId("bin-dialog-submit")).toBeDisabled();
    await user.type(screen.getByLabelText("Reason"), "test upload");
    await user.click(screen.getByTestId("bin-dialog-submit"));
    await waitFor(() => expect(api.purge).toHaveBeenCalledWith("plain", "test upload"));
    expect(screen.queryByRole("button", { name: "Request deletion" })).toBeNull();
  });

  it("approves a request, and rejects one only with a note", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(await screen.findByRole("tab", { name: /^Deletion requests/ }));
    const row = await rowOf("duplicate");
    await user.click(within(row).getByRole("button", { name: "Reject" }));
    expect(screen.getByTestId("decision-submit")).toBeDisabled();
    await user.type(screen.getByLabelText("Note"), "keep for audit");
    await user.click(screen.getByTestId("decision-submit"));
    await waitFor(() => expect(api.decide).toHaveBeenCalledWith("req-1", false, "keep for audit"));
    await user.click(within(await rowOf("duplicate")).getByRole("button", { name: "Approve and delete" }));
    await user.click(screen.getByTestId("decision-submit"));
    await waitFor(() => expect(api.decide).toHaveBeenCalledWith("req-1", true, undefined));
  });
});

describe("Recycle Bin — empty", () => {
  it("says so", async () => {
    api.list.mockResolvedValue({ success: true, items: [], total: 0, page: 1, page_size: 25 });
    mount();
    expect(await screen.findByText("The Recycle Bin is empty")).toBeInTheDocument();
  });
});
