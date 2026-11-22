import type { APIRequestContext } from "@playwright/test";

const API = process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
export const TOKEN = process.env.DEV_API_TOKEN;

// Direct API calls with the dev token: the same data the pages render, fetched independently.
export async function apiData<T>(request: APIRequestContext, path: string): Promise<T> {
  const res = await request.get(`${API}${path}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  if (!res.ok()) throw new Error(`GET ${path} -> ${res.status()}`);
  return (await res.json()).data as T;
}

export const apiPost = (request: APIRequestContext, path: string, data: unknown) =>
  request.post(`${API}${path}`, { headers: { Authorization: `Bearer ${TOKEN}` }, data, timeout: 90_000 });
