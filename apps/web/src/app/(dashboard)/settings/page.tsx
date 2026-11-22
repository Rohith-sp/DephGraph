import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { authDisabled, getMe } from "@/lib/api";

export default async function SettingsPage() {
  const me = await getMe();
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
    </>
  );
}
