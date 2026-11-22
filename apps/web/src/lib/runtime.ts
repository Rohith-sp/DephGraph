// Edge-safe (middleware imports it): no server-only modules here.
const onVercel = () => Boolean(process.env.VERCEL || process.env.VERCEL_ENV);

/** Local-only dev bypass (compose / e2e). Throws on Vercel: it must never be reachable in a deployment. */
export function authDisabled(): boolean {
  if (process.env.AUTH_DISABLED !== "true") return false; // anything but the literal "true" keeps auth on
  if (onVercel()) {
    throw new Error("AUTH_DISABLED=true is refused on Vercel: remove it from the project's environment variables");
  }
  return true;
}

/** FastAPI base URL. The localhost fallback exists for local dev only; Vercel must set API_URL. */
export function apiBaseUrl(): string {
  const url = process.env.API_URL ?? process.env.NEXT_PUBLIC_API_URL;
  if (url) return url;
  if (onVercel()) throw new Error("API_URL is not set: configure it in the Vercel project's environment variables");
  return "http://localhost:8000";
}
