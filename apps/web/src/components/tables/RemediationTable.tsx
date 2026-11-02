import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { RemediationStep } from "@/lib/types";

export function RemediationTable({ steps }: { steps: RemediationStep[] }) {
  return (
    <Table>
      <caption className="sr-only">Greedy remediation plan</caption>
      <THead>
        <TR>
          <TH>#</TH>
          <TH>Upgrade</TH>
          <TH>Fixes</TH>
          <TH>Services affected</TH>
          <TH>Newly covered</TH>
          <TH>Cumulative coverage</TH>
        </TR>
      </THead>
      <TBody>
        {steps.map((s) => (
          <TR key={s.step}>
            <TD>{s.step}</TD>
            <TD>
              {s.package} {s.from_version} → {s.to_version}
            </TD>
            <TD>{s.vulns_fixed.join(", ")}</TD>
            <TD>{s.services_affected.join(", ")}</TD>
            <TD>{s.new_coverage}</TD>
            <TD>{s.cumulative_coverage_pct.toFixed(1)}%</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
