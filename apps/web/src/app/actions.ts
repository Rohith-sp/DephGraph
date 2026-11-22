"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { api } from "@/lib/api";
import { bad, run, type Result } from "@/lib/run";
import { createClient } from "@/lib/supabase/server";
import type { ActionStatus, CreatedToken, PlanAction, RemediationPlan } from "@/lib/types";

export async function signOut() {
  await (await createClient()).auth.signOut();
  redirect("/login");
}

const STATUSES: ActionStatus[] = ["DONE", "SKIPPED", "PENDING"];
const ROLES = ["member", "admin"];

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

// Onboarding mutations. The API validates everything; these only refuse malformed ids so a path
// can never be assembled from user text. Each refreshes the pages that list the data.
async function mutate<T>(path: string, method: "POST" | "PATCH" | "DELETE", body?: unknown): Promise<Result<T>> {
  const r = await run(() => api<T>(path, { method, body }));
  if (r.ok) revalidatePath("/", "layout");
  return r;
}

export const createTeam = async (name: string, slug: string) =>
  mutate<unknown>("/teams", "POST", { name, slug });

export async function inviteMember(teamId: number, email: string, role: string) {
  if (!Number.isInteger(teamId) || !ROLES.includes(role)) return bad;
  return mutate<unknown>(`/teams/${teamId}/invites`, "POST", { email, role });
}

export async function removeInvite(teamId: number, email: string) {
  if (!Number.isInteger(teamId)) return bad;
  return mutate<unknown>(`/teams/${teamId}/invites/${encodeURIComponent(email)}`, "DELETE");
}

const UUID = /^[0-9a-f-]{36}$/i;

export async function setMemberRole(teamId: number, userId: string, role: string) {
  if (!Number.isInteger(teamId) || !UUID.test(userId) || !ROLES.includes(role)) return bad;
  return mutate<unknown>(`/teams/${teamId}/members/${userId}`, "PATCH", { role });
}

export async function removeMember(teamId: number, userId: string) {
  if (!Number.isInteger(teamId) || !UUID.test(userId)) return bad;
  return mutate<unknown>(`/teams/${teamId}/members/${userId}`, "DELETE");
}

export async function addService(teamId: number, repoUrl: string, name: string, rootPath: string) {
  if (!Number.isInteger(teamId)) return bad;
  return mutate<unknown>("/services", "POST", {
    team_id: teamId,
    repo_url: repoUrl,
    root_path: rootPath,
    ...(name.trim() ? { name } : {}),
  });
}

export async function createToken(serviceId: number, name: string): Promise<Result<CreatedToken>> {
  if (!Number.isInteger(serviceId)) return bad;
  return mutate<CreatedToken>(`/services/${serviceId}/tokens`, "POST", { name });
}

export async function revokeToken(serviceId: number, tokenId: number) {
  if (!Number.isInteger(serviceId) || !Number.isInteger(tokenId)) return bad;
  return mutate<unknown>(`/services/${serviceId}/tokens/${tokenId}`, "DELETE");
}
