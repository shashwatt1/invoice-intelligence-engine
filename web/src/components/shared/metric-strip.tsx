import type { LucideIcon } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import type { Tone } from "@/lib/status";
import { cn } from "@/lib/utils";

const TONE_TEXT: Record<Tone, string> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
  neutral: "text-foreground",
};

export type MetricStripItem = {
  key: string;
  label: string;
  value: ReactNode;
  /** One short phrase under the number — real state only, never a trend we do not have. */
  hint?: ReactNode;
  icon?: LucideIcon;
  tone?: Tone;
  onClick?: () => void;
};

/**
 * A row of figures set directly on the canvas: hairlines between them,
 * no boxes. Each figure may open the list it counts.
 */
export function MetricStrip({ items, className }: { items: MetricStripItem[]; className?: string }) {
  return (
    <div
      className={cn("grid grid-cols-[repeat(var(--cols),minmax(0,1fr))] border-y border-border max-xl:grid-cols-3 max-md:grid-cols-2", className)}
      style={{ "--cols": items.length } as CSSProperties}
      data-testid="metric-strip"
    >
      {items.map(({ key, label, value, hint, icon: Icon, tone = "neutral", onClick }, index) => {
        const Cell = onClick ? "button" : "div";
        return (
          <Cell
            key={key}
            type={onClick ? "button" : undefined}
            onClick={onClick}
            className={cn(
              "flex min-w-0 flex-col gap-1.5 py-4 pr-4 text-left",
              index > 0 && "border-l border-border pl-4 max-xl:[&:nth-child(3n+1)]:border-l-0 max-xl:[&:nth-child(3n+1)]:pl-0 max-md:[&:nth-child(2n+1)]:border-l-0 max-md:[&:nth-child(2n+1)]:pl-0",
              onClick && "cursor-pointer transition-colors hover:[&_.t-label]:text-foreground",
            )}
            data-testid="metric"
          >
            <div className="flex items-center gap-1.5">
              {Icon ? <Icon className="size-3.5 shrink-0 text-muted-foreground/70" aria-hidden /> : null}
              <span className="t-label truncate transition-colors">{label}</span>
            </div>
            <div className={cn("t-metric", TONE_TEXT[tone])}>{value}</div>
            {hint ? <div className="t-meta line-clamp-1 leading-snug">{hint}</div> : null}
          </Cell>
        );
      })}
    </div>
  );
}

export function MetricStripSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-[repeat(var(--cols),minmax(0,1fr))] border-y border-border max-xl:grid-cols-3 max-md:grid-cols-2" style={{ "--cols": count } as CSSProperties}>
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className={cn("flex flex-col gap-2.5 py-4 pr-4", index > 0 && "border-l border-border pl-4")}>
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-7 w-14" />
          <Skeleton className="h-3 w-24" />
        </div>
      ))}
    </div>
  );
}
