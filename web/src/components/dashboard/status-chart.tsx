import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import type { DashboardData, DocumentStatus } from "@/api/types";
import { SectionHeader } from "@/components/layout/page-header";
import { STATUS_META } from "@/lib/status";

const TONE_COLORS: Record<string, string> = {
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--danger)",
  info: "var(--info)",
  neutral: "var(--muted-foreground)",
};

/** Where every document stands right now — answers "what is the backlog made of?" */
export function StatusChart({ data }: { data: DashboardData }) {
  const entries = Object.entries(data.status_breakdown) as [DocumentStatus, number][];
  const chartData = entries
    .filter(([, count]) => count > 0)
    .map(([status, count]) => ({
      name: STATUS_META[status]?.label ?? status,
      value: count,
      color: TONE_COLORS[STATUS_META[status]?.tone ?? "neutral"],
    }));

  return (
    <div className="surface flex h-full flex-col overflow-hidden">
      <SectionHeader title="Where invoices stand" description="Every document by its current state" />
      <div className="flex flex-1 items-center gap-5 border-t px-5 py-4">
        <div className="relative h-32 w-32 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={chartData} dataKey="value" innerRadius={42} outerRadius={60} paddingAngle={2} strokeWidth={0} isAnimationActive={false}>
                {chartData.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
              </Pie>
              <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid var(--border)", fontSize: "0.76rem", boxShadow: "var(--shadow-md)" }} />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-lg font-semibold tabular-nums">{data.total_documents}</span>
            <span className="t-eyebrow">total</span>
          </div>
        </div>
        <ul className="min-w-0 flex-1 space-y-1.5">
          {chartData.map((entry) => (
            <li key={entry.name} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-[0.78rem] text-muted-foreground">
                <span className="size-2 rounded-full" style={{ background: entry.color }} aria-hidden />
                {entry.name}
              </span>
              <span className="text-[0.8rem] font-semibold tabular-nums">{entry.value}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
