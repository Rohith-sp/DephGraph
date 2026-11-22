import type { NextConfig } from "next";

const origin = (url?: string) => {
  try {
    return url ? new URL(url).origin : "";
  } catch {
    return "";
  }
};

// The browser only talks to this app and to Supabase (sign-in) and Sentry (when configured); FastAPI is
// called server-side. Next's bootstrap scripts are inline and carry no nonce on statically rendered
// pages, and React/Tailwind/charts set inline styles, so script/style keep 'unsafe-inline' (plus
// 'unsafe-eval' for React's dev tooling only). Everything else is locked to this origin.
const connect = ["'self'", origin(process.env.NEXT_PUBLIC_SUPABASE_URL), origin(process.env.NEXT_PUBLIC_SENTRY_DSN)];
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === "production" ? "" : " 'unsafe-eval'"}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src ${connect.filter(Boolean).join(" ")}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

// Standalone output is for the Docker image only (set NEXT_STANDALONE=1 there); `next start` does not support it.
const nextConfig: NextConfig = {
  output: process.env.NEXT_STANDALONE ? "standalone" : undefined,
  headers: async () => [{ source: "/:path*", headers: securityHeaders }],
};

export default nextConfig;
