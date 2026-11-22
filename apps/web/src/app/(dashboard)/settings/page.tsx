import { headers } from "next/headers";
import { CreateTeam, InviteForm, MemberControls, RemoveInvite } from "@/components/settings/Forms";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, authDisabled, getMe } from "@/lib/api";
import type { MeTeam, TeamInvite, TeamMember } from "@/lib/types";

async function TeamCard({ team, signIn }: { team: MeTeam; signIn: string }) {
  const admin = team.role === "admin";
  const [members, invites] = await Promise.all([
    api<TeamMember[]>(`/teams/${team.team_id}/members`),
    admin ? api<TeamInvite[]>(`/teams/${team.team_id}/invites`) : Promise.resolve([]),
  ]);
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {team.name} <span className="text-fg3">({team.role})</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Table>
          <caption className="sr-only">Members of {team.name}</caption>
          <THead>
            <TR>
              <TH>Member</TH>
              <TH>Role</TH>
            </TR>
          </THead>
          <TBody>
            {members.map((m) => (
              <TR key={m.user_id}>
                <TD className="text-xs">{m.email ?? <span className="font-mono">{m.user_id}</span>}</TD>
                <TD>
                  {admin ? <MemberControls teamId={team.team_id} userId={m.user_id} role={m.role} /> : m.role}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {admin && (
          <>
            {invites.length > 0 && (
              <Table>
                <caption className="sr-only">Pending invites of {team.name}</caption>
                <THead>
                  <TR>
                    <TH>Pending invite</TH>
                    <TH>Role</TH>
                    <TH>Expires</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {invites.map((i) => (
                    <TR key={i.email}>
                      <TD>{i.email}</TD>
                      <TD className="text-fg2">{i.role}</TD>
                      <TD className="text-fg2">{i.expires_at.slice(0, 10)}</TD>
                      <TD>
                        <RemoveInvite teamId={team.team_id} email={i.email} />
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
            <InviteForm teamId={team.team_id} />
            <p className="text-fg2">
              No email is sent. Ask them to sign in at {signIn} with that address and they join automatically.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default async function SettingsPage() {
  const me = await getMe();
  const h = await headers();
  const signIn = `${h.get("x-forwarded-proto") ?? "http"}://${h.get("host") ?? "localhost"}/login`;
  return (
    <>
      <h1>Settings</h1>
      <Card>
        <CardHeader>
          <CardTitle>Connection</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-[8rem_1fr]">
            <dt className="text-fg2">API URL</dt>
            <dd className="break-all font-mono text-xs">
              {process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}
            </dd>
            <dt className="text-fg2">Auth</dt>
            <dd>{authDisabled() ? "Disabled (dev mode, shared dev token)" : "Supabase"}</dd>
          </dl>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Your teams</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <caption className="sr-only">Your teams</caption>
            <THead>
              <TR>
                <TH>Team</TH>
                <TH>Role</TH>
              </TR>
            </THead>
            <TBody>
              {me.teams.map((t) => (
                <TR key={t.team_id}>
                  <TD>{t.name}</TD>
                  <TD className="text-fg2">{t.role}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
      {me.teams.map((t) => (
        <TeamCard key={t.team_id} team={t} signIn={signIn} />
      ))}
      {me.org_admin && (
        <Card>
          <CardHeader>
            <CardTitle>Create a team</CardTitle>
          </CardHeader>
          <CardContent>
            <CreateTeam />
          </CardContent>
        </Card>
      )}
    </>
  );
}
