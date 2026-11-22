"use client";

import { Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { SEVERITIES, sevColor } from "@/lib/severity";
import type { Severity } from "@/lib/types";

const tick = { fill: "rgb(var(--fg3))", fontSize: 12, fontFamily: "var(--font-geist-mono)" };

export function SeverityBars({ counts }: { counts: Record<Severity, number> }) {
  const data = SEVERITIES.map((s) => ({ name: s[0] + s.slice(1).toLowerCase(), severity: s, value: counts[s] ?? 0 }));
  if (data.every((d) => d.value === 0)) return <p className="text-fg2">No open vulnerabilities.</p>;
  return (
    <div role="img" aria-label={data.map((d) => `${d.name}: ${d.value}`).join(", ")} className="h-72 w-full">
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 32, bottom: 0, left: 0 }} barCategoryGap={16}>
          <XAxis type="number" hide domain={[0, "dataMax"]} />
          <YAxis type="category" dataKey="name" width={64} tickLine={false} axisLine={false} tick={tick} />
          <Tooltip
            cursor={{ fill: "rgb(var(--fg) / 0.04)" }}
            contentStyle={{
              background: "rgb(var(--raised))",
              border: "1px solid rgb(var(--fg) / 0.16)",
              borderRadius: 8,
              color: "rgb(var(--fg))",
              fontSize: 12,
            }}
            itemStyle={{ color: "rgb(var(--fg2))" }}
            labelStyle={{ color: "rgb(var(--fg))" }}
          />
          <Bar dataKey="value" name="Open vulnerabilities" barSize={24} radius={[0, 4, 4, 0]} isAnimationActive={false}>
            {data.map((d) => (
              <Cell key={d.name} fill={sevColor(d.severity)} />
            ))}
            <LabelList dataKey="value" position="right" style={{ ...tick, fill: "rgb(var(--fg2))" }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
