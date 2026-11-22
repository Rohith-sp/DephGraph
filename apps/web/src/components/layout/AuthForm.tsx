"use client";

import { GitFork } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createClient } from "@/lib/supabase/client";

export function AuthForm({ mode, initialError }: { mode: "login" | "register"; initialError?: string }) {
  const router = useRouter();
  const [error, setError] = useState(initialError ?? "");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  async function go(formEl: HTMLFormElement, magic: boolean) {
    const form = new FormData(formEl);
    const email = String(form.get("email"));
    const password = String(form.get("password") ?? "");
    setError("");
    setInfo("");
    if (!process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY) {
      return setError("Auth is not configured");
    }
    setBusy(true);
    const auth = createClient().auth;
    const redirectTo = `${window.location.origin}/auth/callback`;
    const { data, error: err } = magic
      ? await auth.signInWithOtp({ email, options: { emailRedirectTo: redirectTo } })
      : mode === "login"
        ? await auth.signInWithPassword({ email, password })
        : await auth.signUp({ email, password, options: { emailRedirectTo: redirectTo } });
    setBusy(false);
    if (err) return setError(err.message);
    // Without email confirmation, sign-up already returns a session.
    if (magic || (mode === "register" && !data.session)) return setInfo("Check your email to continue.");
    router.replace("/");
    router.refresh();
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void go(e.currentTarget, false);
      }}
      className="w-full max-w-sm space-y-5 rounded-xl border border-line bg-card p-6"
    >
      <div className="flex items-center gap-2.5 text-base font-medium">
        <GitFork aria-hidden strokeWidth={1.5} className="h-[18px] w-[18px]" /> DephGraph
      </div>
      <h1 className="text-xl">{mode === "login" ? "Sign in" : "Create account"}</h1>
      <label className="flex flex-col gap-1.5 text-fg2">
        Email
        <Input name="email" type="email" required autoComplete="email" />
      </label>
      <label className="flex flex-col gap-1.5 text-fg2">
        Password
        <Input
          name="password"
          type="password"
          minLength={8}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          required
        />
      </label>
      {error && (
        <p role="alert" className="text-crit">
          {error}
        </p>
      )}
      {info && (
        <p role="status" className="text-fg2">
          {info}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={busy}>
        {mode === "login" ? "Sign in" : "Sign up"}
      </Button>
      {mode === "login" && (
        <Button
          type="button"
          variant="outline"
          className="w-full"
          disabled={busy}
          onClick={(e) => {
            const f = e.currentTarget.form;
            const email = f?.elements.namedItem("email") as HTMLInputElement | null;
            if (f && email?.reportValidity()) void go(f, true);
          }}
        >
          Email me a magic link
        </Button>
      )}
      <Button
        type="button"
        variant="outline"
        className="w-full"
        disabled={busy}
        onClick={async () => {
          if (!process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY) {
            return setError("Auth is not configured");
          }
          setBusy(true);
          const { error: err } = await createClient().auth.signInWithOAuth({
            provider: "github",
            options: { redirectTo: `${window.location.origin}/auth/callback` },
          });
          if (err) {
            setBusy(false);
            setError(err.message);
          }
        }}
      >
        Continue with GitHub
      </Button>
      <p className="text-fg2">
        {mode === "login" ? (
          <Link href="/register" className="hover:text-accent">
            Need an account? Register
          </Link>
        ) : (
          <Link href="/login" className="hover:text-accent">
            Have an account? Sign in
          </Link>
        )}
      </p>
    </form>
  );
}
