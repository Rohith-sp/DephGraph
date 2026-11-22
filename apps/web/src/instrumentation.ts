import type { Instrumentation } from "next";
import { baseOptions, dsn } from "@/lib/sentry";

export async function register() {
  if (!dsn || process.env.NEXT_RUNTIME !== "nodejs") return;
  const Sentry = await import("@sentry/nextjs");
  Sentry.init({ ...baseOptions, tracesSampleRate: Number(process.env.SENTRY_TRACES_SAMPLE_RATE ?? 0.1) });
}

// Server render / route handler errors (Next hides their text from the browser, so this is where they are seen).
export const onRequestError: Instrumentation.onRequestError = async (...args) => {
  if (dsn) (await import("@sentry/nextjs")).captureRequestError(...args);
};
