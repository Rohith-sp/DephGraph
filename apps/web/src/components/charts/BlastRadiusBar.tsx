"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { RemediationStep } from "@/lib/types";

export function BlastRadiusBar({ steps }: { steps: RemediationStep[] }) {
  const data = steps.map((s) => ({ step: `#${s.step} ${s.package}`, coverage: s.cumulative_coverage_pct }));
  return (
    <div role="img" aria-label="Cumulative vulnerability coverage per remediation step" className="h-64 w-full">
      <ResponsiveContainer>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="step" tick={{ fontSize: 11 }} />
          <YAxis domain={[0, 100]} unit="%" />
          <Tooltip formatter={(v: number) => `${v.toFixed(1)}%`} />
          <Bar dataKey="coverage" name="Cumulative coverage" fill="#0f766e" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
