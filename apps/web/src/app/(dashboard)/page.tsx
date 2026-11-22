import Link from "next/link";
import { SeverityBars } from "@/components/charts/SeverityBars";
import { MetricCard } from "@/components/layout/MetricCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Service, StatsOverview } from "@/lib/types";

export default async function OverviewPage() {
  const [s, services] = await Promise.all([api<StatsOverview>("/stats/overview"), api<Service[]>("/services")]);
  const exposed = services
    .filter((v) => v.vuln_count > 0)
    .sort((a, b) => b.vuln_count - a.vuln_count || a.name.localeCompare(b.name))
    .slice(0, 6);
  const max = exposed[0]?.vuln_count ?? 1;
  return (
    <>
      <h1>Overview</h1>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard title="Services" value={s.services} />
        <MetricCard title="Packages" value={s.packages} />
        <MetricCard title="Open vulns" value={s.open_vulns} />
        <MetricCard title="Critical vulns" value={s.critical_vulns} tone="crit" />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Open vulnerabilities by severity</CardTitle>
          </CardHeader>
          <CardContent>
            <SeverityBars counts={s.severity_counts} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Most exposed services</CardTitle>
          </CardHeader>
          <CardContent>
            {exposed.length === 0 ? (
              <p className="text-fg2">No service has open vulnerabilities.</p>
            ) : (
              <ol className="space-y-4">
                {exposed.map((v) => (
                  <li key={v.service_id}>
                    <div className="flex items-baseline justify-between gap-3">
                      <Link href={`/services/${v.service_id}`} className="truncate hover:text-accent">
                        {v.name}
                      </Link>
                      <span className="font-mono text-xs tabular-nums text-fg2">{v.vuln_count}</span>
                    </div>
                    <div aria-hidden className="mt-2 h-1 rounded-full bg-raised">
                      <div className="h-1 rounded-full bg-fg3" style={{ width: `${(v.vuln_count / max) * 100}%` }} />
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
