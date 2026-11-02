"use client";

import { Button } from "@/components/ui/button";

export default function DashboardError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div role="alert" className="space-y-3 rounded-lg border border-red-200 bg-red-50 p-6">
      <h1 className="text-lg font-semibold text-red-900">Something went wrong</h1>
      <p className="text-sm text-red-800">{error.message}</p>
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
