import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ExportOverrideSummary, InvoiceDetail } from "@/api/types";

/**
 * Export block and administrator override: shown to MANAGER+, the override itself only to an ADMIN, a reason is
 * mandatory, a review copy is always labelled, and an override never reads as the invoice having passed.
 */

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (...a: unknown[]) => toastSuccess(...a), error: (...a: unknown[]) => toastError(...a) } }));
const role = { current: "ADMIN" as "USER" | "MANAGER" | "ADMIN" };
const RANK = { USER: 0, MANAGER: 1, ADMIN: 2 };
vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: { username: "u", role: role.current }, isLoading: false,
    hasRole: (minimum: "USER" | "MANAGER" | "ADMIN") => RANK[role.current] >= RANK[minimum],
  }),
}));
const grant = vi.fn();
const revoke = vi.fn();
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  grantExportOverride: (...a: unknown[]) => grant(...a),
  revokeExportOverride: (...a: unknown[]) => revoke(...a),
}));

const { ExportOverridePanel } = await import("./export-override-panel");

const REASON = "Vendor confirmed the missing row is a credit";

function override(over: Partial<ExportOverrideSummary> = {}): ExportOverrideSummary {
  return { id: "o1", state: "ACTIVE", reason: REASON, blocked_checks: ["COMPLETENESS_MISSING_LINES"],
           granted_by: "admin1", created_at: "2026-10-31T10:00:00Z", ...over };
}

function detail(over: Partial<InvoiceDetail> = {}): InvoiceDetail {
  return { invoice_id: "inv-1", blocking_checks: ["COMPLETENESS_MISSING_LINES"], pdi_export_allowed: false,
           pdi_export_overridden: false, export_override: null, status: "REVIEW_REQUIRED", ...over } as InvoiceDetail;
}

function show(d: InvoiceDetail) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><ExportOverridePanel detail={d} /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  role.current = "ADMIN";
  grant.mockResolvedValue({ ...override() });
  revoke.mockResolvedValue({ ...override({ state: "REVOKED" }) });
});

describe("who sees what", () => {
  it("shows nothing to a USER, and nothing when no check fails and no override exists", () => {
    role.current = "USER";
    show(detail());
    expect(screen.queryByTestId("export-override-panel")).not.toBeInTheDocument();
    role.current = "ADMIN";
    show(detail({ blocking_checks: [] }));
    expect(screen.queryByTestId("export-override-panel")).not.toBeInTheDocument();
  });

  it("a MANAGER sees the block and the labelled review copies, but cannot override", () => {
    role.current = "MANAGER";
    show(detail());
    expect(screen.getByRole("heading", { name: "Export blocked" })).toBeInTheDocument();
    expect(screen.getByTestId("override-checks")).toHaveTextContent("COMPLETENESS_MISSING_LINES");
    expect(screen.getByTestId("review-copy-txt")).toHaveAttribute("href", "/api/v1/invoices/inv-1/export?format=txt&purpose=review");
    expect(screen.getByTestId("review-copy-csv")).toHaveAttribute("href", "/api/v1/invoices/inv-1/export?format=csv&purpose=review");
    expect(screen.getByTestId("review-copy-txt")).toHaveTextContent("not for import");
    expect(screen.queryByTestId("override-open")).not.toBeInTheDocument();
  });

  it("an ADMIN can start an override", () => {
    show(detail());
    expect(screen.getByTestId("override-open")).toBeInTheDocument();
  });
});

describe("granting needs a real reason", () => {
  it("stays disabled until the reason is long enough, then records the trimmed reason", async () => {
    const user = userEvent.setup();
    show(detail());
    await user.click(screen.getByTestId("override-open"));
    const submit = screen.getByTestId("override-submit");
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText(/Reason/), "too short");
    expect(submit).toBeDisabled();
    await user.clear(screen.getByLabelText(/Reason/));
    await user.type(screen.getByLabelText(/Reason/), `  ${REASON}  `);
    expect(submit).toBeEnabled();
    await user.click(submit);
    await waitFor(() => expect(grant).toHaveBeenCalledWith("inv-1", REASON));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("says plainly that the invoice itself is not changed", async () => {
    const user = userEvent.setup();
    show(detail());
    await user.click(screen.getByTestId("override-open"));
    expect(screen.getByRole("group", { name: "Override export block" })).toHaveTextContent("stays unvalidated");
  });

  it("reports a refusal and does not claim success", async () => {
    const user = userEvent.setup();
    grant.mockRejectedValue(new Error("Only an administrator can override an export block."));
    show(detail());
    await user.click(screen.getByTestId("override-open"));
    await user.type(screen.getByLabelText(/Reason/), REASON);
    await user.click(screen.getByTestId("override-submit"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Only an administrator can override an export block."));
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});

describe("an active override is unmistakable and never reads as a pass", () => {
  const active = () => detail({ pdi_export_allowed: true, pdi_export_overridden: true, export_override: override() });

  it("names the admin, the reason and that the checks still fail", () => {
    show(active());
    expect(screen.getByRole("heading", { name: "Export block overridden by an administrator" })).toBeInTheDocument();
    expect(screen.getByTestId("override-detail")).toHaveTextContent("admin1");
    expect(screen.getByTestId("override-detail")).toHaveTextContent(REASON);
    expect(screen.getByTestId("override-checks")).toHaveTextContent("still fail and the invoice is still not validated");
    expect(screen.getByTestId("review-copy-txt")).toBeInTheDocument();      // the failing checks still offer the review copy
    expect(screen.queryByTestId("override-open")).not.toBeInTheDocument();
  });

  it("an ADMIN can revoke it, with a reason; a MANAGER cannot", async () => {
    const user = userEvent.setup();
    const { unmount } = show(active());
    await user.click(screen.getByTestId("override-revoke-open"));
    expect(screen.getByTestId("override-submit")).toBeDisabled();
    await user.type(screen.getByLabelText(/Reason/), "Granted on the wrong invoice");
    await user.click(screen.getByTestId("override-submit"));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("o1", "Granted on the wrong invoice"));
    unmount();
    role.current = "MANAGER";
    show(active());
    expect(screen.queryByTestId("override-revoke-open")).not.toBeInTheDocument();
  });
});

describe("an override that no longer applies", () => {
  it("a stale one is described as such and the block is back, with the option to override again", () => {
    show(detail({ export_override: override({ state: "STALE" }) }));
    expect(screen.getByRole("heading", { name: "Export blocked" })).toBeInTheDocument();
    expect(screen.getByTestId("override-detail")).toHaveTextContent("the invoice changed afterwards");
    expect(screen.getByTestId("override-open")).toBeInTheDocument();
  });

  it("once the checks pass, a leftover override is only a note", () => {
    show(detail({ blocking_checks: [], export_override: override({ state: "STALE" }) }));
    expect(screen.getByRole("heading", { name: "Export override no longer applies" })).toBeInTheDocument();
    expect(screen.queryByTestId("review-copy-txt")).not.toBeInTheDocument();
    expect(screen.queryByTestId("override-open")).not.toBeInTheDocument();
  });
});
