import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { PrunablePackage } from "@/lib/types";

export function PrunableTable({ rows }: { rows: PrunablePackage[] }) {
  if (rows.length === 0) return <p className="text-sm text-slate-500">No prunable packages.</p>;
  return (
    <Table>
      <caption className="sr-only">Packages never loaded at runtime</caption>
      <THead>
        <TR>
          <TH>Package</TH>
          <TH>Ecosystem</TH>
          <TH>Version</TH>
          <TH>Runtime executions</TH>
          <TH>Deployments observed</TH>
        </TR>
      </THead>
      <TBody>
        {rows.map((r) => (
          <TR key={r.package_id}>
            <TD>{r.name}</TD>
            <TD>{r.ecosystem}</TD>
            <TD>{r.version}</TD>
            <TD>{r.exec_count}</TD>
            <TD>{r.deploy_seq}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
