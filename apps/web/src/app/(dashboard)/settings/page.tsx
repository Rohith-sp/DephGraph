import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { authDisabled } from "@/lib/api";

export default function SettingsPage() {
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
    </>
  );
}
