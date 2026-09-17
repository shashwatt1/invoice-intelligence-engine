import { Check, CircleDashed, Loader2, Minus, TriangleAlert, X } from "lucide-react";

import type { InvoiceDetail } from "@/api/types";
import { workflowSteps } from "@/lib/workflow";
import { cn } from "@/lib/utils";

import type { StepState } from "@/lib/workflow";

const ICON: Record<StepState, React.ReactNode> = {
  done: <Check className="size-3" strokeWidth={3} />,
  warn: <TriangleAlert className="size-3" />,
  blocked: <X className="size-3" strokeWidth={3} />,
  active: <Loader2 className="size-3 animate-spin" />,
  idle: <Minus className="size-3" />,
};
const RING: Record<StepState, string> = {
  done: "bg-success text-white",
  warn: "bg-warning text-white",
  blocked: "bg-danger text-white",
  active: "bg-info text-white",
  idle: "bg-surface-3 text-muted-foreground",
};

/** OCR → Extraction → Validation → Master data → EDI, from the invoice's real state. */
export function WorkflowTimeline({ detail }: { detail: InvoiceDetail }) {
  const steps = workflowSteps(detail);
  return (
    <ol className="surface flex items-stretch overflow-hidden" aria-label="Invoice workflow" data-testid="workflow-timeline">
      {steps.map((step, index) => (
        <li key={step.key} className={cn("relative flex min-w-0 flex-1 items-center gap-3 px-4 py-3", index > 0 && "border-l")}>
          <span className={cn("flex size-6 shrink-0 items-center justify-center rounded-full", RING[step.state])} aria-hidden>
            {ICON[step.state]}
          </span>
          <span className="min-w-0">
            <span className="t-eyebrow block">{step.label}</span>
            <span className={cn("block truncate text-[0.78rem] font-medium", step.state === "blocked" && "text-danger", step.state === "warn" && "text-warning", step.state === "idle" && "text-muted-foreground")}
                  title={step.detail}>
              {step.detail}
            </span>
          </span>
        </li>
      ))}
      <li className="sr-only"><CircleDashed /></li>
    </ol>
  );
}
