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
  label = "Extraction confidence",
  className,
}: {
  score: number | null | undefined;
  label?: string;
  className?: string;
}) {
  if (score === null || score === undefined) {
    return <span className="t-meta">No confidence recorded</span>;
  }
  const pct = Math.max(0, Math.min(1, score));
  return (
    <div className={cn("min-w-40", className)}>
      <div className="flex items-baseline justify-between gap-4">
        <span className="t-eyebrow">{label}</span>
        <span className={cn("text-[0.95rem] font-semibold tabular-nums", toneFor(pct))}>{formatPercent(pct)}</span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface-3" role="meter" aria-valuenow={Math.round(pct * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className={cn("h-full rounded-full transition-[width] duration-500", barToneFor(pct))} style={{ width: `${pct * 100}%` }} />
      </div>
    </div>
  );
}

/** Compact inline confidence used in table rows: ring + percent. */
export function ConfidenceInline({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined) return <span className="text-muted-foreground">—</span>;
  const pct = Math.max(0, Math.min(1, score));
  const r = 6;
  const c = 2 * Math.PI * r;
  return (
    <span className="inline-flex items-center gap-1.5" title={`Extraction confidence ${formatPercent(pct)}`}>
      <svg viewBox="0 0 16 16" className="size-4 -rotate-90" aria-hidden>
        <circle cx="8" cy="8" r={r} fill="none" stroke="currentColor" strokeWidth="2" className="text-surface-3" />
        <circle cx="8" cy="8" r={r} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                strokeDasharray={`${c * pct} ${c}`} className={toneFor(pct).replace("text-", "text-")} />
      </svg>
      <span className={cn("text-[0.78rem] font-semibold tabular-nums", toneFor(pct))}>{formatPercent(pct, 0)}</span>
    </span>
  );
}
