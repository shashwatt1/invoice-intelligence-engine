import { Check, Loader2, Minus, TriangleAlert, X } from "lucide-react";
import type { ReactNode } from "react";

import type { InvoiceDetail } from "@/api/types";
import { workflowSteps } from "@/lib/workflow";
import { cn } from "@/lib/utils";

import type { StepState } from "@/lib/workflow";

const ICON: Record<StepState, ReactNode> = {
  done: <Check className="size-3" strokeWidth={3} />,
  warn: <TriangleAlert className="size-3" />,
  blocked: <X className="size-3" strokeWidth={3} />,
  active: <Loader2 className="size-3 animate-spin" />,
  idle: <Minus className="size-3" />,
};
const NODE: Record<StepState, string> = {
  done: "border-brand bg-brand text-white",
  warn: "border-warning bg-warning text-white",
  blocked: "border-danger bg-danger text-white",
  active: "border-brand bg-card text-brand",
  idle: "border-border bg-card text-muted-foreground",
};
const DETAIL: Record<StepState, string> = {
  done: "text-foreground",
  warn: "text-warning",
  blocked: "text-danger",
  active: "text-brand",
  idle: "text-muted-foreground",
};

/**
 * OCR → Extraction → Validation → Master data → EDI as one rail, from the
 * invoice's real state: a node per step, the line between them, the
 * outcome beneath. No cells.
 */
export function WorkflowTimeline({ detail }: { detail: InvoiceDetail }) {
  const steps = workflowSteps(detail);
  return (
    <ol className="grid gap-3" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}
        aria-label="Invoice workflow" data-testid="workflow-timeline">
      {steps.map((step, index) => {
        const last = index === steps.length - 1;
        const reached = step.state !== "idle";
        return (
          <li key={step.key} className="relative min-w-0" data-state={step.state}>
            {!last ? <span className={cn("absolute top-[9px] right-0 left-[18px] h-px", reached ? "bg-brand/50" : "bg-border")} aria-hidden /> : null}
            <span className={cn("relative z-10 flex size-[19px] items-center justify-center rounded-full border-[1.5px]", NODE[step.state])} aria-hidden>
              {ICON[step.state]}
            </span>
            <div className="mt-2 pr-3">
              <div className="t-label">{step.label}</div>
              <div className={cn("mt-0.5 truncate text-[0.82rem] font-medium", DETAIL[step.state])} title={step.detail}>{step.detail}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
