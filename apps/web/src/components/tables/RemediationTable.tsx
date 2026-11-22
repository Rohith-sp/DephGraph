import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { RemediationStep } from "@/lib/types";

export function RemediationTable({ steps }: { steps: RemediationStep[] }) {
  return (
    <Table>
      <caption className="sr-only">Greedy remediation plan</caption>
      <THead>
        <TR>
          <TH className="w-12 text-right">#</TH>
          <TH>Upgrade</TH>
          <TH>Fixes</TH>
          <TH>Services affected</TH>
          <TH className="text-right">Newly covered</TH>
          <TH className="text-right">Cumulative coverage</TH>
        </TR>
      </THead>
      <TBody>
        {steps.map((s) => (
          <TR key={s.step}>
            <TD className="text-right text-fg2">{s.step}</TD>
            <TD>
              <span className="font-medium">{s.package}</span>{" "}
              <span className="font-mono text-xs text-fg2">
                {s.from_version} → {s.to_version}
              </span>
            </TD>
            <TD className="font-mono text-xs">{s.vulns_fixed.join(", ")}</TD>
            <TD>{s.services_affected.join(", ")}</TD>
            <TD className="text-right">{s.new_coverage}</TD>
            <TD className="text-right">{s.cumulative_coverage_pct.toFixed(1)}%</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}
