"use client";

import { useRouter } from "next/navigation";
import { startTransition } from "react";
import { Button } from "@/components/ui/button";

// Production hides server error text behind a digest, so the message is fixed and the digest is the reference.
export default function DashboardError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const router = useRouter();
  // reset() alone only re-renders the client tree; the failed server render must be fetched again.
  const retry = () =>
    startTransition(() => {
      router.refresh();
      reset();
    });
  return (
    <div role="alert" className="max-w-xl space-y-4 rounded-xl border border-crit/40 bg-card p-5">
      <h1 className="text-xl">Could not load this page</h1>
      <p className="text-fg2">The API did not return what this page needs. Check that it is running, then try again.</p>
      {error.digest && <p className="font-mono text-xs text-fg3">Ref {error.digest}</p>}
      <Button onClick={retry}>Try again</Button>
    </div>
  );
}
