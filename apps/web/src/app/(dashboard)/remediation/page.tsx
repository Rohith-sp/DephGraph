import { BlastRadiusBar } from "@/components/charts/BlastRadiusBar";
import { RemediationTable } from "@/components/tables/RemediationTable";
import { SavePlan } from "@/components/tables/SavePlan";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import type { RemediationStep, Service } from "@/lib/types";

export default async function RemediationPage({ searchParams }: { searchParams: Promise<{ team_id?: string }> }) {
  const { team_id } = await searchParams;
  const services = await api<Service[]>("/services");
  const teams = [...new Map(services.map((s) => [s.team_id, s.team_name])).entries()];
  const teamId = Number(team_id) || teams[0]?.[0];
  const steps = teamId ? await api<RemediationStep[]>(`/remediation/plan/${teamId}`) : [];
  return (
    <>
      <h1 className="text-2xl font-bold">Remediation</h1>
      <form method="get" className="flex items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Team
          <Select name="team_id" defaultValue={teamId}>
            {teams.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </Select>
        </label>
        <Button type="submit" variant="outline">
          Show plan
        </Button>
      </form>
      {!teamId || steps.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing to remediate for this team.</p>
      ) : (
        <>
          <BlastRadiusBar steps={steps} />
          <RemediationTable steps={steps} />
          <SavePlan teamId={teamId} />
        </>
      )}
    </>
  );
}
