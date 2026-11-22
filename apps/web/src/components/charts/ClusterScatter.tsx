"use client";

import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import type { Cluster, ClusterMember } from "@/lib/types";

// Categorical hues readable on both the dark and the light surface; none reuses a severity colour.
const PALETTE = ["#2fa5c4", "#5b8def", "#38b58a", "#b0a05a", "#8a93a8", "#a78bdb"];
export const clusterColor = (id: number) => PALETTE[id % PALETTE.length];

type Point = ClusterMember & { x: number; y: number };
const placed = (m: ClusterMember): m is Point => m.x != null && m.y != null;

export function ClusterScatter({ clusters }: { clusters: Cluster[] }) {
  const series = clusters.map((c) => ({ ...c, points: c.members.filter(placed) }));
  const hasPoints = series.some((c) => c.points.length > 0);
  return (
    <div className="space-y-4">
      {hasPoints ? (
        <div
          role="img"
          aria-label="Package clusters, 2D projection"
          className="h-96 w-full rounded-xl border border-line bg-card p-4"
        >
          <ResponsiveContainer>
            <ScatterChart>
              <CartesianGrid stroke="rgb(var(--fg) / 0.08)" />
              <XAxis type="number" dataKey="x" name="x" hide />
              <YAxis type="number" dataKey="y" name="y" hide />
              <Tooltip
                cursor={{ stroke: "rgb(var(--fg) / 0.16)", strokeDasharray: "3 3" }}
                content={({ payload }) => {
                  const p = payload?.[0]?.payload as ClusterMember | undefined;
                  return p ? (
                    <div className="rounded-lg border border-line-strong bg-raised px-3 py-2 text-xs">
                      {p.name} <span className="font-mono text-fg2">{p.ecosystem}</span>
                    </div>
                  ) : null;
                }}
              />
              {series.map((c) => (
                <Scatter
                  key={c.cluster_id}
                  name={c.label}
                  data={c.points}
                  fill={clusterColor(c.cluster_id)}
                  isAnimationActive={false}
                />
              ))}
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="rounded-xl border border-dashed border-line-strong p-5 text-fg2">
          No 2D projection yet. Coordinates appear after the next cluster recompute.
        </p>
      )}
      <ul aria-label="Cluster legend" className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {clusters.map((c) => (
          <li key={c.cluster_id} className="flex items-start gap-2.5">
            <span
              aria-hidden
              className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
              style={{ background: clusterColor(c.cluster_id) }}
            />
            <span>
              <span className="font-medium">{c.label}</span>{" "}
              <span className="font-mono text-xs text-fg2">{c.member_count}</span>
              {c.keywords.length > 0 && <span className="block text-fg3">{c.keywords.join(", ")}</span>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
