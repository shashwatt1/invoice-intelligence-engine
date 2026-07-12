import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import type { DashboardData, DocumentStatus } from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { STATUS_META } from "@/lib/status";

const TONE_COLORS: Record<string, string> = {
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--danger)",
  info: "var(--info)",
  neutral: "var(--muted-foreground)",
};

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
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-[0.95rem]">Pipeline health</CardTitle>
      </CardHeader>
      <CardContent className="flex items-center gap-5">
        <div className="relative h-36 w-36 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={chartData}
                dataKey="value"
                innerRadius={44}
                outerRadius={64}
                paddingAngle={3}
                strokeWidth={0}
              >
                {chartData.map((entry) => (
                  <Cell key={entry.name} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: "1px solid var(--border)",
                  fontSize: "0.78rem",
                  boxShadow: "0 4px 12px rgb(0 0 0 / 0.06)",
                }}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-xl font-bold tabular-nums">{data.total_documents}</span>
            <span className="text-[0.65rem] tracking-wide text-muted-foreground uppercase">
              documents
            </span>
          </div>
        </div>
        <div className="min-w-0 flex-1 space-y-1.5">
          {chartData.map((entry) => (
            <div key={entry.name} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-[0.8rem] text-muted-foreground">
                <span className="size-2 rounded-full" style={{ background: entry.color }} />
                {entry.name}
              </span>
              <span className="text-[0.8rem] font-semibold tabular-nums">{entry.value}</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
