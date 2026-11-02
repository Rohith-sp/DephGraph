"use client";

import { useState, useTransition } from "react";
import { toast } from "sonner";
import { setActionStatus } from "@/app/actions";
import { Button } from "@/components/ui/button";
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
    <div className="space-y-3">
      <div
        role="progressbar"
        aria-label="Plan progress"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-2 w-full rounded bg-slate-200"
      >
        <div className="h-2 rounded bg-green-600" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-sm text-slate-600">
        {done} of {actions.length} actions done ({pct}%)
      </p>
      <ul className="space-y-1 text-sm">
        {actions.map((a) => (
          <li key={a.action_seq} className="flex items-center justify-between gap-2">
            <span>
              {a.action_seq}. {a.description} <em className="text-slate-500">[{a.status}]</em>
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={() => mark(a.action_seq, a.status === "DONE" ? "PENDING" : "DONE")}
            >
              {a.status === "DONE" ? "Undo" : "Mark done"}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
