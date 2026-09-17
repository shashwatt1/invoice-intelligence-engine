import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { DashboardData } from "@/api/types";
import { SectionHeader } from "@/components/layout/page-header";

function colorFor(score: number): string {
  if (score >= 0.85) return "var(--success)";
  if (score >= 0.6) return "var(--warning)";
  return "var(--danger)";
}

/** Extraction confidence across the most recent invoices — answers "is extraction quality holding?" */
export function ConfidenceChart({ data }: { data: DashboardData }) {
  const chartData = data.recent
    .filter((row) => row.composite_confidence !== null)
    .slice(0, 8)
    .reverse()
    .map((row) => ({
      name: row.invoice_number ?? (row.filename.length > 12 ? `${row.filename.slice(0, 8)}…` : row.filename),
      confidence: Math.round((row.composite_confidence ?? 0) * 100),
    }));

  return (
    <div className="surface flex h-full flex-col overflow-hidden">
      <SectionHeader title="Extraction confidence" description="Composite score, most recent invoices" />
      <div className="flex-1 border-t px-4 py-4">
        {chartData.length === 0 ? (
          <div className="flex h-32 items-center justify-center text-[0.78rem] text-muted-foreground">
            Scores appear once invoices are processed.
          </div>
        ) : (
          <div className="h-32">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
                <XAxis dataKey="name" tick={{ fontSize: 9, fill: "var(--muted-foreground)" }} tickLine={false} axisLine={{ stroke: "var(--border)" }} interval={0} tickFormatter={(v: string) => (v.length > 7 ? `${v.slice(0, 6)}…` : v)} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} tickLine={false} axisLine={false} ticks={[0, 50, 85, 100]} />
                <Tooltip formatter={(value) => [`${value}%`, "Confidence"]}
                         contentStyle={{ borderRadius: 8, border: "1px solid var(--border)", fontSize: "0.76rem", boxShadow: "var(--shadow-md)" }}
                         cursor={{ fill: "var(--surface-2)" }} />
                <Bar dataKey="confidence" radius={[3, 3, 0, 0]} maxBarSize={26} isAnimationActive={false}>
                  {chartData.map((entry) => <Cell key={entry.name} fill={colorFor(entry.confidence / 100)} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
