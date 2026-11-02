import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api } from "@/lib/api";
import type { Service } from "@/lib/types";

export default async function ServicesPage() {
  const services = await api<Service[]>("/services");
  return (
    <>
      <h1 className="text-2xl font-bold">Services</h1>
      {services.length === 0 ? (
        <p className="text-sm text-slate-500">No services registered yet.</p>
      ) : (
        <Table>
          <caption className="sr-only">Services</caption>
          <THead>
            <TR>
              <TH>Name</TH>
              <TH>Team</TH>
              <TH>Language</TH>
              <TH>Packages</TH>
              <TH>Vulns</TH>
              <TH>Critical</TH>
            </TR>
          </THead>
          <TBody>
            {services.map((s) => (
              <TR key={s.service_id}>
                <TD>
                  <Link href={`/services/${s.service_id}`} className="font-medium underline-offset-2 hover:underline">
                    {s.name}
                  </Link>
                </TD>
                <TD>{s.team_name}</TD>
                <TD>{s.language ?? "—"}</TD>
                <TD>{s.package_count}</TD>
                <TD>{s.vuln_count}</TD>
                <TD>{s.critical_count > 0 ? <Badge variant="critical">{s.critical_count}</Badge> : 0}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </>
  );
}
