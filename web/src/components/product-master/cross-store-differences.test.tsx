import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CrossStoreDifference } from "@/api/types";

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (...a: unknown[]) => toastSuccess(...a),
                                    error: (...a: unknown[]) => toastError(...a) } }));

const listDifferences = vi.fn();
const resolveDifference = vi.fn();
vi.mock("@/api/endpoints", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/endpoints")>()),
  listCrossStoreDifferences: () => listDifferences(),
  resolveCrossStoreDifference: (...a: unknown[]) => resolveDifference(...a),
}));

const { CrossStoreDifferences } = await import("./cross-store-differences");

// The Michelob Ultra C-18 shape the audit found: one store says 1, another 18.
const MICHELOB: CrossStoreDifference = {
  item_code: "01820096721", shared_units: null, distinct_values: [1, 18],
  stores: [
    { store_id: "s-rcm", store: "LG - RCM", units: 1, source: "store mapping" },
    { store_id: "s-wolf", store: "PB Wolf", units: 18, source: "store mapping" },
  ],
  overrides: [],
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><CrossStoreDifferences /></QueryClientProvider>);
}

describe("CrossStoreDifferences", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listDifferences.mockResolvedValue([MICHELOB]);
    resolveDifference.mockResolvedValue({ item_code: MICHELOB.item_code, units: 1, shared_mapping_id: "m1",
                                          adopted_store_ids: [], kept_store_ids: ["s-wolf"] });
  });

  it("renders nothing when approved values agree everywhere", async () => {
    listDifferences.mockResolvedValue([]);
    renderPanel();
    await waitFor(() => expect(listDifferences).toHaveBeenCalled());
    expect(screen.queryByTestId("cross-store-differences")).not.toBeInTheDocument();
  });

  it("lists every store's value and never settles without a chosen value and a note", async () => {
    const user = userEvent.setup();
    renderPanel();
    expect(await screen.findByText(/LG - RCM: 1 · PB Wolf: 18/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Settle" }));
    const save = screen.getByTestId("difference-save");
    expect(save).toBeDisabled();
    await user.click(screen.getByLabelText("1"));
    expect(save).toBeDisabled();                       // a note is still required
    await user.type(screen.getByLabelText(/Why this value/), "distributor sheet: the 18-pack is one item");
    expect(save).toBeEnabled();
  });

  it("settles with the chosen value, keeping only the stores the reviewer ticked", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: "Settle" }));
    await user.click(screen.getByLabelText("1"));
    await user.click(screen.getByLabelText("Keep PB Wolf's value 18"));
    await user.type(screen.getByLabelText(/Why this value/), "sells singles");
    await user.click(screen.getByTestId("difference-save"));
    await waitFor(() => expect(resolveDifference).toHaveBeenCalledWith(
      "01820096721", { units: 1, keep_store_ids: ["s-wolf"], note: "sells singles" }));
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("reports a refusal and claims nothing", async () => {
    const user = userEvent.setup();
    resolveDifference.mockRejectedValue(new Error("An approved shared value already says 12."));
    renderPanel();
    await user.click(await screen.findByRole("button", { name: "Settle" }));
    await user.click(screen.getByLabelText("18"));
    await user.type(screen.getByLabelText(/Why this value/), "x");
    await user.click(screen.getByTestId("difference-save"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("An approved shared value already says 12."));
    expect(toastSuccess).not.toHaveBeenCalled();
  });
});
