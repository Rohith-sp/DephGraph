import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { PrunablePackage } from "@/lib/types";

export function PrunableTable({ rows }: { rows: PrunablePackage[] }) {
  if (rows.length === 0) return <p className="text-fg2">No prunable packages.</p>;
  return (
    <Table>
      <caption className="sr-only">Packages never loaded at runtime</caption>
      <THead>
        <TR>
          <TH>Package</TH>
          <TH>Ecosystem</TH>
          <TH>Version</TH>
          <TH className="text-right">Runtime executions</TH>
          <TH className="text-right">Deployments observed</TH>
        </TR>
      </THead>
      <TBody>
        {rows.map((r) => (
          <TR key={`${r.package_id}@${r.version}`}>
            <TD>{r.name}</TD>
            <TD className="font-mono text-xs text-fg2">{r.ecosystem}</TD>
            <TD className="font-mono text-xs">{r.version}</TD>
            <TD className="text-right">{r.exec_count}</TD>
            <TD className="text-right">{r.deploy_seq}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
