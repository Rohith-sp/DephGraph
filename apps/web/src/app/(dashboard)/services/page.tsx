import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { Service } from "@/lib/types";

export default async function ServicesPage() {
  const services = await api<Service[]>("/services");
  return (
    <>
      <h1>Services</h1>
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
