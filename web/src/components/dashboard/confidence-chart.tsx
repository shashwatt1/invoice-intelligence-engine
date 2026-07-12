import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { DashboardData } from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function colorFor(score: number): string {
  if (score >= 0.85) return "var(--success)";
  if (score >= 0.6) return "var(--warning)";
  return "var(--danger)";
}

/** Extraction confidence across the most recent documents. */
export function ConfidenceChart({ data }: { data: DashboardData }) {
  const chartData = data.recent
    .filter((row) => row.composite_confidence !== null)
    .slice(0, 10)
    .reverse()
    .map((row) => ({
      name:
        row.invoice_number ??
        (row.filename.length > 14 ? `${row.filename.slice(0, 12)}…` : row.filename),
      confidence: Math.round((row.composite_confidence ?? 0) * 100),
    }));

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="text-[0.95rem]">Recent extraction confidence</CardTitle>
      </CardHeader>
      <CardContent>
        {chartData.length === 0 ? (
          <div className="flex h-36 items-center justify-center text-[0.8rem] text-muted-foreground">
            Confidence scores appear once invoices are processed.
          </div>
        ) : (
          <div className="h-36">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -22 }}>
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
                  tickLine={false}
                  axisLine={{ stroke: "var(--border)" }}
                  interval={0}
                />
                <YAxis
                  domain={[0, 100]}
                  tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
                  tickLine={false}
                  axisLine={false}
                  ticks={[0, 50, 85, 100]}
                />
                <Tooltip
                  formatter={(value) => [`${value}%`, "Confidence"]}
                  contentStyle={{
                    borderRadius: 8,
                    border: "1px solid var(--border)",
                    fontSize: "0.78rem",
                    boxShadow: "0 4px 12px rgb(0 0 0 / 0.06)",
                  }}
                  cursor={{ fill: "var(--secondary)" }}
                />
                <Bar dataKey="confidence" radius={[3, 3, 0, 0]} maxBarSize={28}>
                  {chartData.map((entry) => (
                    <Cell key={entry.name} fill={colorFor(entry.confidence / 100)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
