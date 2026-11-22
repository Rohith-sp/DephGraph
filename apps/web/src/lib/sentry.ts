import type { ErrorEvent } from "@sentry/nextjs";

// Error reporting is opt-in: with no DSN configured nothing below loads or sends anything. The SDK is
// imported on demand, so deployments without a DSN never ship it to the browser.
// Server: SENTRY_DSN (or the public one); browser: NEXT_PUBLIC_SENTRY_DSN, inlined at build time.
export const dsn = process.env.SENTRY_DSN ?? process.env.NEXT_PUBLIC_SENTRY_DSN;

const SECRET_HEADERS = ["authorization", "cookie", "x-hub-signature-256", "x-cron-secret"];

/** Strip credentials, cookies and request bodies (webhook payloads) before an event leaves the process. */
export function scrub(event: ErrorEvent): ErrorEvent {
  if (event.request) {
    delete event.request.data;
    delete event.request.cookies;
    event.request.headers = Object.fromEntries(
      Object.entries(event.request.headers ?? {}).filter(([k]) => !SECRET_HEADERS.includes(k.toLowerCase())),
    );
  }
  return event;
}

export const baseOptions = { dsn, sendDefaultPii: false, beforeSend: scrub };

export async function report(error: unknown, extra?: Record<string, unknown>) {
  if (dsn) (await import("@sentry/nextjs")).captureException(error, { extra });
}
