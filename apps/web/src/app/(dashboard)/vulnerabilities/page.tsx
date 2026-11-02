import { VulnFilters } from "@/components/tables/VulnFilters";
import { VulnTable, type VulnQuery } from "@/components/tables/VulnTable";
import { api } from "@/lib/api";
import type { Paginated, Vulnerability } from "@/lib/types";

type Params = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

export default async function VulnerabilitiesPage({ searchParams }: { searchParams: Promise<Params> }) {
  const sp = await searchParams;
  const query: VulnQuery = {
    severity: one(sp.severity),
    ecosystem: one(sp.ecosystem),
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
      <h1 className="text-2xl font-bold">Vulnerabilities</h1>
      <VulnFilters />
      <VulnTable data={data} query={query} />
    </>
  );
}
