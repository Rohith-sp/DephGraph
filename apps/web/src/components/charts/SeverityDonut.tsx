"use client";

import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { Severity } from "@/lib/types";

export const SEVERITY_COLORS: Record<Severity, string> = {
  CRITICAL: "#dc2626",
  HIGH: "#ea580c",
  MEDIUM: "#ca8a04",
  LOW: "#2563eb",
};

export function SeverityDonut({ counts }: { counts: Record<Severity, number> }) {
  const data = (Object.keys(SEVERITY_COLORS) as Severity[]).map((name) => ({ name, value: counts[name] ?? 0 }));
  if (data.every((d) => d.value === 0)) return <p className="text-sm text-slate-500">No open vulnerabilities.</p>;
  return (
    <div role="img" aria-label={data.map((d) => `${d.name}: ${d.value}`).join(", ")} className="h-64 w-full">
      <ResponsiveContainer>
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" innerRadius={60} outerRadius={90}>
            {data.map((d) => (
              <Cell key={d.name} fill={SEVERITY_COLORS[d.name]} />
            ))}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
