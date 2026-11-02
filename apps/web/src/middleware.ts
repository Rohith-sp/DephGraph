import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC = ["/login", "/register"];

export async function middleware(req: NextRequest) {
  // Opt-in only: anything but the literal "true" keeps auth enforced.
  if (process.env.AUTH_DISABLED === "true") return NextResponse.next();

  const { pathname } = req.nextUrl;
  const isPublic = PUBLIC.some((p) => pathname === p);
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  const toLogin = (error?: string) => {
    const dest = req.nextUrl.clone();
    dest.pathname = "/login";
    dest.search = error ? `?error=${encodeURIComponent(error)}` : "";
    return NextResponse.redirect(dest);
  };

  if (!url || !key) return isPublic ? NextResponse.next() : toLogin("Auth is not configured");

  let res = NextResponse.next({ request: req });
  const supabase = createServerClient(url, key, {
    cookies: {
      getAll: () => req.cookies.getAll(),
      setAll: (all) => {
        all.forEach(({ name, value }) => req.cookies.set(name, value));
        res = NextResponse.next({ request: req });
        all.forEach(({ name, value, options }) => res.cookies.set(name, value, options));
      },
    },
  });
  const { data } = await supabase.auth.getUser();
  if (!data.user && !isPublic) return toLogin();
  return res;
}

export const config = {
  // Webhook is HMAC-authenticated in its own handler.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/webhooks).*)"],
};
