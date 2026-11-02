"use client";

import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import type { Cluster, ClusterMember } from "@/lib/types";

const PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#ca8a04", "#db2777"];
export const clusterColor = (id: number) => PALETTE[id % PALETTE.length];

export interface ClusterWithMembers extends Cluster {
  members: ClusterMember[];
}

export function ClusterScatter({ clusters }: { clusters: ClusterWithMembers[] }) {
  return (
    <div className="space-y-3">
      <div role="img" aria-label="Package clusters, 2D projection" className="h-96 w-full">
        <ResponsiveContainer>
          <ScatterChart>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis type="number" dataKey="x" name="x" hide />
            <YAxis type="number" dataKey="y" name="y" hide />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              content={({ payload }) => {
                const p = payload?.[0]?.payload as ClusterMember | undefined;
                return p ? (
                  <div className="rounded border bg-white px-2 py-1 text-xs shadow">
                    {p.name} <span className="text-slate-500">({p.ecosystem})</span>
                  </div>
                ) : null;
              }}
            />
            {clusters.map((c) => (
              <Scatter key={c.cluster_id} name={c.label} data={c.members} fill={clusterColor(c.cluster_id)} />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <ul aria-label="Cluster legend" className="grid gap-2 sm:grid-cols-2">
        {clusters.map((c) => (
          <li key={c.cluster_id} className="flex items-start gap-2 text-sm">
            <span aria-hidden className="mt-1 h-3 w-3 shrink-0 rounded-full" style={{ background: clusterColor(c.cluster_id) }} />
            <span>
              <strong>{c.label}</strong> ({c.member_count})
              <span className="block text-slate-500">{c.keywords.join(", ")}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
