import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentStatusData } from "@/api/types";

import { DocumentActions } from "./document-actions";

vi.mock("@/api/endpoints", () => ({ stopDocument: vi.fn(), moveDocumentToBin: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import * as api from "@/api/endpoints";
import { toast } from "sonner";

const BASE: DocumentStatusData = {
  document_id: "doc-1", filename: "invoice.pdf", status: "OCR_IN_PROGRESS", is_terminal: false,
  source_type: null, store: null, awaiting_store_confirmation: false, store_candidates: [],
  invoice_id: null, photos: [], error: null, stages: [],
  created_at: "2026-09-21T00:00:00Z", updated_at: "2026-09-21T00:00:00Z",
};

function Where() {
  const location = useLocation();
  return <output data-testid="where">{location.pathname}</output>;
}

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/documents/doc-1"]}>
        {node}
        <Routes><Route path="*" element={<Where />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DocumentActions — STOP", () => {
  it("acts as the signed-in session, with no identity field to fill in, and navigates home only after the backend call succeeds", async () => {
    const user = userEvent.setup();
    vi.mocked(api.stopDocument).mockResolvedValue({ ...BASE, status: "STOPPED", is_terminal: true });
    wrap(<DocumentActions status={BASE} />);

    // P3: no actor/role is collected or sent — identity is the session.
    expect(screen.queryByLabelText("Your name")).toBeNull();

    await user.click(screen.getByTestId("stop-document"));
    await waitFor(() => expect(api.stopDocument).toHaveBeenCalledWith("doc-1"));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/"));
    expect(toast.success).toHaveBeenCalled();
  });

  it("does not navigate away when the backend refuses (e.g. 403 on someone else's document)", async () => {
    const user = userEvent.setup();
    vi.mocked(api.stopDocument).mockRejectedValue(new Error("You may only stop documents you uploaded yourself."));
    wrap(<DocumentActions status={BASE} />);

    await user.click(screen.getByTestId("stop-document"));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.getByTestId("where")).toHaveTextContent("/documents/doc-1");
  });

  it("is not offered once the document is already terminal", () => {
    wrap(<DocumentActions status={{ ...BASE, status: "COMPLETED", is_terminal: true }} />);
    expect(screen.queryByTestId("stop-document")).toBeNull();
  });

  it("renders nothing once the document has already been withdrawn", () => {
    wrap(<DocumentActions status={{ ...BASE, status: "STOPPED", is_terminal: true }} />);
    expect(screen.queryByTestId("document-actions")).toBeNull();
  });
});

describe("DocumentActions — MOVE TO BIN", () => {
  it("requires confirmation before calling the backend", async () => {
    const user = userEvent.setup();
    vi.mocked(api.moveDocumentToBin).mockResolvedValue({ ...BASE, status: "BINNED", is_terminal: true });
    wrap(<DocumentActions status={{ ...BASE, status: "COMPLETED", is_terminal: true }} />);

    await user.click(screen.getByTestId("move-to-bin"));
    expect(api.moveDocumentToBin).not.toHaveBeenCalled();
    expect(screen.getByText(/move this document to the bin/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^move to bin$/i }));
    await waitFor(() => expect(api.moveDocumentToBin).toHaveBeenCalledWith("doc-1"));
  });

  it("is offered even on a completed document", () => {
    wrap(<DocumentActions status={{ ...BASE, status: "COMPLETED", is_terminal: true }} />);
    expect(screen.getByTestId("move-to-bin")).toBeEnabled();
  });
});
