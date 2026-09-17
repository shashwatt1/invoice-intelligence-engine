import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { InvoiceDetail } from "@/api/types";
import { WorkflowTimeline } from "@/components/invoice/workflow-timeline";
import { workflowSteps } from "@/lib/workflow";

import { PipelineStrip } from "./pipeline-strip";
import { StatusBadge, StatusPill } from "./status-badge";

/** The status system: icon + word + tone, meaning as a tooltip — never colour alone. */
describe("StatusPill / StatusBadge", () => {
  it("renders an icon, the word, and the meaning as the accessible name", () => {
    render(<StatusBadge status="REVIEW_REQUIRED" />);
    const pill = screen.getByLabelText(/Needs review: A check failed/);
    expect(pill).toHaveTextContent("Needs review");
    expect(pill.querySelector("svg")).not.toBeNull();
    expect(pill).toHaveAttribute("title", expect.stringContaining("a person decides"));
  });

  it("spins for in-flight states and falls back to a neutral label for unknown ones", () => {
    const { container } = render(<StatusBadge status="AI_PROCESSING" />);
    expect(container.querySelector("svg.animate-spin")).not.toBeNull();
    render(<StatusBadge status="SOMETHING_NEW" />);
    expect(screen.getByText("Something New")).toBeInTheDocument();
  });

  it("StatusPill takes an explicit tone", () => {
    render(<StatusPill tone="danger" label="Blocked" meaning="No mapping" />);
    expect(screen.getByLabelText("Blocked: No mapping")).toBeInTheDocument();
  });
});

describe("PipelineStrip", () => {
  it("shows real counts and an explicit 'not available' when the API has none", () => {
    render(<PipelineStrip stages={[
      { key: "u", label: "Upload", value: 8, hint: "received", state: "done" },
      { key: "e", label: "EDI", value: null, hint: "per invoice" },
    ]} />);
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("not available")).toBeInTheDocument();
    expect(screen.getByLabelText("complete")).toBeInTheDocument();
  });
});

describe("WorkflowTimeline", () => {
  const base = {
    ocr_text: "text", llm_metadata: { model: "gpt-4o", total_tokens: 1, latency_ms: 1 }, line_items: [{}, {}],
    photos: [], source_type: "ocr", status: "VALIDATED", validation_report: { summary: { failed: 0 } },
    case_mappings: [{ mapped: true }, { mapped: true }], review: { pending: 0 }, store_pending: false, store: { id: "s" },
    pdi_export_allowed: true, pdi_export_requires_confirmation: false, pdi_export_blocked_reason: null,
  } as unknown as InvoiceDetail;

  it("derives every step from the invoice's recorded state", () => {
    const steps = workflowSteps(base);
    expect(steps.map((s) => [s.key, s.state])).toEqual([
      ["ocr", "done"], ["extract", "done"], ["validate", "done"], ["master", "done"], ["edi", "done"],
    ]);
    expect(steps[3].detail).toBe("2/2 mappings approved");
    expect(steps[4].detail).toBe("ready");
  });

  it("shows the blocker verbatim and marks pending master data", () => {
    const blocked = {
      ...base, status: "REVIEW_REQUIRED", validation_report: { summary: { failed: 2 } },
      review: { pending: 3 }, pdi_export_allowed: false, pdi_export_blocked_reason: "3 products need a units-per-case mapping",
    } as unknown as InvoiceDetail;
    render(<WorkflowTimeline detail={blocked} />);
    expect(screen.getByText("2 checks failed")).toBeInTheDocument();
    expect(screen.getByText("3 pending approval")).toBeInTheDocument();
    expect(screen.getByText("3 products need a units-per-case mapping")).toBeInTheDocument();
  });

  it("waits for a store before master data", () => {
    const pending = { ...base, store_pending: true, store: null } as unknown as InvoiceDetail;
    expect(workflowSteps(pending)[3]).toMatchObject({ state: "idle", detail: "assign the store first" });
  });
});
