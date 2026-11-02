import { ClusterScatter, type ClusterWithMembers } from "@/components/charts/ClusterScatter";
import { api } from "@/lib/api";
import type { Cluster, ClusterMember, ClusterRedundancy } from "@/lib/types";

export default async function ClustersPage() {
  const [clusters, redundancy] = await Promise.all([
    api<Cluster[]>("/clusters"),
    api<ClusterRedundancy[]>("/clusters/redundancy"),
  ]);
  const withMembers: ClusterWithMembers[] = await Promise.all(
    clusters.map(async (c) => ({ ...c, members: await api<ClusterMember[]>(`/clusters/${c.cluster_id}/members`) })),
  );
  return (
    <>
      <h1 className="text-2xl font-bold">Clusters</h1>
      {clusters.length === 0 ? (
        <p className="text-sm text-slate-500">No clusters yet. Trigger a recompute from the API to generate them.</p>
      ) : (
        <ClusterScatter clusters={withMembers} />
      )}
      <section aria-labelledby="redundancy-h" className="space-y-2">
        <h2 id="redundancy-h" className="text-lg font-semibold">
          Redundant packages
        </h2>
        {redundancy.length === 0 ? (
          <p className="text-sm text-slate-500">No redundancy detected.</p>
        ) : (
          <ul className="list-disc pl-5 text-sm">
            {redundancy.map((r) => (
              <li key={`${r.service_id}-${r.cluster_id}`}>
                {r.package_count} {r.cluster_label} packages in {r.service_name}: {r.packages.join(", ")}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
