"use client";

import { useState, useTransition } from "react";
import { toast } from "sonner";
import { savePlan } from "@/app/actions";
import { RemediationProgress } from "@/components/charts/RemediationProgress";
import { Button } from "@/components/ui/button";
import type { RemediationPlan } from "@/lib/types";

export function SavePlan({ teamId }: { teamId: number }) {
  const [plan, setPlan] = useState<RemediationPlan | null>(null);
  const [pending, start] = useTransition();

  const save = () =>
    start(async () => {
      const r = await savePlan(teamId);
      if (!r.ok) return void toast.error(r.error);
      setPlan(r.value);
      toast.success("Plan saved");
    });

  return (
    <div className="space-y-3">
      <Button onClick={save} disabled={pending}>
        {pending ? "Saving…" : "Save plan"}
      </Button>
      {plan && <RemediationProgress key={plan.plan_id} plan={plan} />}
    </div>
  );
}
