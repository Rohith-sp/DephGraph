"use server";

import { redirect } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { createClient } from "@/lib/supabase/server";
import type { ActionStatus, PlanAction, RemediationPlan } from "@/lib/types";

export async function signOut() {
  await (await createClient()).auth.signOut();
  redirect("/login");
}

type Result<T> = { ok: true; value: T } | { ok: false; error: string };

async function run<T>(fn: () => Promise<T>): Promise<Result<T>> {
  try {
    return { ok: true, value: await fn() };
  } catch (e) {
    return { ok: false, error: e instanceof ApiError ? e.message : "Request failed" };
  }
}

const STATUSES: ActionStatus[] = ["DONE", "SKIPPED", "PENDING"];
const bad = { ok: false, error: "Invalid input" } as const;

export async function savePlan(teamId: number): Promise<Result<RemediationPlan>> {
  if (!Number.isInteger(teamId)) return bad;
  return run(() => api<RemediationPlan>("/remediation/plan", { method: "POST", body: { team_id: teamId } }));
}

export async function setActionStatus(planId: number, seq: number, status: ActionStatus): Promise<Result<PlanAction>> {
  if (!Number.isInteger(planId) || !Number.isInteger(seq) || !STATUSES.includes(status)) return bad;
  return run(() =>
    api<PlanAction>(`/remediation/plan/${planId}/actions/${seq}`, { method: "PATCH", body: { status } }),
  );
}
