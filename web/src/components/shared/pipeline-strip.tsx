import { Check, Loader2 } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

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

type State = NonNullable<PipelineStage["state"]>;
type Variant = "light" | "dark";

const NODE: Record<Variant, Record<State, string>> = {
  light: {
    done: "border-brand bg-brand text-white",
    active: "border-brand bg-card text-brand",
    blocked: "border-warning bg-warning text-white",
    idle: "border-border bg-card text-muted-foreground",
  },
  dark: {
    done: "border-brand-cream bg-brand-cream text-brand-deep",
    active: "border-brand-cream bg-transparent text-brand-cream",
    blocked: "border-amber-300 bg-amber-300 text-brand-deep",
    idle: "border-white/25 bg-transparent text-white/40",
  },
};

const VALUE_TONE: Record<Variant, Record<Tone, string>> = {
  light: { success: "text-foreground", warning: "text-warning", danger: "text-danger", info: "text-brand", neutral: "text-foreground" },
  dark: { success: "text-white", warning: "text-amber-300", danger: "text-red-300", info: "text-white", neutral: "text-white" },
};

/**
 * The processing pipeline as a rail: one line, a node per stage, the real
 * count beneath each — or an explicit "not available" when the API has
 * none. The line is the composition; there are no cells.
 */
export function PipelineStrip({ stages, dense = false, variant = "light" }: { stages: PipelineStage[]; dense?: boolean; variant?: Variant }) {
  const dark = variant === "dark";
  return (
    <ol className={cn("relative grid grid-cols-[repeat(var(--cols),minmax(0,1fr))] max-md:grid-cols-3 max-md:gap-y-5", dense ? "gap-2" : "gap-3")}
        style={{ "--cols": stages.length } as CSSProperties}
        aria-label="Processing pipeline" data-testid="process-rail">
      {stages.map((stage, index) => {
        const state = stage.state ?? "idle";
        const tone = stage.tone ?? "neutral";
        const last = index === stages.length - 1;
        return (
          <li key={stage.key} className="relative min-w-0" data-state={state}>
            {/* The rail segment leading to the next node. */}
            {!last ? (
              <span
                className={cn("absolute top-[9px] right-0 left-[18px] h-px", state === "done" || state === "active" ? (dark ? "bg-brand-cream/40" : "bg-brand/50") : (dark ? "bg-white/15" : "bg-border"))}
                aria-hidden
              />
            ) : null}
            <span className={cn("relative z-10 flex size-[19px] items-center justify-center rounded-full border-[1.5px]", NODE[variant][state])} aria-hidden={state === "idle" || state === "blocked"}>
              {state === "done" ? <Check className="size-3" strokeWidth={2.6} aria-label="complete" /> : null}
              {state === "active" ? <Loader2 className="size-3 animate-spin" aria-label="in progress" /> : null}
              {state === "blocked" ? <span className={cn("size-1.5 rounded-full", dark ? "bg-brand-deep" : "bg-white")} /> : null}
            </span>
            <div className={cn("mt-2.5 pr-3", dense && "mt-2")}>
              <div className={cn("truncate text-[0.74rem] font-medium", dark ? "text-brand-cream/60" : "text-muted-foreground")}>{stage.label}</div>
              <div className={cn("mt-0.5 leading-none font-semibold tracking-[-0.02em] tabular-nums", dense ? "text-[1.05rem]" : "text-[1.35rem]", VALUE_TONE[variant][tone])}>
                {stage.value === null ? <span className={cn("text-[0.78rem] font-normal tracking-normal", dark ? "text-brand-cream/50" : "text-muted-foreground")}>not available</span> : stage.value}
              </div>
              {stage.hint ? <div className={cn("mt-1 truncate text-[0.72rem]", dark ? "text-brand-cream/50" : "text-muted-foreground")}>{stage.hint}</div> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
