import { AnimatePresence, motion } from "framer-motion";
import { Check, Loader2, X } from "lucide-react";

import type { DocumentStatusData } from "@/api/types";
import { ACTIVE_STEP_BY_STATUS, PIPELINE_STEPS } from "@/lib/status";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

type StepState = "done" | "active" | "failed" | "pending";

function stepStates(status: DocumentStatusData): StepState[] {
  const completed = new Set(
    status.stages.filter((entry) => entry.status === "SUCCESS").map((entry) => entry.stage),
  );
  const failedStage = status.error?.stage ?? null;
  const activeStage = status.is_terminal ? null : ACTIVE_STEP_BY_STATUS[status.status];

  return PIPELINE_STEPS.map(({ stage }) => {
    if (stage === failedStage) return "failed";
    if (completed.has(stage)) return "done";
    if (failedStage) return "pending";
    if (stage === activeStage) return "active";
    return "pending";
  });
}

/**
 * Live pipeline timeline. Every state shown here is a real persisted
 * stage transition read from the status endpoint — never simulated.
 */
export function ProcessingTimeline({ status }: { status: DocumentStatusData | null }) {
  // Before a run starts the same rail shows the journey ahead, every step pending.
  const states: StepState[] = status ? stepStates(status) : PIPELINE_STEPS.map(() => "pending");
  const durations = new Map(
    (status?.stages ?? [])
      .filter((entry) => entry.duration_ms !== null)
      .map((entry) => [entry.stage, entry.duration_ms as number]),
  );

  return (
    <ol className="relative" aria-label={status ? "Processing stages" : "What happens next"} data-testid="processing-timeline">
      {PIPELINE_STEPS.map(({ stage, title, description }, index) => {
        const state = states[index];
        const duration = durations.get(stage);
        const isLast = index === PIPELINE_STEPS.length - 1;

        return (
          <li key={stage} className="relative flex gap-4 pb-5 last:pb-0">
            {/* Connector */}
            {!isLast && (
              <span
                className={cn(
                  "absolute top-6 left-[11px] h-[calc(100%-1.5rem)] w-px transition-colors duration-500",
                  state === "done" ? "bg-brand/50" : "bg-border",
                )}
              />
            )}

            {/* Marker */}
            <span
              className={cn(
                "relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full border-[1.5px] bg-card transition-all duration-300",
                state === "done" && "border-brand bg-brand text-white",
                state === "active" && "border-brand text-brand",
                state === "failed" && "border-danger bg-danger text-white",
                state === "pending" && "border-border text-muted-foreground",
              )}
            >
              <AnimatePresence mode="wait" initial={false}>
                {state === "done" ? (
                  <motion.span
                    key="done"
                    initial={{ scale: 0.4, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ type: "spring", stiffness: 500, damping: 25 }}
                  >
                    <Check className="size-3.5" strokeWidth={3} />
                  </motion.span>
                ) : state === "active" ? (
                  <motion.span key="active" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                    <Loader2 className="size-3.5 animate-spin" strokeWidth={2.5} />
                  </motion.span>
                ) : state === "failed" ? (
                  <motion.span
                    key="failed"
                    initial={{ scale: 0.4, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                  >
                    <X className="size-3.5" strokeWidth={3} />
                  </motion.span>
                ) : (
                  <span key="pending" className="text-[0.64rem] font-semibold tabular-nums">
                    {index + 1}
                  </span>
                )}
              </AnimatePresence>
              {state === "active" && (
                <span className="absolute inset-0 animate-ping rounded-full border border-brand opacity-30 motion-reduce:hidden" />
              )}
            </span>

            {/* Copy */}
            <div className="min-w-0 pt-0.5">
              <div
                className={cn(
                  "text-[0.86rem] font-medium transition-colors",
                  state === "done" && "font-semibold",
                  state === "active" && "font-semibold text-brand",
                  state === "pending" && "text-muted-foreground",
                  state === "failed" && "font-semibold text-danger",
                )}
              >
                {title}
                {state === "done" && duration !== undefined ? (
                  <span className="ml-2 text-[0.7rem] font-medium text-muted-foreground tabular-nums">
                    {formatDuration(duration)}
                  </span>
                ) : null}
              </div>
              <div className="text-[0.75rem] text-muted-foreground">
                {state === "failed" && status?.error?.message ? (
                  <span className="text-danger">{status?.error?.message}</span>
                ) : (
                  description
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
