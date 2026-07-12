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
export function ProcessingTimeline({ status }: { status: DocumentStatusData }) {
  const states = stepStates(status);
  const durations = new Map(
    status.stages
      .filter((entry) => entry.duration_ms !== null)
      .map((entry) => [entry.stage, entry.duration_ms as number]),
  );

  return (
    <ol className="relative">
      {PIPELINE_STEPS.map(({ stage, title, description }, index) => {
        const state = states[index];
        const duration = durations.get(stage);
        const isLast = index === PIPELINE_STEPS.length - 1;

        return (
          <li key={stage} className="relative flex gap-3.5 pb-6 last:pb-0">
            {/* Connector */}
            {!isLast && (
              <span
                className={cn(
                  "absolute top-7 left-[13px] h-[calc(100%-1.75rem)] w-0.5 rounded-full transition-colors duration-500",
                  state === "done" ? "bg-success" : "bg-border",
                )}
              />
            )}

            {/* Marker */}
            <span
              className={cn(
                "relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border-2 bg-card transition-all duration-300",
                state === "done" && "border-success bg-success text-white",
                state === "active" && "border-primary text-primary",
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
                  <span key="pending" className="text-[0.68rem] font-bold">
                    {index + 1}
                  </span>
                )}
              </AnimatePresence>
              {state === "active" && (
                <span className="absolute inset-0 animate-ping rounded-full border-2 border-primary opacity-30" />
              )}
            </span>

            {/* Copy */}
            <div className="min-w-0 pt-0.5">
              <div
                className={cn(
                  "text-[0.85rem] font-semibold transition-colors",
                  state === "pending" && "text-muted-foreground",
                  state === "failed" && "text-danger",
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
                {state === "failed" && status.error?.message ? (
                  <span className="text-danger">{status.error.message}</span>
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
