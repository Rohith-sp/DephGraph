import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { authDisabled } from "@/lib/api";

export default function SettingsPage() {
  return (
    <>
      <h1 className="text-2xl font-bold">Settings</h1>
      <Card>
        <CardHeader>
          <CardTitle>Connection</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[10rem_1fr] gap-2 text-sm">
            <dt className="text-slate-500">API URL</dt>
            <dd>{process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}</dd>
            <dt className="text-slate-500">Auth</dt>
            <dd>{authDisabled() ? "Disabled (dev mode, shared dev token)" : "Supabase"}</dd>
          </dl>
        </CardContent>
      </Card>
    </>
  );
}
