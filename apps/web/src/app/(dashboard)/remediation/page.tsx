import { CoverageCurve } from "@/components/charts/CoverageCurve";
import { RemediationTable } from "@/components/tables/RemediationTable";
import { SavePlan } from "@/components/tables/SavePlan";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { api, getMe } from "@/lib/api";
import type { RemediationPlan, RemediationStep } from "@/lib/types";

export default async function RemediationPage({ searchParams }: { searchParams: Promise<{ team_id?: string }> }) {
  const { team_id } = await searchParams;
  const asked = Number(team_id);
  const plan = (id: number) => api<RemediationStep[]>(`/remediation/plan/${id}`);
  // A team in the URL is fetched alongside the caller's teams (a non-member team simply 404s and
  // falls back to the first team); without one the default comes from the list.
  const [{ teams }, askedSteps] = await Promise.all([
    getMe(),
    Number.isInteger(asked) && asked > 0 ? plan(asked).catch(() => null) : null,
  ]);
  const teamId = teams.some((t) => t.team_id === asked) ? asked : teams[0]?.team_id;
  const steps = teamId === asked && askedSteps ? askedSteps : teamId ? await plan(teamId) : [];
  // The API marks steps that belong to the team's latest saved plan; rebuild it so a reload keeps progress.
  const savedSteps = steps.filter((s) => s.plan_id != null);
  const saved: RemediationPlan | null =
    teamId && savedSteps.length
      ? {
          plan_id: savedSteps[0].plan_id!,
          team_id: teamId,
          title: null,
          status: "OPEN",
          coverage_pct: 0,
          actions: savedSteps.map((s) => ({
            action_seq: s.action_seq!,
            description: `Upgrade ${s.package} ${s.from_version} -> ${s.to_version}`,
            status: s.action_status!,
          })),
        }
      : null;
  return (
    <>
      <h1>Remediation</h1>
      <form method="get" className="flex items-end gap-3">
        <label className="flex flex-col gap-1.5 text-fg2">
          Team
          <Select name="team_id" defaultValue={teamId}>
            {teams.map((t) => (
              <option key={t.team_id} value={t.team_id}>
                {t.name}
              </option>
            ))}
          </Select>
        </label>
        <Button type="submit" variant="outline">
          Show plan
        </Button>
      </form>
      {!teamId || steps.length === 0 ? (
        <p className="text-fg2">Nothing to remediate for this team.</p>
      ) : (
        <>
          <CoverageCurve steps={steps} />
          <RemediationTable steps={steps} />
          <SavePlan key={teamId} teamId={teamId} saved={saved} />
        </>
      )}
    </>
  );
}
