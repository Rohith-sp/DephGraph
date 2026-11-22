import { ApiError } from "@/lib/api";

// Shared by the server actions (a "use server" file may only export async actions).
export type Result<T> = { ok: true; value: T } | { ok: false; error: string };

export const bad = { ok: false, error: "Invalid input" } as const;

export async function run<T>(fn: () => Promise<T>): Promise<Result<T>> {
  try {
    return { ok: true, value: await fn() };
  } catch (e) {
    return { ok: false, error: e instanceof ApiError ? e.message : "Request failed" };
  }
}
