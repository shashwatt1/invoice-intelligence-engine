import { Check, ChevronRight, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import type { Tone } from "@/lib/status";
import { cn } from "@/lib/utils";

export type PipelineStage = {
  key: string;
  label: string;
  /** The number or state shown under the label; null = not available. */
  value: ReactNode | null;
  hint?: string;
  tone?: Tone;
  state?: "done" | "active" | "blocked" | "idle";
};

const DOT: Record<Tone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
  neutral: "bg-muted-foreground/40",
};

/**
 * The processing pipeline as a horizontal strip: UPLOAD → OCR →
 * EXTRACTION → VALIDATION → MASTER DATA → EDI, each with the real count
 * or state, or an explicit "not available" when the API has none.
 */
export function PipelineStrip({ stages, dense = false }: { stages: PipelineStage[]; dense?: boolean }) {
  return (
    <ol className={cn("grid gap-px overflow-hidden rounded-lg bg-border ring-1 ring-foreground/8", `grid-cols-${stages.length}`)}
        style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }}
        aria-label="Processing pipeline">
      {stages.map((stage, index) => {
        const tone = stage.tone ?? "neutral";
        return (
          <li key={stage.key} className={cn("relative flex min-w-0 flex-col bg-card", dense ? "px-3 py-2.5" : "px-4 py-3")}
              data-state={stage.state ?? "idle"}>
            <div className="flex items-center gap-1.5">
              <span className={cn("t-eyebrow truncate")}>{stage.label}</span>
              {stage.state === "done" ? <Check className="size-3 text-success" aria-label="complete" /> : null}
              {stage.state === "active" ? <Loader2 className="size-3 animate-spin text-info" aria-label="in progress" /> : null}
              {index < stages.length - 1 ? (
                <ChevronRight className="absolute top-1/2 -right-1.5 z-10 size-3 -translate-y-1/2 text-muted-foreground/50" aria-hidden />
              ) : null}
            </div>
            <div className={cn("mt-1 flex items-baseline gap-2", dense ? "text-[1rem]" : "text-[1.15rem]", "leading-none font-semibold tabular-nums")}>
              {stage.value === null ? <span className="text-[0.78rem] font-normal text-muted-foreground">not available</span> : stage.value}
            </div>
            {stage.hint ? (
              <div className="t-meta mt-1 flex items-center gap-1.5 truncate">
                <span className={cn("size-1.5 shrink-0 rounded-full", DOT[tone])} aria-hidden />
                <span className="truncate">{stage.hint}</span>
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
