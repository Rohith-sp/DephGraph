import { execFileSync } from "node:child_process";
import path from "node:path";
import { defineConfig } from "@playwright/test";
import { AUTH_DISABLED, STATE, TEAM2_USER_ID } from "./e2e/credentials";

// AUTH_DISABLED=true (+ DEV_API_TOKEN) is the CI mode; otherwise real Supabase auth.
const env = { AUTH_DISABLED: process.env.AUTH_DISABLED ?? "", DEV_API_TOKEN: process.env.DEV_API_TOKEN ?? "" };

// E2E_BASE_URL: run the suite against an already running web server (compose, a deployment) instead
// of spawning `npm run start`. The API-down (:3100) and team-2 (:3200) servers are still spawned
// locally, so a build must exist; the browser reaches the main API through that server's own API_URL.
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

// Dev token of the single-team user; only needed (and only valid) in AUTH_DISABLED mode.
const team2Token = AUTH_DISABLED
  ? execFileSync(
      process.env.E2E_PYTHON ?? "python",
      [path.resolve(__dirname, "../../scripts/make_dev_token.py"), "--user-id", TEAM2_USER_ID],
      { encoding: "utf8" },
    ).trim()
  : "";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  // One worker: specs share one database and some of them write to it.
  workers: 1,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL, storageState: AUTH_DISABLED ? undefined : STATE },
  webServer: [
    ...(process.env.E2E_BASE_URL
      ? []
      : [{ command: "npm run start", port: 3000, reuseExistingServer: !process.env.CI, env }]),
    // Same build pointed at a closed port: the "API is down" case in errors.spec.ts.
    {
      command: "npx next start -p 3100",
      port: 3100,
      reuseExistingServer: !process.env.CI,
      env: { ...env, API_URL: "http://127.0.0.1:9" },
    },
    // Same build as the user who belongs to team 2 only (tenancy.spec.ts).
    {
      command: "npx next start -p 3200",
      port: 3200,
      reuseExistingServer: !process.env.CI,
      env: { ...env, DEV_API_TOKEN: team2Token },
    },
  ],
});
