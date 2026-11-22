import { ClusterScatter } from "@/components/charts/ClusterScatter";
import { api } from "@/lib/api";
import type { Cluster, ClusterRedundancy } from "@/lib/types";

export default async function ClustersPage() {
  const [clusters, redundancy] = await Promise.all([
    api<Cluster[]>("/clusters"),
    api<ClusterRedundancy[]>("/clusters/redundancy"),
  ]);
  return (
    <>
      <h1>Clusters</h1>
      {clusters.length === 0 ? (
        <p className="text-fg2">No clusters yet. Trigger a recompute from the API to generate them.</p>
      ) : (
        <ClusterScatter clusters={clusters} />
      )}
      <section aria-labelledby="redundancy-h" className="space-y-3">
        <h2 id="redundancy-h">
          Redundant packages
        </h2>
        {redundancy.length === 0 ? (
          <p className="text-fg2">No redundancy detected.</p>
        ) : (
          <ul className="divide-y divide-line rounded-xl border border-line bg-card">
            {redundancy.map((r) => (
              <li key={`${r.service_id}-${r.cluster_id}`} className="px-4 py-2.5">
                {r.package_count} {r.cluster_label} packages in {r.service_name}:{" "}
                <span className="font-mono text-xs text-fg2">{r.packages.join(", ")}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
