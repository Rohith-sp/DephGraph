import { notFound } from "next/navigation";
import { DependencyGraph } from "@/components/graph/DependencyGraph";
import { PrunableTable } from "@/components/tables/PrunableTable";
import { api, ApiError } from "@/lib/api";
import type { PrunablePackage, ServiceGraph } from "@/lib/types";

export default async function ServiceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) notFound();
  const [graph, prunable] = await Promise.all([
    api<ServiceGraph>(`/services/${id}/graph`),
    api<PrunablePackage[]>(`/services/${id}/prunable`),
  ]).catch((e) => {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  });
  return (
    <>
      <h1 className="text-2xl font-bold">{graph.service.name}</h1>
      <DependencyGraph graph={graph} />
      <section aria-labelledby="prunable-h" className="space-y-2">
        <h2 id="prunable-h" className="text-lg font-semibold">
          Prunable packages
        </h2>
        <PrunableTable rows={prunable} />
      </section>
    </>
  );
}
