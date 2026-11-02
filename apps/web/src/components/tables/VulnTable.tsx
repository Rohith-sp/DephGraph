import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { Paginated, Severity, Vulnerability } from "@/lib/types";

export interface VulnQuery {
  severity?: string;
  ecosystem?: string;
  q?: string;
  sort: "cvss_score" | "published_at";
  order: "asc" | "desc";
  page: number;
}

const VARIANT = { CRITICAL: "critical", HIGH: "high", MEDIUM: "medium", LOW: "low" } as const;

export const SeverityBadge = ({ severity }: { severity: Severity }) => (
  <Badge variant={VARIANT[severity]}>{severity}</Badge>
);

const href = (query: VulnQuery, patch: Partial<VulnQuery>) => {
  const m = { ...query, ...patch };
  const p = new URLSearchParams();
  (["severity", "ecosystem", "q"] as const).forEach((k) => m[k] && p.set(k, m[k]));
  p.set("sort", m.sort);
  p.set("order", m.order);
  p.set("page", String(m.page));
  return `/vulnerabilities?${p}`;
};

function SortHeader({ col, label, query }: { col: VulnQuery["sort"]; label: string; query: VulnQuery }) {
  const active = query.sort === col;
  const order = active && query.order === "desc" ? "asc" : "desc";
  return (
    <TH aria-sort={active ? (query.order === "desc" ? "descending" : "ascending") : "none"}>
      <Link href={href(query, { sort: col, order, page: 1 })} className="underline-offset-2 hover:underline">
        {label}
        {active && (query.order === "desc" ? " ↓" : " ↑")}
      </Link>
    </TH>
  );
}

export function VulnTable({ data, query }: { data: Paginated<Vulnerability>; query: VulnQuery }) {
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  if (data.items.length === 0) return <p className="text-sm text-slate-500">No vulnerabilities match.</p>;
  return (
    <div className="space-y-3">
      <Table>
        <caption className="sr-only">Vulnerabilities</caption>
        <THead>
          <TR>
            <TH>ID</TH>
            <TH>Title</TH>
            <TH>Severity</TH>
            <SortHeader col="cvss_score" label="CVSS" query={query} />
            <SortHeader col="published_at" label="Published" query={query} />
            <TH>Packages</TH>
            <TH>Services</TH>
            <TH>Fix</TH>
          </TR>
        </THead>
        <TBody>
          {data.items.map((v) => (
            <TR key={v.vuln_id}>
              <TD className="whitespace-nowrap">{v.osv_id}</TD>
              <TD>{v.title ?? "—"}</TD>
              <TD>
                <SeverityBadge severity={v.severity} />
              </TD>
              <TD>{v.cvss_score ?? "—"}</TD>
              <TD className="whitespace-nowrap">{v.published_at?.slice(0, 10) ?? "—"}</TD>
              <TD>{v.affected_packages}</TD>
              <TD>{v.affected_services}</TD>
              <TD>{v.has_fix ? "Yes" : "No"}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <nav aria-label="Pagination" className="flex items-center justify-between text-sm">
        <span>
          Page {data.page} of {pages} ({data.total} total)
        </span>
        <span className="flex gap-3">
          {data.page > 1 && <Link href={href(query, { page: data.page - 1 })}>Previous</Link>}
          {data.page < pages && <Link href={href(query, { page: data.page + 1 })}>Next</Link>}
        </span>
      </nav>
    </div>
  );
}
