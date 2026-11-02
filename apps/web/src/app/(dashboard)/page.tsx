import { SeverityDonut } from "@/components/charts/SeverityDonut";
import { MetricCard } from "@/components/layout/MetricCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { StatsOverview } from "@/lib/types";

export default async function OverviewPage() {
  const s = await api<StatsOverview>("/stats/overview");
  return (
    <>
      <h1 className="text-2xl font-bold">Overview</h1>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard title="Services" value={s.services} />
        <MetricCard title="Packages" value={s.packages} />
        <MetricCard title="Open vulns" value={s.open_vulns} />
        <MetricCard title="Critical vulns" value={s.critical_vulns} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Open vulnerabilities by severity</CardTitle>
        </CardHeader>
        <CardContent>
          <SeverityDonut counts={s.severity_counts} />
        </CardContent>
      </Card>
    </>
  );
}
