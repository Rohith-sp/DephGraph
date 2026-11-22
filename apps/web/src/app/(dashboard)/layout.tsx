import { CreateTeam } from "@/components/settings/Forms";
import { Sidebar } from "@/components/layout/Sidebar";
import { Topbar } from "@/components/layout/Topbar";
import { getMe } from "@/lib/api";

// Data comes from the live API per request; never prerender at build time.
export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  // If /me fails, render the page anyway: its own fetch then shows the error boundary.
  const me = await getMe().catch(() => null);
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main className="mx-auto w-full max-w-[1440px] flex-1 space-y-8 px-4 py-6 md:px-8 md:py-8">{me?.allowed === false ? (
            <div className="max-w-xl space-y-4">
              <h1>Access restricted</h1>
              <p className="text-fg2">{me.reason ?? "Ask an admin for an invite."}</p>
            </div>
          ) : me?.teams.length === 0 ? (
            <div className="max-w-xl space-y-4">
              <h1>No team yet</h1>
              <p className="text-fg2">Create the first team, or ask a team admin to invite you and sign in again.</p>
              <CreateTeam />
            </div>
          ) : (
            children
          )}
        </main>
      </div>
    </div>
  );
}
