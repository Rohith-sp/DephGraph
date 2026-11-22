import { VulnFilters } from "@/components/tables/VulnFilters";
import { VulnTable, type VulnQuery } from "@/components/tables/VulnTable";
import { api } from "@/lib/api";
import { ECOSYSTEMS, SEVERITIES } from "@/lib/severity";
import type { Paginated, Vulnerability } from "@/lib/types";

type Params = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
// Anything outside the list would be a 422 from the API, so unknown values mean "no filter".
const oneOf = (v: string | string[] | undefined, allowed: readonly string[]) => {
  const x = one(v);
  return x && allowed.includes(x) ? x : undefined;
};

export default async function VulnerabilitiesPage({ searchParams }: { searchParams: Promise<Params> }) {
  const sp = await searchParams;
  const query: VulnQuery = {
    severity: oneOf(sp.severity, SEVERITIES),
    ecosystem: oneOf(sp.ecosystem, ECOSYSTEMS),
    q: one(sp.q),
    sort: one(sp.sort) === "published_at" ? "published_at" : "cvss_score",
    order: one(sp.order) === "asc" ? "asc" : "desc",
    page: Math.max(1, Number(one(sp.page)) || 1),
  };
  const qs = new URLSearchParams({ sort: query.sort, order: query.order, page: String(query.page), page_size: "50" });
  (["severity", "ecosystem", "q"] as const).forEach((k) => query[k] && qs.set(k, query[k]));
  const data = await api<Paginated<Vulnerability>>(`/vulnerabilities?${qs}`);
  return (
    <>
      <h1>Vulnerabilities</h1>
      <VulnFilters />
      <VulnTable data={data} query={query} />
    </>
  );
}
