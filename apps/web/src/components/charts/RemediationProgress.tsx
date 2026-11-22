"use client";

import { useState, useTransition } from "react";
import { toast } from "sonner";
import { setActionStatus } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ActionStatus, RemediationPlan } from "@/lib/types";

export function RemediationProgress({ plan }: { plan: RemediationPlan }) {
  const [actions, setActions] = useState(plan.actions);
  const [pending, start] = useTransition();
  const done = actions.filter((a) => a.status === "DONE").length;
  const pct = actions.length ? Math.round((done / actions.length) * 100) : 0;

  const mark = (seq: number, status: ActionStatus) =>
    start(async () => {
      const r = await setActionStatus(plan.plan_id, seq, status);
      if (!r.ok) return void toast.error(r.error);
      setActions((prev) => prev.map((a) => (a.action_seq === seq ? { ...a, status: r.value.status } : a)));
    });

  return (
    <section aria-label="Saved plan" className="space-y-4 rounded-xl border border-line bg-card p-5">
      <div
        role="progressbar"
        aria-label="Plan progress"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-1 w-full rounded-full bg-raised"
      >
        <div className="h-1 rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <p className="tabular-nums text-fg2">
        {done} of {actions.length} actions done ({pct}%)
      </p>
      <ul className="divide-y divide-line">
        {actions.map((a) => (
          <li key={a.action_seq} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
            <span className="min-w-0">
              <span className="mr-2 tabular-nums text-fg3">{a.action_seq}.</span>
              <span className={cn(a.status === "DONE" && "text-fg3 line-through")}>{a.description ?? "Untitled action"}</span>
              <span className="ml-2 font-mono text-xs text-fg3">{a.status}</span>
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={pending}
              aria-label={`${a.status === "DONE" ? "Undo" : "Mark done"} action ${a.action_seq}`}
              onClick={() => mark(a.action_seq, a.status === "DONE" ? "PENDING" : "DONE")}
            >
              {a.status === "DONE" ? "Undo" : "Mark done"}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
