"use server";

import { api, ApiError } from "@/lib/api";
import type { AlertSettings, AlertSeverity } from "@/lib/types";

type Result = { ok: true; value: AlertSettings } | { ok: false; error: string };

const LEVELS: AlertSeverity[] = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

export async function getAlerts(teamId: number): Promise<AlertSettings> {
  return api<AlertSettings>(`/teams/${teamId}/alerts`);
}

export async function saveAlerts(
  teamId: number,
  patch: { enabled?: boolean; min_severity?: AlertSeverity },
): Promise<Result> {
  if (!Number.isInteger(teamId) || (patch.min_severity && !LEVELS.includes(patch.min_severity))) {
    return { ok: false, error: "Invalid input" };
  }
  try {
    return { ok: true, value: await api<AlertSettings>(`/teams/${teamId}/alerts`, { method: "PATCH", body: patch }) };
  } catch (e) {
    return { ok: false, error: e instanceof ApiError ? e.message : "Request failed" };
  }
}
