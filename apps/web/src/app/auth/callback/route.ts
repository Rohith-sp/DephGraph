import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

// PKCE: magic links and email confirmations land here with ?code=, which becomes a session cookie.
export async function GET(req: Request) {
  const { searchParams, origin } = new URL(req.url);
  const code = searchParams.get("code");
  if (code) {
    const { error } = await (await createClient()).auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(`${origin}/`);
  }
  return NextResponse.redirect(`${origin}/login?error=${encodeURIComponent("Sign-in link is invalid or expired")}`);
}
