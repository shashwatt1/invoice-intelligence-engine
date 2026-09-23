import type { DashboardData, DocumentStatus } from "@/api/types";
import { STATUS_META } from "@/lib/status";

const TONE_COLORS: Record<string, string> = {
  success: "var(--brand)",
  warning: "var(--warning)",
  danger: "var(--danger)",
  info: "var(--chart-3)",
  neutral: "var(--chart-5)",
};

/**
 * Where every document stands — one proportional bar and its legend,
 * set on the canvas. Answers "what is the backlog made of?" without a
 * chart frame.
 */
export function StatusChart({ data }: { data: DashboardData }) {
  const entries = Object.entries(data.status_breakdown) as [DocumentStatus, number][];
  const rows = entries
    .filter(([, count]) => count > 0)
    .map(([status, count]) => ({
      key: status,
      name: STATUS_META[status]?.label ?? status,
      value: count,
      color: TONE_COLORS[STATUS_META[status]?.tone ?? "neutral"],
    }));
  const total = rows.reduce((sum, row) => sum + row.value, 0) || 1;

  return (
    <div data-testid="status-breakdown">
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-surface-3" role="img" aria-label="Documents by state">
        {rows.map((row) => (
          <span key={row.key} style={{ width: `${(row.value / total) * 100}%`, background: row.color }} title={`${row.name}: ${row.value}`} />
        ))}
      </div>
      <ul className="mt-3 divide-y divide-border">
        {rows.map((row) => (
          <li key={row.key} className="flex items-center justify-between gap-3 py-1.5">
            <span className="flex items-center gap-2 text-[0.8rem] text-foreground">
              <span className="size-2 rounded-[2px]" style={{ background: row.color }} aria-hidden />
              {row.name}
            </span>
            <span className="text-[0.82rem] font-semibold tabular-nums">{row.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
