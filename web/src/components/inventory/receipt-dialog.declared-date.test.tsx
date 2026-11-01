import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ReceiptPreview } from "@/api/types";

import { ReceiptDialog } from "./receipt-dialog";

let preview: ReceiptPreview;
vi.mock("@/hooks/use-api", () => ({
  useReceiptPreview: () => ({ data: preview, isLoading: false, isError: false }),
  useApproveReceipt: () => ({ mutate: vi.fn(), isPending: false }),
  useRejectReceipt: () => ({ mutate: vi.fn(), isPending: false }),
  useReverseReceipt: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: { id: "u-1", username: "approver", role: "MANAGER" } }) }));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), error: vi.fn(), warning: vi.fn(), success: vi.fn() } }));

function submission(declared: string | null): ReceiptPreview {
  return {
    invoice_id: "inv-1", invoice_number: "A-1", store: null, eligibility_reasons: [], blockers: [], receipt: null, history: [],
    can_approve: true, declared_delivery_date: declared,
    lines: [{ invoice_item_id: "l-1", sort_order: 0, description: "LAGER", item_code: "123", cases: "2", units_per_case: 12,
              expected_units: "24", conversion_path: "case_mapping", conversion_scope: null }],
    submission: {
      receipt: { id: "r-1", invoice_id: "inv-1", invoice_number: "A-1", status: "SUBMITTED", line_count: 1, created_at: "2026-10-09T00:00:00Z",
                 submitted_by: "someone", submitted_role: "USER", received_on: null, confirmed_by: null, confirmed_role: null,
                 discrepancy_count: null },
      proposed_lines: [], proposed_blockers: [], export_number: 1, stale_reasons: [], line_changes: [],
    },
  } as unknown as ReceiptPreview;
}

function open() {
  return render(<MemoryRouter><ReceiptDialog invoiceId="inv-1" onClose={() => undefined} /></MemoryRouter>);
}

beforeEach(() => vi.clearAllMocks());

describe("the received date of a receipt", () => {
  it("starts as the delivery day declared at upload, not the browser's today", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 10, 1, 12, 0, 0));
    preview = submission("2026-10-22");
    open();
    expect(screen.getByLabelText("Received on")).toHaveValue("2026-10-22");
    expect(screen.getByTestId("received-on-hint")).toHaveTextContent("The delivery date declared at upload.");
    vi.useRealTimers();
  });

  it("says when the approver's date differs from the declared one, and that it is allowed", async () => {
    preview = submission("2026-10-22");
    const user = userEvent.setup();
    open();
    const input = screen.getByLabelText("Received on");
    await user.clear(input);
    await user.type(input, "2026-10-24");
    expect(screen.getByTestId("received-on-hint")).toHaveTextContent(/Differs from the delivery date declared at upload \(22 Oct 2026\)/);
  });

  it("falls back to today, as before, when no delivery day was declared", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 10, 1, 12, 0, 0));
    preview = submission(null);
    open();
    expect(screen.getByLabelText("Received on")).toHaveValue("2026-11-01");
    expect(screen.queryByTestId("received-on-hint")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});
