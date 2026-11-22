import { baseOptions, dsn } from "@/lib/sentry";

// Browser errors only (unhandled errors and rejections); no tracing, so the SDK stays small.
if (dsn) void import("@sentry/nextjs").then((Sentry) => Sentry.init({ ...baseOptions, tracesSampleRate: 0 }));
