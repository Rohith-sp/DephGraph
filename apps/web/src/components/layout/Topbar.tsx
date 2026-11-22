import { LogOut } from "lucide-react";
import { signOut } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { authDisabled } from "@/lib/api";
import { createClient } from "@/lib/supabase/server";

async function userLabel(): Promise<string> {
  if (authDisabled()) return "Dev mode (auth disabled)";
  const { data } = await (await createClient()).auth.getClaims();
  return data?.claims.email ?? "Signed out";
}

export async function Topbar() {
  return (
    <header className="flex h-14 items-center justify-end gap-3 border-b border-line px-4 md:px-8">
      <span className="truncate font-mono text-xs text-fg2">{await userLabel()}</span>
      {!authDisabled() && (
        <form action={signOut}>
          <Button variant="outline" size="sm" type="submit">
            <LogOut aria-hidden strokeWidth={1.5} className="h-4 w-4" /> Sign out
          </Button>
        </form>
      )}
    </header>
  );
}
