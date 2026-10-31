import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/use-api", () => ({
  useInvoiceExport: () => ({ data: { invoice_id: "inv-1" }, isPending: false, isError: false, error: null, refetch: vi.fn() }),
}));

const { StructuredOutput } = await import("./structured-output");

function show(criticalBlocked: boolean) {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <StructuredOutput invoiceId="inv-1" pdiExportAllowed={!criticalBlocked} pdiExportRequiresConfirmation={false}
                        pdiExportBlockedReason={null} criticalBlocked={criticalBlocked} enabled />
    </QueryClientProvider>,
  );
}

describe("the Export menu follows the server's single export rule", () => {
  it("while critical checks fail, TXT and CSV are disabled and only labelled review copies are offered", async () => {
    const user = userEvent.setup();
    show(true);
    await user.click(screen.getByRole("button", { name: /Export/ }));
    expect(screen.getByRole("menuitem", { name: /Download TXT/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: /Download CSV/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: /Review copy TXT \(not for import\)/ })).not.toHaveAttribute("aria-disabled");
    expect(screen.getByRole("menuitem", { name: /Review copy CSV \(not for import\)/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Download JSON/ })).not.toHaveAttribute("aria-disabled");
  });

  it("when nothing blocks, the normal downloads are enabled and there is no review copy", async () => {
    const user = userEvent.setup();
    show(false);
    await user.click(screen.getByRole("button", { name: /Export/ }));
    expect(screen.getByRole("menuitem", { name: /Download TXT/ })).not.toHaveAttribute("aria-disabled");
    expect(screen.getByRole("menuitem", { name: /Download CSV/ })).not.toHaveAttribute("aria-disabled");
    expect(screen.queryByRole("menuitem", { name: /Review copy/ })).not.toBeInTheDocument();
  });
});
