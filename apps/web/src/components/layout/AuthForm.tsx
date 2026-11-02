"use client";

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
    const redirectTo = `${window.location.origin}/`;
    const { error: err } = magic
      ? await auth.signInWithOtp({ email, options: { emailRedirectTo: redirectTo } })
      : mode === "login"
        ? await auth.signInWithPassword({ email, password })
        : await auth.signUp({ email, password, options: { emailRedirectTo: redirectTo } });
    setBusy(false);
    if (err) return setError(err.message);
    if (magic || mode === "register") return setInfo("Check your email to continue.");
    router.replace("/");
    router.refresh();
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void go(e.currentTarget, false);
      }}
      className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6">
      <h1 className="text-xl font-bold">{mode === "login" ? "Sign in" : "Create account"}</h1>
      <label className="flex flex-col gap-1 text-sm">
        Email
        <Input name="email" type="email" required autoComplete="email" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
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
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {info && (
        <p role="status" className="text-sm text-green-700">
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
      <p className="text-sm text-slate-600">
        {mode === "login" ? (
          <Link href="/register">Need an account? Register</Link>
        ) : (
          <Link href="/login">Have an account? Sign in</Link>
        )}
      </p>
    </form>
  );
}
