"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { RemediationStep } from "@/lib/types";

const tick = { fill: "rgb(var(--fg3))", fontSize: 12, fontFamily: "var(--font-geist-mono)" };
const accent = "rgb(var(--accent))";

// Cumulative share of the team's open vulnerabilities fixed after each upgrade, in plan order.
export function CoverageCurve({ steps }: { steps: RemediationStep[] }) {
  const data = steps.map((s) => ({ step: `#${s.step}`, package: s.package, coverage: s.cumulative_coverage_pct }));
  return (
    <div
      role="img"
      aria-label="Cumulative vulnerability coverage per remediation step"
      className="h-80 w-full rounded-xl border border-line bg-card py-5 pr-5"
    >
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="rgb(var(--fg) / 0.08)" />
          <XAxis dataKey="step" tick={tick} tickLine={false} axisLine={{ stroke: "rgb(var(--fg) / 0.16)" }} />
          <YAxis
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            unit="%"
            width={48}
            tick={tick}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            cursor={{ stroke: "rgb(var(--fg) / 0.16)" }}
            content={({ payload }) => {
              const p = payload?.[0]?.payload as (typeof data)[number] | undefined;
              return p ? (
                <div className="rounded-lg border border-line-strong bg-raised px-3 py-2 text-xs">
                  <div className="font-mono text-fg2">
                    {p.step} {p.package}
                  </div>
                  <div className="mt-1 font-mono text-fg">{p.coverage.toFixed(1)}% covered</div>
                </div>
              ) : null;
            }}
          />
          <Line
            type="monotone"
            dataKey="coverage"
            name="Cumulative coverage"
            stroke={accent}
            strokeWidth={2}
            dot={{ r: 4, fill: "rgb(var(--card))", stroke: accent, strokeWidth: 2 }}
            activeDot={{ r: 5, fill: accent, stroke: accent }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
