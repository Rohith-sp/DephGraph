import { notFound } from "next/navigation";
import { DependencyGraph } from "@/components/graph/DependencyGraph";
import { RevokeToken, TokenForm } from "@/components/settings/Forms";
import { PrunableTable } from "@/components/tables/PrunableTable";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import type { PrunablePackage, ServiceGraph, ServiceToken } from "@/lib/types";

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
  // Only team admins may list tokens (403 for everyone else): no section for them.
  const tokens = await api<ServiceToken[]>(`/services/${id}/tokens`).catch((e) => {
    if (e instanceof ApiError && e.status === 403) return null;
    throw e;
  });
  return (
    <>
      <h1>{graph.service.name}</h1>
      <DependencyGraph graph={graph} />
      <section aria-labelledby="prunable-h" className="space-y-3">
        <h2 id="prunable-h">
          Prunable packages
        </h2>
        <PrunableTable rows={prunable} />
      </section>
      {tokens && (
        <section aria-labelledby="tokens-h" className="space-y-3">
          <h2 id="tokens-h">Ingest tokens</h2>
          <p className="text-fg2">
            Runtime collectors send liveness with <code>Authorization: Bearer &lt;token&gt;</code> for service{" "}
            {graph.service.service_id}.
          </p>
          {tokens.length > 0 && (
            <Table>
              <caption className="sr-only">Ingest tokens</caption>
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Last used</TH>
                  <TH>Status</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {tokens.map((t) => (
                  <TR key={t.token_id}>
                    <TD>{t.name}</TD>
                    <TD className="text-fg2">{t.last_used_at ? t.last_used_at.slice(0, 16).replace("T", " ") : "never"}</TD>
                    <TD className="text-fg2">{t.revoked_at ? "revoked" : "active"}</TD>
                    <TD>{!t.revoked_at && <RevokeToken serviceId={graph.service.service_id} tokenId={t.token_id} />}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          <TokenForm serviceId={graph.service.service_id} />
        </section>
      )}
    </>
  );
}
