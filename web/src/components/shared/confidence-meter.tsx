import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

function toneFor(score: number): string {
  if (score >= 0.85) return "text-success";
  if (score >= 0.6) return "text-warning";
  return "text-danger";
}

function barToneFor(score: number): string {
  if (score >= 0.85) return "bg-success";
  if (score >= 0.6) return "bg-warning";
  return "bg-danger";
}

/** Composite confidence with a tinted fill bar. */
export function ConfidenceMeter({
  score,
  label = "Composite confidence",
  className,
}: {
  score: number | null | undefined;
  label?: string;
  className?: string;
}) {
  if (score === null || score === undefined) {
    return <span className="text-[0.78rem] text-muted-foreground">No confidence recorded</span>;
  }
  const pct = Math.max(0, Math.min(1, score));
  return (
    <div className={cn("min-w-40", className)}>
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-[0.68rem] font-semibold tracking-wider text-muted-foreground uppercase">
          {label}
        </span>
        <span className={cn("text-[0.95rem] font-bold tabular-nums", toneFor(pct))}>
          {formatPercent(pct)}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-secondary">
        <div
          className={cn("h-full rounded-full transition-all duration-500", barToneFor(pct))}
          style={{ width: `${pct * 100}%` }}
        />
      </div>
    </div>
  );
}

/** Compact inline confidence used in table rows. */
export function ConfidenceInline({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined)
    return <span className="text-muted-foreground">—</span>;
  const pct = Math.max(0, Math.min(1, score));
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1 w-12 overflow-hidden rounded-full bg-secondary">
        <span
          className={cn("block h-full rounded-full", barToneFor(pct))}
          style={{ width: `${pct * 100}%` }}
        />
      </span>
      <span className={cn("text-[0.78rem] font-semibold tabular-nums", toneFor(pct))}>
        {formatPercent(pct, 0)}
      </span>
    </span>
  );
}
