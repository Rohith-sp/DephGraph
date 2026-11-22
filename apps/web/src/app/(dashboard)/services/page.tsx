import Link from "next/link";
import { AddServiceForm } from "@/components/settings/Forms";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, getMe } from "@/lib/api";
import type { Service } from "@/lib/types";

export default async function ServicesPage() {
  const [services, me] = await Promise.all([api<Service[]>("/services"), getMe()]);
  const adminTeams = me.teams.filter((t) => t.role === "admin");
  return (
    <>
      <h1>Services</h1>
      {adminTeams.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Add service</CardTitle>
          </CardHeader>
          <CardContent>
            <AddServiceForm teams={adminTeams} />
          </CardContent>
        </Card>
      )}
      {services.length === 0 ? (
        <p className="text-fg2">No services registered yet.</p>
      ) : (
        <Table>
          <caption className="sr-only">Services</caption>
          <THead>
            <TR>
              <TH>Name</TH>
              <TH>Team</TH>
              <TH>Language</TH>
              <TH className="text-right">Packages</TH>
              <TH className="text-right">Vulns</TH>
              <TH className="text-right">Critical</TH>
            </TR>
          </THead>
          <TBody>
            {services.map((s) => (
              <TR key={s.service_id}>
                <TD>
                  <Link href={`/services/${s.service_id}`} className="font-medium hover:text-accent">
                    {s.name}
                  </Link>
                </TD>
                <TD>{s.team_name}</TD>
                <TD className="font-mono text-xs text-fg2">{s.language ?? "—"}</TD>
                <TD className="text-right">{s.package_count}</TD>
                <TD className="text-right">{s.vuln_count}</TD>
                <TD className="text-right">
                  {s.critical_count > 0 ? <Badge variant="critical">{s.critical_count}</Badge> : 0}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </>
  );
}
