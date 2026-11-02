import { createClient } from "@/lib/supabase/server";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const authDisabled = () => process.env.AUTH_DISABLED === "true";

// shortcut: server-only. Browser code never calls FastAPI directly (the dev token is server-only
// and the Supabase token would need a second client path); mutations go through server actions.
const baseUrl = () => process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function token(): Promise<string | undefined> {
  if (authDisabled()) return process.env.DEV_API_TOKEN;
  const { data } = await (await createClient()).auth.getSession();
  return data.session?.access_token;
}

interface Envelope<T> {
  data: T | null;
  error: string | null;
}

export async function api<T>(
  path: string,
  init: { method?: "GET" | "POST" | "PATCH"; body?: unknown } = {},
): Promise<T> {
  const jwt = await token();
  let res: Response;
  try {
    res = await fetch(`${baseUrl()}${path}`, {
      method: init.method ?? "GET",
      cache: "no-store",
      headers: {
        ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
        ...(init.body ? { "Content-Type": "application/json" } : {}),
      },
      body: init.body ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new ApiError("API unreachable", 503);
  }
  const env = (await res.json().catch(() => null)) as Envelope<T> | null;
  if (!res.ok || !env || env.error !== null || env.data === null) {
    throw new ApiError(env?.error ?? `API error ${res.status}`, res.status);
  }
  return env.data;
}
